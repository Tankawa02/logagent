"""按文件名在源码目录里找文件：跨请求复用，只记被问过的文件名。

- 只为查过的文件名保存命中路径（每个源码目录最多 MAX_NAMES 个，LRU 淘汰），
  其他文件什么都不留，内存不随仓库文件总数增长；
- 另外记下每个目录的 mtime：目录里增删、改名文件都会改它。之后的请求只 stat 一遍目录
  （不列文件），变了的目录才重新列，所以新出现的同名文件下一个请求就能看到，
  不需要每次都重新遍历整个仓库；
- 只有第一次查某个文件名时才完整遍历一次。
"""

from __future__ import annotations

import os
import threading
import time
from collections import OrderedDict
from pathlib import Path

from .tools import SKIP_DIRS

MAX_ROOTS = 16
MAX_NAMES = 4096
# mtime 精度有限：扫描时刚改过的目录记成「待确认」，下次一定重新列，避免同一时刻的改动被漏掉
_RACY_WINDOW_NS = 2_000_000_000
_DIRTY = -1


def _skip(name: str) -> bool:
    return name in SKIP_DIRS or name.startswith(".")


class _RootIndex:
    def __init__(self, root: Path) -> None:
        self.root = str(root)
        self.lock = threading.Lock()
        self.dirs: dict[str, int] = {}
        self.children: dict[str, set[str]] = {}
        self.names: OrderedDict[str, set[str]] = OrderedDict()
        # 目录 → 在这个目录里命中的文件名，用来在目录变化时撤掉旧命中
        self.hits: dict[str, set[str]] = {}

    def _scan(self, directory: str) -> list[str] | None:
        """列一个目录，更新所有已缓存文件名在这个目录里的命中，返回子目录。"""
        scanned = time.time_ns()
        try:
            mtime = os.stat(directory).st_mtime_ns
            with os.scandir(directory) as it:
                entries = list(it)
        except OSError:
            return None
        subdirs: list[str] = []
        files: set[str] = set()
        for entry in entries:
            try:
                if entry.is_dir(follow_symlinks=False):
                    if not _skip(entry.name):
                        subdirs.append(entry.path)
                elif entry.is_file():
                    files.add(entry.name)
            except OSError:
                continue
        self.dirs[directory] = _DIRTY if scanned - mtime < _RACY_WINDOW_NS else mtime

        old = self.hits.get(directory, set())
        new = files & self.names.keys()
        for name in old - new:
            self.names[name].discard(os.path.join(directory, name))
        for name in new - old:
            self.names[name].add(os.path.join(directory, name))
        if new:
            self.hits[directory] = new
        else:
            self.hits.pop(directory, None)

        current = set(subdirs)
        for gone in self.children.get(directory, set()) - current:
            self._forget(gone)
        self.children[directory] = current
        return subdirs

    def _walk(self, top: str, *, full: bool) -> None:
        """full=True 遍历整棵子树；否则只列 top，再往下走新出现的子目录。"""
        stack = [top]
        while stack:
            directory = stack.pop()
            subdirs = self._scan(directory)
            if subdirs is None:
                self._forget(directory)
                continue
            stack.extend(s for s in subdirs if full or s not in self.dirs)

    def _forget(self, directory: str) -> None:
        prefix = directory + os.sep
        for known in [d for d in self.dirs if d == directory or d.startswith(prefix)]:
            del self.dirs[known]
            self.children.pop(known, None)
            for name in self.hits.pop(known, ()):
                self.names[name].discard(os.path.join(known, name))

    def refresh(self) -> None:
        """只 stat 已知目录：消失的去掉，变了的重新列。"""
        for directory, mtime in list(self.dirs.items()):
            if directory not in self.dirs:
                continue
            try:
                current = os.stat(directory).st_mtime_ns
            except OSError:
                self._forget(directory)
                continue
            if mtime == _DIRTY or current != mtime:
                self._walk(directory, full=False)

    def find(self, name: str) -> list[str]:
        if name in self.names:
            self.names.move_to_end(name)
        else:
            self.names[name] = set()
            self._walk(self.root, full=True)
            while len(self.names) > MAX_NAMES:
                evicted, paths = self.names.popitem(last=False)
                for path in paths:
                    hit = self.hits.get(os.path.dirname(path))
                    if hit is not None:
                        hit.discard(evicted)
        return sorted(self.names[name])


_ROOTS: OrderedDict[Path, _RootIndex] = OrderedDict()
_ROOTS_LOCK = threading.Lock()


def _root_index(root: Path) -> _RootIndex:
    with _ROOTS_LOCK:
        index = _ROOTS.get(root)
        if index is None:
            index = _ROOTS[root] = _RootIndex(root)
            while len(_ROOTS) > MAX_ROOTS:
                _ROOTS.popitem(last=False)
        else:
            _ROOTS.move_to_end(root)
        return index


class FileNameLookup:
    """一个请求内使用：同一个源码目录在这个请求里只检查一次变化。"""

    def __init__(self) -> None:
        self._checked: set[Path] = set()

    def find(self, root: Path, name: str) -> list[Path]:
        index = _root_index(root)
        with index.lock:
            if root not in self._checked:
                index.refresh()
                self._checked.add(root)
            return [Path(p) for p in index.find(name)]


def clear_cache() -> None:
    with _ROOTS_LOCK:
        _ROOTS.clear()

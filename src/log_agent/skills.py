"""Skills：团队预先写好的排查手册（`<skill 名>/SKILL.md`），由 deepagents 的 SkillsMiddleware 按需加载。

系统提示词里只放每个 skill 的名字和一句描述，模型判断用得上时再用 `read_file` 读全文（progressive disclosure），
所以装再多 skill 也不会撑大每次请求的上下文。

查找顺序（后者同名覆盖前者）：
1. 用户级 `~/.log-agent/skills/`
2. 项目级 `.log-agent/skills/`：从当前目录逐级向上找，找到最近的一个
3. 命令行 `--skills` / 配置文件 `skills = [...]` 显式指定的目录

每个来源挂到虚拟路径 `/skills/<来源>/` 下，由只以该目录为根的 FilesystemBackend（virtual_mode）支撑：
模型只能读到 skill 目录内的文件，也不依赖进程 cwd，Windows 上 skill 与日志、源码在不同盘符也没问题。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

SKILL_FILE = "SKILL.md"
SKILLS_ROUTE = "/skills/"

SKILLS_PROMPT = """## Skills（排查手册）

下面是团队预先编写的排查手册（skill）。每个 skill 针对某个系统或某类问题，
记录了关键日志字段、常见根因、需要重点查看的代码位置和排查步骤。

{skills_locations}{skills_load_warnings}

**可用的 skill：**

{skills_list}

**用法（按需加载）：**
1. 用户问题与某个 skill 的描述相符时，先用 `read_file` 读取它的 SKILL.md 全文，传 `limit=1000`（默认 100 行通常不够）。
2. 按手册里的步骤和要点排查；手册引用的同目录参考文件，也用 `read_file` 按上面列出的路径读取。
3. 手册只是经验参考：结论必须以日志和源码里查到的证据为准；手册与证据冲突时以证据为准，并在报告里说明。
4. 手册里提到执行脚本、修改文件的步骤直接跳过——你的工具都是只读的。

没有相符的 skill 时照常排查，不要为了用 skill 而用。"""


@dataclass(frozen=True)
class SkillSource:
    route: str
    label: str
    directory: Path

    def skill_count(self) -> int:
        try:
            return sum(1 for child in self.directory.iterdir() if (child / SKILL_FILE).is_file())
        except OSError:
            return 0


def user_skills_dir() -> Path:
    return Path.home() / ".log-agent" / "skills"


def find_project_skills_dir(start: Path | None = None) -> Path | None:
    current = (start or Path.cwd()).resolve()
    for folder in (current, *current.parents):
        candidate = folder / ".log-agent" / "skills"
        if candidate.is_dir():
            return candidate
    return None


def resolve_skill_sources(extra: Sequence[str | Path] = (), cwd: Path | None = None) -> list[SkillSource]:
    """按优先级从低到高返回存在的 skill 目录；同一目录只保留优先级最高的一次。"""
    candidates: list[tuple[str, str, Path]] = [("user", "用户级", user_skills_dir())]
    project = find_project_skills_dir(cwd)
    if project is not None:
        candidates.append(("project", "项目级", project))
    for index, item in enumerate(extra, start=1):
        path = Path(item).expanduser()
        candidates.append((f"extra-{index}", f"自定义（{path.name or path}）", path))

    by_dir: dict[Path, tuple[str, str]] = {}
    for key, label, path in candidates:
        if not path.is_dir():
            continue
        resolved = path.resolve()
        by_dir.pop(resolved, None)
        by_dir[resolved] = (key, label)
    return [SkillSource(route=f"{SKILLS_ROUTE}{key}/", label=label, directory=d) for d, (key, label) in by_dir.items()]


def build_skills(sources: Sequence[SkillSource]):
    """返回 (backend, SkillsMiddleware)；没有任何 skill 来源时返回 (None, None)，主代理提示词保持不变。"""
    if not sources:
        return None, None

    from deepagents.backends import CompositeBackend, FilesystemBackend, StateBackend
    from deepagents.middleware import SkillsMiddleware

    backend = CompositeBackend(
        default=StateBackend(),
        routes={s.route: FilesystemBackend(root_dir=s.directory, virtual_mode=True) for s in sources},
    )
    middleware = SkillsMiddleware(
        backend=backend,
        sources=[(s.route, s.label) for s in sources],
        system_prompt=SKILLS_PROMPT,
    )
    return backend, middleware

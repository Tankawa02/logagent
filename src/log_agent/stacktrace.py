"""异常链解析与聚类：按“根因异常 + 首个业务栈帧”把同一个问题归到一起。

只看报错的第一行做聚类时，同一个根因会因为外层包装不同被拆成好几类（`ServiceException`
包着 `SQLTimeoutException`、`ResponseStatusException` 包着同一个 `SQLTimeoutException`），
不同根因也可能因为外层文案相同被合成一类。这里把整段堆栈解析成异常链：

- Java / Kotlin / Scala：`Caused by:` 逐层深入，最后一个是根因；`Suppressed:` 忽略；`... N more`
- Python：多段 traceback 由 “During handling of the above exception” /
  “The above exception was the direct cause” 连接，**第一段**是根因
- .NET：`A: m ---> B: m2`，`--->` 之后的是内层，最内层是根因
- Node.js：`TypeError: ...` + `    at fn (file:line:col)`；`[cause]:` 视为更深一层
- Go：`panic: ...` + `goroutine N [running]:` 后的函数 / 文件行

栈帧统一按“离抛出点最近的在前”排列；业务栈帧是第一个不属于标准库 / 常见框架的帧，
可以在配置文件里用 `app_packages = ["com.acme"]` 指定业务包前缀，优先级最高。
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from .timefilter import find_timestamp

MAX_BLOCK_LINES = 400

_app_prefixes: tuple[str, ...] = ()


def set_app_packages(prefixes: Iterable[str] | None) -> None:
    global _app_prefixes
    _app_prefixes = tuple(p.strip() for p in (prefixes or []) if p and p.strip())


def app_packages() -> tuple[str, ...]:
    return _app_prefixes


@dataclass(frozen=True)
class Frame:
    func: str
    file: str = ""
    line: int | None = None

    def describe(self) -> str:
        where = f"{self.file}:{self.line}" if self.file and self.line else self.file
        return f"{self.func} ({where})" if where and self.func else (self.func or where)

    @property
    def location_key(self) -> str:
        """聚类用：函数 + 文件，不含行号（每次发布行号都会变）。"""
        return f"{self.func}@{self.file.rsplit('/', 1)[-1]}"


@dataclass
class Link:
    type: str
    message: str = ""
    frames: list[Frame] = field(default_factory=list)


@dataclass
class Chain:
    language: str
    links: list[Link]  # 最外层在前，根因在最后

    @property
    def root(self) -> Link:
        return self.links[-1]

    @property
    def wrappers(self) -> list[str]:
        return [link.type for link in self.links[:-1]]

    @property
    def app_frame(self) -> Frame | None:
        """根因的第一个业务栈帧；根因的帧被 `... N more` 省略时退回外层。"""
        for link in reversed(self.links):
            frame = next((f for f in link.frames if is_app_frame(f, self.language)), None)
            if frame:
                return frame
        for link in reversed(self.links):
            if link.frames:
                return link.frames[0]
        return None


# ---------------------------------------------------------------------------
# 业务栈帧判断
# ---------------------------------------------------------------------------

_LIB_PREFIXES = {
    "java": (
        "java.", "javax.", "jdk.", "sun.", "com.sun.", "kotlin.", "kotlinx.", "scala.", "org.springframework.",
        "org.apache.", "org.hibernate.", "io.netty.", "reactor.", "com.fasterxml.", "org.junit.", "okhttp3.",
        "feign.", "io.grpc.", "com.zaxxer.", "org.mybatis.", "com.mysql.", "org.postgresql.", "ch.qos.",
        "org.slf4j.", "com.google.", "io.micrometer.", "org.eclipse.", "net.bytebuddy.", "io.undertow.",
        "com.alibaba.druid.", "com.baomidou.", "io.lettuce.", "redis.clients.", "org.aspectj.", "io.opentelemetry.",
    ),
    "dotnet": ("System.", "Microsoft.", "lambda_method", "Npgsql.", "Newtonsoft.", "Grpc."),
    "go": ("runtime.", "runtime/", "reflect.", "testing.", "net/http.", "sync.", "panic(", "created by "),
    "node": (),
    "python": (),
}
_LIB_FILE_MARKERS = (
    "site-packages", "dist-packages", "/lib/python", "\\lib\\python", "<frozen", "node_modules", "node:internal",
    "internal/", "<anonymous>", "/pkg/mod/", "/usr/local/go/", "/usr/lib/go", "\\Lib\\",
)


def is_app_frame(frame: Frame, language: str) -> bool:
    haystack = f"{frame.func} {frame.file}"
    if _app_prefixes:
        return any(frame.func.startswith(p) or p in frame.file for p in _app_prefixes)
    if any(marker in frame.file for marker in _LIB_FILE_MARKERS):
        return False
    if "$$" in frame.func or frame.func.startswith(("$Proxy", "jdk.proxy", "<")):
        return False  # CGLIB / 动态代理
    if frame.file in {"Native Method", "Unknown Source"} and not frame.line:
        return False
    return not any(haystack.startswith(p) for p in _LIB_PREFIXES.get(language, ()))


# ---------------------------------------------------------------------------
# 块识别：哪一行开始一段堆栈
# ---------------------------------------------------------------------------

_JAVA_TYPE = r"(?:[a-zA-Z_$][\w$]*\.)*[A-Z][\w$]*(?:Exception|Error|Throwable|Fault|Failure)"
_QUALIFIED = r"(?:[a-z_$][\w$]*\.)+[A-Z][\w$]*"
_HEADER = re.compile(
    rf"^(?:Exception in thread \"[^\"]*\"\s+|Uncaught\s+)?(?P<type>{_JAVA_TYPE}|{_QUALIFIED})(?:\s*:\s*(?P<msg>.*))?$"
)
_CAUSED = re.compile(rf"^(?P<indent>\s*)(?P<kind>Caused by|Suppressed):\s*(?P<type>{_JAVA_TYPE}|{_QUALIFIED}|[\w.$]+)"
                     r"(?:\s*:\s*(?P<msg>.*))?$")
_JAVA_FRAME = re.compile(r"^\s*at\s+(?P<func>[^\s(]+)\((?P<loc>[^)]*)\)\s*(?:~?\[.*\])?\s*$")
_JAVA_LOC = re.compile(r"^(?P<file>[^:]+\.(?:java|kt|scala|groovy|kts))(?::(?P<line>\d+))?$")
_DOTNET_FRAME = re.compile(r"^\s*at\s+(?P<func>[^(]+)\([^)]*\)(?:\s+in\s+(?P<file>.+?):line\s+(?P<line>\d+))?\s*$")
_NODE_FRAME = re.compile(
    r"^\s*at\s+(?:(?:async\s+)?(?P<func>[^\s(][^(]*?)\s+\()?(?P<file>[^\s()]+?):(?P<line>\d+):\d+\)?\s*$"
)
_NODE_HEADER = re.compile(r"^(?:Uncaught\s+)?(?P<type>(?:[A-Z]\w*)?(?:Error|Exception))(?:\s+\[[\w_]+\])?:\s*(?P<msg>.*)$")
_NODE_CAUSE = re.compile(r"^\s*\[cause\]:\s*(?P<type>[A-Z]\w*(?:Error|Exception)?)(?::\s*(?P<msg>.*))?")
_PY_START = "Traceback (most recent call last):"
_PY_FRAME = re.compile(r'^\s+File "(?P<file>[^"]+)", line (?P<line>\d+)(?:, in (?P<func>.+))?$')
_PY_LINKS = ("During handling of the above exception", "The above exception was the direct cause")
_PY_FINAL = re.compile(r"^(?P<type>[A-Za-z_][\w.]*)(?::\s*(?P<msg>.*))?$")
_GO_PANIC = re.compile(r"^(?:panic|fatal error): (?P<msg>.*)$")
_GO_FUNC = re.compile(r"^(?P<func>[\w./*()\[\]{}\-~$]+?)(?:\(.*\))?$")
_GO_FILE = re.compile(r"^\s+(?P<file>\S+\.go):(?P<line>\d+)(?:\s+\+0x[0-9a-f]+)?$")
_DOTNET_END = "--- End of inner exception stack trace ---"
# 首行（带时间和级别的日志行）末尾附带的异常，如 `... - 下单失败 java.lang.IllegalStateException: boom`
_TRAILING = re.compile(rf"(?:^|[\s:])(?P<type>{_JAVA_TYPE})(?::\s*(?P<msg>.*))?$")
# 日志行末尾的 Node.js 异常头：`... ERROR Error: boom`、`... ERROR TypeError [ERR_X]: msg`
# _JAVA_TYPE 要求类型名在 Error 前还有字符，匹配不了最常见的裸 `Error:`
_NODE_TRAILING = re.compile(r"(?:^|[\s\]|])(?P<type>(?:[A-Z]\w*)?Error)(?:\s+\[[\w_]+\])?:\s+(?P<msg>.*)$")


def starts_block(body: str) -> bool:
    """没有级别的行能否作为一段堆栈的开头（例如直接打到 stderr 的 Traceback / panic）。"""
    text = body.strip()
    return (text.startswith(_PY_START) or bool(_GO_PANIC.match(text)) or text.startswith("Exception in thread ")
            or bool(_HEADER.match(text) and re.search(r"(?:Exception|Error)\b", text.split(":", 1)[0])))


def is_frame_line(text: str) -> bool:
    return bool(_JAVA_FRAME.match(text) or _PY_FRAME.match(text) or _NODE_FRAME.match(text)
                or _DOTNET_FRAME.match(text) or _GO_FILE.match(text))


# ---------------------------------------------------------------------------
# 解析
# ---------------------------------------------------------------------------


def _clean(msg: str | None) -> str:
    return (msg or "").strip()


def _parse_python(lines: Sequence[str]) -> Chain | None:
    segments: list[Link] = []
    frames: list[Frame] = []
    in_tb = False
    for raw in lines:
        text = raw.rstrip()
        stripped = text.strip()
        if stripped.endswith(_PY_START):  # 日志前缀和 Traceback 头可能在同一行
            in_tb, frames = True, []
            continue
        if stripped.startswith(_PY_LINKS):
            in_tb = False
            continue
        if not in_tb:
            continue
        frame = _PY_FRAME.match(text)
        if frame:
            frames.append(Frame((frame["func"] or "").strip(), frame["file"], int(frame["line"])))
            continue
        if text.startswith((" ", "\t")) or not stripped:
            continue  # 源码行 / ^^^^ 标记
        final = _PY_FINAL.match(stripped)
        if final and final["type"][0].isalpha():
            segments.append(Link(final["type"], _clean(final["msg"]), list(reversed(frames))))
            in_tb = False
    if not segments:
        return None
    # 先打印的是更早（更深）的异常：根因在第一段，链表按“外层在前”倒过来
    return Chain("python", list(reversed(segments)))


def _parse_go(lines: Sequence[str]) -> Chain | None:
    link: Link | None = None
    pending_func = ""
    for raw in lines:
        text = raw.rstrip()
        if link is None:
            panic = _GO_PANIC.search(text)
            if panic:
                msg = _clean(panic["msg"])
                kind = "runtime error" if msg.startswith("runtime error:") else "panic"
                link = Link(kind, msg.removeprefix("runtime error:").strip() if kind == "runtime error" else msg)
            continue
        file_match = _GO_FILE.match(text)
        if file_match and pending_func:
            func = pending_func
            if not func.startswith(("runtime.", "panic(")):
                link.frames.append(Frame(func, file_match["file"], int(file_match["line"])))
            pending_func = ""
            continue
        if text.startswith("goroutine ") or not text.strip() or text.startswith("["):
            continue
        stripped = text.strip()
        if not text.startswith(("\t", " ")) and _GO_FUNC.match(stripped):
            # `pkg.(*T).Method(0x0, {...})`：参数里没有圆括号，最后一个 "(" 之前就是函数名
            pending_func = stripped[:stripped.rfind("(")] if stripped.endswith(")") else stripped
    return Chain("go", [link]) if link else None


def _link_from_header(text: str) -> Link | None:
    header = _HEADER.match(text.strip()) or _NODE_HEADER.match(text.strip())
    if header:
        return Link(header["type"], _clean(header["msg"]))
    trailing = _TRAILING.search(text) or _NODE_TRAILING.search(text)
    return Link(trailing["type"], _clean(trailing["msg"])) if trailing else None


def _has_node_frame(lines: Sequence[str]) -> bool:
    """允许无源码位置的栈条目，但不能越过另一条真正的异常头。"""
    for line in lines:
        if _NODE_FRAME.match(line):
            return True
        if _HEADER.match(line.strip()) or _NODE_HEADER.match(line.strip()):
            return False
    return False


def _parse_jvm_like(lines: Sequence[str]) -> Chain | None:
    """Java / Kotlin / .NET / Node.js：一行异常头 + 若干 `at` 栈帧。"""
    links: list[Link] = []
    language = ""
    skipping = False  # Suppressed: 段落
    dotnet_segment = 0
    dotnet_links: list[Link] = []
    for index, raw in enumerate(lines):
        text = raw.rstrip()
        stripped = text.strip()
        if not stripped:
            continue
        caused = _CAUSED.match(text)
        if caused:
            if caused["kind"] == "Suppressed":
                skipping = True
            elif skipping and caused["indent"]:
                pass  # Suppressed 内部的 Caused by
            else:
                skipping = False
                links.append(Link(caused["type"], _clean(caused["msg"])))
            continue
        if skipping:
            continue
        if stripped == _DOTNET_END:
            dotnet_segment += 1
            continue
        node_cause = _NODE_CAUSE.match(text)
        if node_cause:
            links.append(Link(node_cause["type"], _clean(node_cause["msg"])))
            language = "node"
            continue
        java = _JAVA_FRAME.match(text)
        loc = _JAVA_LOC.match(java["loc"]) if java else None
        if java and (loc or java["loc"] in {"Native Method", "Unknown Source"} or java["loc"].startswith("Unknown")):
            language = language or "java"
            if links:
                links[-1].frames.append(Frame(java["func"], loc["file"] if loc else java["loc"],
                                              int(loc["line"]) if loc and loc["line"] else None))
            continue
        dotnet = _DOTNET_FRAME.match(text)
        if dotnet and (dotnet["file"] or "." in dotnet["func"]) and not _NODE_FRAME.match(text):
            language = language or "dotnet"
            frame = Frame(dotnet["func"].strip(), dotnet["file"] or "", int(dotnet["line"]) if dotnet["line"] else None)
            if dotnet_links:
                target = dotnet_links[-1 - dotnet_segment] if dotnet_segment < len(dotnet_links) else None
            else:  # 没有 ---> 的单个 .NET 异常（Serilog @x 最常见）
                target = links[-1] if links else None
            if target is not None:
                target.frames.append(frame)
            continue
        node = _NODE_FRAME.match(text)
        if node:
            language = "node"
            if links:
                links[-1].frames.append(Frame((node["func"] or "<anonymous>").strip(), node["file"], int(node["line"])))
            continue
        if stripped.startswith("... ") and stripped.endswith(("more", "common frames omitted")):
            continue
        if not links:
            # 第一条异常头：可能单独成行，也可能挂在日志首行末尾；.NET 用 ---> 串起内层异常
            if "--->" in text:
                parts = [p.strip() for p in text.split("--->")]
                parsed = [_link_from_header(parts[0])] + [_link_from_header(p) for p in parts[1:]]
                dotnet_links = [p for p in parsed if p]
                if dotnet_links:
                    links.extend(dotnet_links)
                    language = "dotnet"
                continue
            # 第一行通常是带时间戳的日志行，只认行尾的异常头；是否真是异常由后面有没有堆栈帧决定
            first_ok = index > 0 or _TRAILING.search(text) or (
                _NODE_TRAILING.search(text) and _has_node_frame(lines[index + 1:])
            )
            link = _link_from_header(text) if first_ok else None
            if link:
                links.append(link)
    if not links:
        return None
    if language == "dotnet" or not language:
        language = language or ("java" if "." in links[0].type else "node")
    return Chain(language, links)


def parse_chain(lines: Sequence[str]) -> Chain | None:
    """把一段日志（首行 + 续行）解析成异常链；认不出返回 None。"""
    lines = list(lines[:MAX_BLOCK_LINES])
    if not lines:
        return None
    if any(line.rstrip().endswith(_PY_START) for line in lines):
        chain = _parse_python(lines)
        if chain:
            return chain
    if any(_GO_PANIC.search(line) for line in lines[:3]) and any(_GO_FILE.match(line) for line in lines):
        chain = _parse_go(lines)
        if chain:
            return chain
    chain = _parse_jvm_like(lines)
    # 只有一行、没有任何栈帧的“异常头”多半是普通报错文案里提到了异常名，不算异常链
    if chain and (any(link.frames for link in chain.links) or len(chain.links) > 1):
        return chain
    return None


# ---------------------------------------------------------------------------
# 聚类
# ---------------------------------------------------------------------------

_NORMALIZE = [
    (re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"), "<uuid>"),
    (re.compile(r"\b0x[0-9a-fA-F]+\b|\b[0-9a-fA-F]{12,}\b"), "<hex>"),
    (re.compile(r"'[^']{1,80}'|\"[^\"]{1,80}\""), "<str>"),
    (re.compile(r"\d+"), "#"),
]


def normalize_message(message: str) -> str:
    text = message[:300]
    for pattern, repl in _NORMALIZE:
        text = pattern.sub(repl, text)
    return " ".join(text.split())[:100]


def chain_key(chain: Chain) -> tuple[str, str, str]:
    frame = chain.app_frame
    return chain.root.type, normalize_message(chain.root.message), frame.location_key if frame else ""


@dataclass
class ChainCluster:
    key: tuple[str, str, str]
    count: int
    first_line: int
    example: Chain
    lines: list[int] = field(default_factory=list)

    def describe_root(self) -> str:
        root = self.example.root
        return f"{root.type}: {root.message}" if root.message else root.type


class ChainCollector:
    """边扫描边收集：一段堆栈从“首行”开始，吞掉后面没有时间戳、没有级别的续行。"""

    def __init__(self) -> None:
        self.clusters: dict[tuple[str, str, str], ChainCluster] = {}
        self._block: list[str] = []
        self._start = 0

        self.extended = False  # 最近一次调用是续行（extend）还是新块 / 结束
        # Python traceback 进行到哪一步：None / "tb"（堆栈帧）/ "final"（刚读完 Type: msg）/ "link"
        self._py: str | None = None

    @property
    def open(self) -> bool:
        return bool(self._block)

    def begin(self, lineno: int, line: str) -> None:
        self.flush()
        self._block, self._start = [line], lineno
        self.extended = False
        self._py = "tb" if line.rstrip().endswith(_PY_START) else None

    def extend(self, line: str) -> bool:
        if not self._block or len(self._block) >= MAX_BLOCK_LINES:
            self.extended = False
            return False
        self._block.append(line)
        self.extended = True
        self._py = self._next_py_state(line)
        return True

    def _next_py_state(self, line: str) -> str | None:
        stripped = line.strip()
        if stripped.endswith(_PY_START):
            return "tb"
        if self._py is None or not stripped:
            return self._py
        if self._py == "tb":
            return "tb" if line[:1] in " \t" else "final"
        if stripped.startswith(_PY_LINKS):
            return "link"
        return None

    def continues_traceback(self, line: str) -> bool:
        """Python traceback 还没结束时，这一行是否属于它。

        源码行（`    logger.error(x)`）和最后的 `ValueError: parse error` 都可能含级别词或时钟，
        不能按普通日志行的规则切断。
        """
        if not self._block or self._py is None:
            return False
        stripped = line.strip()
        if self._py == "tb":
            if not _PY_FRAME.match(line):
                found = find_timestamp(stripped)
                # 时间出现在源码字符串内部时仍是续行；行首的时间戳则开始新记录。
                if found and stripped.startswith((found[0], "[" + found[0])):
                    return False
            return True if line[:1] in " \t" or not stripped else bool(_PY_FINAL.match(stripped))
        if not stripped:
            return True
        if self._py == "final":
            return stripped.startswith(_PY_LINKS)
        return stripped.endswith(_PY_START)  # "link"

    def add_lines(self, lineno: int, lines: Sequence[str]) -> None:
        """一次性给出一整段（例如 JSON 日志的 stack_trace 字段）。"""
        self.flush()
        self._record(lineno, parse_chain(lines))

    def flush(self) -> None:
        if self._block:
            self._record(self._start, parse_chain(self._block))
        self._block = []
        self.extended = False
        self._py = None

    def _record(self, lineno: int, chain: Chain | None) -> None:
        if chain is None:
            return
        key = chain_key(chain)
        cluster = self.clusters.get(key)
        if cluster is None:
            self.clusters[key] = ChainCluster(key, 1, lineno, chain, [lineno])
        else:
            cluster.count += 1
            if len(cluster.lines) < 5:
                cluster.lines.append(lineno)

    def top(self, limit: int = 8) -> list[ChainCluster]:
        return sorted(self.clusters.values(), key=lambda c: (-c.count, c.first_line))[:limit]


# JSON 日志里常见的堆栈字段
STACK_FIELDS = ("stack_trace", "stacktrace", "stack", "exception", "exc_info", "exc_text", "error.stack_trace",
                "error.stack", "err.stack", "throwable", "trace", "@x")  # @x：Serilog CLEF 的异常


def stack_from_record(record: dict) -> list[str] | None:
    for key in STACK_FIELDS:
        value: object = record
        for part in key.split("."):
            value = value.get(part) if isinstance(value, dict) else None
        if isinstance(value, dict):  # {"error": {"stack": "..."}} 或 {"exception": {"stacktrace": ...}}
            value = next((value[k] for k in ("stack_trace", "stacktrace", "stack") if isinstance(value.get(k), str)),
                         None)
        if isinstance(value, str) and "\n" in value:
            return value.splitlines()
    return None

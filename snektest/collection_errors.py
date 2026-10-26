"""Bounded, user-first console diagnostics, independent of JSON snapshots."""

lazy import linecache
lazy import sysconfig
lazy from dataclasses import dataclass, field
lazy from pathlib import Path
lazy from types import TracebackType

lazy from rich.cells import cell_len

lazy from snektest.models import CollectionError

_PACKAGE_PATH = Path(__file__).resolve().parent
_LIBRARY_PATHS = tuple(
    Path(path).resolve()
    for name in ("stdlib", "platstdlib", "purelib", "platlib")
    if (path := sysconfig.get_path(name))
)
_MAX_NODES = 50
_MAX_DEPTH = 10
_MAX_GROUP_MEMBERS = 10
_MAX_FRAMES = 200
_MAX_USER_FRAMES = 6
_MAX_NOTES = 10
_TRUNCATED = "[Collection diagnostics truncated to bounded display limits.]"


def safe_collection_message(exception: BaseException) -> str:
    """Keep original message text or a safe failure type, without masking its error."""
    try:
        return str(exception)
    except BaseException as failure:
        return f"<str failed: {type(failure).__name__}>"


@dataclass
class _Report:
    full: bool
    lines: list[str] = field(default_factory=list)
    size: int = 0
    truncated: bool = False
    seen: set[int] = field(default_factory=set)

    @property
    def remaining(self) -> int:
        return (60_000 if self.full else 16_000) - self.size

    def line(self, text: str) -> None:
        field_limit = 4_000 if self.full else 2_000
        available = min(field_limit, max(0, self.remaining))
        if len(text) > available:
            text = text[:available] + "..."
            self.truncated = True
        if available:
            self.lines.append(text)
            self.size += len(text) + 1

    def finish(self) -> str:
        if self.truncated:
            self.lines.append(_TRUNCATED)
        if not self.full:
            self.lines.append(
                "Rerun with --full-traceback for framework traceback details."
            )
        return "\n".join(self.lines)


def _user_frame(filename: str) -> bool:
    if filename.startswith("<"):
        return False
    try:
        path = Path(filename).resolve()
    except OSError, ValueError:
        return True
    return not path.is_relative_to(_PACKAGE_PATH) and not any(
        path.is_relative_to(library) for library in _LIBRARY_PATHS
    )


def _render_frames(traceback: TracebackType | None, report: _Report) -> None:
    frames: list[tuple[str, int, str]] = []
    visited = 0
    while traceback is not None and visited < _MAX_FRAMES:
        code = traceback.tb_frame.f_code
        if report.full or _user_frame(code.co_filename):
            frames.append((code.co_filename, traceback.tb_lineno, code.co_name))
        visited += 1
        traceback = traceback.tb_next
    if traceback is not None:
        report.truncated = True
    if not report.full and len(frames) > _MAX_USER_FRAMES:
        frames = frames[-_MAX_USER_FRAMES:]
        report.truncated = True
    if report.full and frames:
        report.line("Traceback (most recent call last):")
    for filename, lineno, function in frames:
        if report.remaining <= 0:
            report.truncated = True
            break
        report.line(f'  File "{filename}", line {lineno}, in {function}')
        source = linecache.getline(filename, lineno).strip()
        if source:
            report.line("    " + source)


def _render_syntax(exception: SyntaxError, report: _Report) -> None:
    report.line(f'  File "{exception.filename}", line {exception.lineno}')
    if not exception.text:
        return
    offset = max(0, (exception.offset or 1) - 1)
    start = max(0, offset - 80)
    source = exception.text[start : start + 400].rstrip("\n")
    prefix = "..." if start else ""
    if start or len(exception.text) > start + 400:
        report.truncated = True
    report.line("    " + prefix + source.expandtabs())
    before = source[: max(0, offset - start)].expandtabs()
    width = cell_len(prefix + before)
    end = exception.end_offset or exception.offset or 1
    span = max(1, min(80, end - (exception.offset or 1)))
    report.line("    " + " " * width + "^" * span)


def _render_exception(  # noqa: C901 -- bounded exception-graph traversal stays local
    exception: BaseException, report: _Report, *, depth: int = 0
) -> None:
    if (
        depth >= _MAX_DEPTH
        or len(report.seen) >= _MAX_NODES
        or id(exception) in report.seen
        or report.remaining <= 0
    ):
        report.truncated = True
        return
    report.seen.add(id(exception))
    message = safe_collection_message(exception)
    report.line(f"{type(exception).__name__}: {message}")
    if isinstance(exception, TypeError) and message.startswith("Param.__init__()"):
        report.line(
            "Hint: Snektest uses Param(value=..., name=...) with one value and an explicit stable name."
        )
        report.line(
            'For related inputs, use a tuple value, e.g. Param(value=(1, 2), name="pair"), and accept one tuple argument.'
        )
    notes = getattr(exception, "__notes__", ())
    if type(notes) in {list, tuple}:
        for note in notes[:_MAX_NOTES]:
            report.line(
                note if isinstance(note, str) else "<non-string exception note>"
            )
        if len(notes) > _MAX_NOTES:
            report.truncated = True
    _render_frames(exception.__traceback__, report)
    if isinstance(exception, SyntaxError):
        _render_syntax(exception, report)
    if exception.__cause__ is not None:
        report.line("Caused by:")
        _render_exception(exception.__cause__, report, depth=depth + 1)
    elif exception.__context__ is not None and not exception.__suppress_context__:
        report.line("While handling:")
        _render_exception(exception.__context__, report, depth=depth + 1)
    if isinstance(exception, BaseExceptionGroup):
        for index, grouped in enumerate(exception.exceptions[:_MAX_GROUP_MEMBERS], 1):
            report.line(f"Exception group member {index}:")
            _render_exception(grouped, report, depth=depth + 1)
        if len(exception.exceptions) > _MAX_GROUP_MEMBERS:
            report.truncated = True


def format_collection_error(
    error: CollectionError, *, full_traceback: bool = False
) -> str:
    """Describe collection failures with optional framework frames and fixed budgets."""
    cached = (
        error.collection_full_traceback if full_traceback else error.collection_console
    )
    if cached is not None:
        return cached
    report = _Report(full=full_traceback)
    subject = error.collection_file or error.collection_filter
    report.line(f"Collection error in {subject}:" if subject else "Collection error:")
    if error.collection_filter and error.collection_filter != error.collection_file:
        report.line(f"Filter: {error.collection_filter}")
    original = (
        error.__cause__
        if not full_traceback
        and error.collection_wrapped
        and error.__cause__ is not None
        else error
    )
    _render_exception(original, report)
    return report.finish()

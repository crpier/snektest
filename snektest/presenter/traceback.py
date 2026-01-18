import pathlib
from collections.abc import Callable
from types import TracebackType

from rich.console import Console
from rich.syntax import Syntax

import snektest


def render_traceback(  # noqa: PLR0913
    console: Console,
    exc_type: type[BaseException],
    exc_value: BaseException,
    traceback: object,
    *,
    show_exception_line: bool = True,
    open_path: Callable[[str], list[str]] | None = None,
) -> None:
    """Render a traceback without a box, using Rich for syntax highlighting."""
    console.print("[bold]Traceback[/bold] [dim](most recent call last):[/dim]")

    tb = traceback
    snektest_path = str(snektest.__file__).rsplit("/", 1)[0]

    while tb:
        if not isinstance(tb, TracebackType):
            break

        frame = tb.tb_frame
        lineno = tb.tb_lineno
        filename = frame.f_code.co_filename
        name = frame.f_code.co_name

        if not filename.startswith(snektest_path):
            console.print(
                f'  File "[cyan]{filename}[/cyan]", line {lineno}, in [yellow]{name}[/yellow]'
            )

            try:
                if open_path is None:
                    with pathlib.Path(filename).open(encoding="utf-8") as f:
                        lines = f.readlines()
                else:
                    lines = open_path(filename)
                if 0 <= lineno - 1 < len(lines):
                    code_line = lines[lineno - 1].rstrip()
                    syntax = Syntax(
                        code_line,
                        "python",
                        theme="ansi_dark",
                        line_numbers=False,
                        padding=(0, 0, 0, 4),
                    )
                    console.print(syntax)
            except (OSError, IndexError):
                pass

        tb = tb.tb_next

    if not show_exception_line:
        return

    exc_name = exc_type.__name__
    exc_msg = str(exc_value)
    console.print(f"[red bold]{exc_name}[/red bold]: {exc_msg}")

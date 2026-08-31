from rich.console import Console
from rich.markup import escape
from rich.syntax import Syntax

from snektest.models import ExceptionDiagnostic


def _render_single_traceback(
    console: Console,
    exception: ExceptionDiagnostic,
    *,
    show_exception_line: bool,
) -> None:
    console.print("[bold]Traceback[/bold] [dim](most recent call last):[/dim]")

    for frame in exception.frames:
        console.print(
            f'  File "[cyan]{frame.filename}[/cyan]", line {frame.lineno}, in [yellow]{frame.function_name}[/yellow]'
        )
        if frame.source_line is not None:
            console.print(
                Syntax(
                    frame.source_line,
                    "python",
                    theme="ansi_dark",
                    line_numbers=False,
                    padding=(0, 0, 0, 4),
                    word_wrap=True,
                )
            )

    if show_exception_line:
        console.print(
            f"[red bold]{exception.type_name}[/red bold]: {escape(exception.message)}"
        )
        for note in exception.notes:
            console.print(escape(note), style="red")


def render_traceback(
    console: Console,
    exception: ExceptionDiagnostic,
    *,
    show_exception_line: bool = True,
) -> None:
    """Render immutable exception chains and groups with captured source."""
    if exception.cause is not None:
        render_traceback(console, exception.cause)
        console.print()
        console.print(
            "[dim]The above exception was the direct cause of the following exception:[/dim]"
        )
        console.print()
    elif exception.context is not None and not exception.suppress_context:
        render_traceback(console, exception.context)
        console.print()
        console.print(
            "[dim]During handling of the above exception, another exception occurred:[/dim]"
        )
        console.print()

    _render_single_traceback(
        console,
        exception,
        show_exception_line=show_exception_line,
    )
    for index, grouped_exception in enumerate(exception.exceptions, start=1):
        console.print()
        console.rule(
            f"Exception group member {index}",
            style="dim red",
        )
        render_traceback(console, grouped_exception)

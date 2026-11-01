"""Bridge Hypothesis thread locals for individual coroutine steps, not awaits.

Hypothesis owns the BuildContext lifetime in the generating worker. Do not enter
that object again: its exit runs example cleanup callbacks. Bind its existing
value, settings and reporter only while advancing the example's await iterator.
The original native task still owns cancellation, ContextVars and fixture loads.
No Hypothesis state is installed while another event-loop task can execute.
"""

lazy from collections.abc import Callable, Coroutine, Generator
lazy from contextlib import AbstractContextManager, ExitStack, contextmanager
lazy from dataclasses import dataclass
lazy from typing import cast

lazy from hypothesis._settings import default_variable
lazy from hypothesis.control import _current_build_context
lazy from hypothesis.reporting import reporter
lazy from hypothesis.utils.dynamicvariables import DynamicVariable


@dataclass
class _VariableBinding[T]:
    variable: DynamicVariable[T]
    value: T

    @contextmanager
    def bind(self) -> Generator[None]:
        with self.variable.with_value(self.value):
            try:
                yield
            finally:
                # User reporter/settings scopes may span awaits. Save their
                # current value before restoring the loop thread's prior one.
                self.value = self.variable.value


@dataclass(frozen=True)
class ExampleContext:
    """One worker example's bindings; never re-enter its BuildContext itself."""

    bindings: tuple[Callable[[], AbstractContextManager[object]], ...]

    def step_scope(self) -> ExitStack:
        stack = ExitStack()
        try:
            for binding in self.bindings:
                _ = stack.enter_context(binding())
        except BaseException:
            stack.close()
            raise
        return stack

    async def run[Y, S, T](self, coroutine: Coroutine[Y, S, T]) -> T:
        if not isinstance(coroutine, Coroutine):
            msg = "Async Hypothesis examples must return a coroutine"
            raise TypeError(msg)
        return await _BoundExample(coroutine, self)


@dataclass
class _BoundExample[Y, S, T]:
    coroutine: Coroutine[Y, S, T]
    context: ExampleContext

    def __await__(self) -> Generator[Y, S, T]:
        # Follow yield-from's send/throw/close protocol without changing native
        # yield tokens (Trio traps and asyncio Futures must pass through intact).
        # Coroutine.__await__ erases its yield/send parameters in typeshed.
        iterator = cast("Generator[Y, S, T]", self.coroutine.__await__())
        try:
            with self.context.step_scope():
                yielded = next(iterator)
            while True:
                try:
                    value = yield yielded
                except GeneratorExit:
                    # Follow yield-from's close semantics, including finalizer
                    # errors retained by the task_cleanup diagnostic machinery.
                    with self.context.step_scope():
                        iterator.close()
                    raise
                except BaseException as raised:
                    with self.context.step_scope():
                        yielded = iterator.throw(raised)
                else:
                    with self.context.step_scope():
                        yielded = iterator.send(value)
        except StopIteration as completed:
            return cast("T", completed.value)


def capture_example_context() -> ExampleContext:
    """Capture on the Hypothesis worker before submitting an example task.

    These three DynamicVariable objects are a deliberately isolated dependency
    on Hypothesis internals. ContextVar copying alone cannot copy threading.local.
    The reporter is essential: final notes must reach Hypothesis's exception
    annotations, not leak into ordinary captured stdout.
    """
    return ExampleContext(
        bindings=(
            _VariableBinding(_current_build_context, _current_build_context.value).bind,
            _VariableBinding(default_variable, default_variable.value).bind,
            _VariableBinding(reporter, reporter.value).bind,
        ),
    )

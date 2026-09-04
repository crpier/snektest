"""JUnit XML adapter for normalized Snektest run results."""

from __future__ import annotations

from xml.etree.ElementTree import Element, SubElement, indent, tostring

from snektest._version import __version__
from snektest.models import (
    ErrorResult,
    ExceptionDiagnostic,
    ExpectedFailureResult,
    FailedResult,
    RunResult,
    SkippedResult,
    TeardownFailure,
    TestResult,
    UnexpectedPassResult,
)
from snektest.structured import SCHEMA_VERSION


def _diagnostic_text(exception: ExceptionDiagnostic) -> str:
    lines = [
        f'  File "{frame.filename}", line {frame.lineno}, in {frame.function_name}'
        + (f"\n    {frame.source_line}" if frame.source_line else "")
        for frame in exception.frames
    ]
    lines.append(f"{exception.qualified_type_name}: {exception.message}")
    return "\n".join(lines)


def _append_teardown_case(
    suite: Element,
    *,
    classname: str,
    name: str,
    teardown_failure: TeardownFailure,
) -> None:
    test_case = SubElement(
        suite,
        "testcase",
        {"name": name, "classname": classname, "time": "0.000000000"},
    )
    exception = teardown_failure.exception
    SubElement(
        test_case,
        "error",
        {"message": exception.message, "type": exception.type_name},
    ).text = _diagnostic_text(exception)


def _append_test_case(suite: Element, test_result: TestResult) -> None:
    test_case = SubElement(
        suite,
        "testcase",
        {
            "name": str(test_result.name),
            "classname": test_result.name.file_path.as_posix(),
            "time": f"{test_result.duration:.9f}",
        },
    )
    match test_result.result:
        case SkippedResult(reason=reason):
            SubElement(
                test_case,
                "skipped",
                {"message": reason, "type": "skip"},
            )
        case ExpectedFailureResult(reason=reason):
            SubElement(
                test_case,
                "skipped",
                {"message": reason, "type": "xfail"},
            )
        case UnexpectedPassResult(reason=reason):
            SubElement(
                test_case,
                "failure",
                {"message": reason, "type": "UnexpectedPass"},
            ).text = reason
        case FailedResult(exception=exception):
            SubElement(
                test_case,
                "failure",
                {"message": exception.message, "type": exception.type_name},
            ).text = _diagnostic_text(exception)
        case ErrorResult(exception=exception):
            SubElement(
                test_case,
                "error",
                {"message": exception.message, "type": exception.type_name},
            ).text = _diagnostic_text(exception)
    if test_result.captured_output or test_result.fixture_teardown_output:
        SubElement(test_case, "system-out").text = "".join(
            (
                test_result.captured_output,
                test_result.fixture_teardown_output or "",
            )
        )
    if test_result.warnings:
        SubElement(test_case, "system-err").text = "\n".join(test_result.warnings)
    for teardown_failure in test_result.fixture_teardown_failures:
        _append_teardown_case(
            suite,
            classname="snektest.function_teardown",
            name=f"{test_result.name}::teardown[{teardown_failure.fixture_name}]",
            teardown_failure=teardown_failure,
        )


def build_junit_xml(run_result: RunResult) -> str:
    """Render one normalized run as a JUnit `testsuite` document."""
    teardown_failure_count = (
        run_result.fixture_teardown_failed
        + run_result.session_teardown_failed
        + run_result.run_teardown_failed
    )
    suite = Element(
        "testsuite",
        {
            "name": "snektest",
            "tests": str(run_result.total_tests + teardown_failure_count),
            "failures": str(run_result.failed + run_result.unexpected_passes),
            "errors": str(run_result.errors + teardown_failure_count),
            "skipped": str(run_result.skipped + run_result.expected_failures),
            "time": f"{run_result.total_duration:.9f}",
        },
    )
    properties = SubElement(suite, "properties")
    SubElement(
        properties,
        "property",
        {"name": "snektest.schema_version", "value": str(SCHEMA_VERSION)},
    )
    SubElement(
        properties,
        "property",
        {"name": "snektest.framework_version", "value": __version__},
    )
    SubElement(
        properties,
        "property",
        {"name": "snektest.selected_tests", "value": str(run_result.selected_tests)},
    )
    SubElement(
        properties,
        "property",
        {"name": "snektest.stopped_early", "value": str(run_result.stopped_early)},
    )
    for test_result in run_result.test_results:
        _append_test_case(suite, test_result)
    for teardown_failure in run_result.session_teardown_failures:
        _append_teardown_case(
            suite,
            classname="snektest.session_teardown",
            name=f"session teardown[{teardown_failure.fixture_name}]",
            teardown_failure=teardown_failure,
        )
    for teardown_failure in run_result.run_teardown_failures:
        _append_teardown_case(
            suite,
            classname="snektest.run_teardown",
            name=f"run teardown[{teardown_failure.fixture_name}]",
            teardown_failure=teardown_failure,
        )

    suite_output = "".join(
        (
            run_result.collection_output,
            run_result.session_teardown_output or "",
            run_result.run_teardown_output or "",
        )
    )
    if suite_output:
        SubElement(suite, "system-out").text = suite_output
    if run_result.warnings:
        SubElement(suite, "system-err").text = "\n".join(run_result.warnings)

    indent(suite)
    return tostring(suite, encoding="unicode", xml_declaration=True)


__all__ = ["build_junit_xml"]

"""JUnit text and attributes must obey the XML 1.0 character grammar."""

from pathlib import Path
from xml.etree.ElementTree import fromstring

from snektest import Param, assert_eq, assert_is_not_none, test
from snektest.junit import build_junit_xml
from snektest.models import (
    ErrorResult,
    ExceptionDiagnostic,
    RunResult,
    TestName,
    TestResult,
)


def _render_sample(sample: str) -> str:
    diagnostic = ExceptionDiagnostic(
        frames=(),
        message=sample,
        qualified_type_name="SampleError",
        type_name="SampleError",
    )
    outcome = TestResult(
        captured_output=sample,
        duration=0,
        fixture_teardown_failures=(),
        fixture_teardown_output=None,
        markers=(),
        name=TestName(
            file_path=Path("test_sample.py"),
            func_name="test_sample",
            params_part=sample,
        ),
        result=ErrorResult(exception=diagnostic),
        warnings=(sample,),
    )
    return build_junit_xml(
        RunResult.from_execution(
            collection_output=sample,
            collection_warnings=(sample,),
            run_teardown_failures=[],
            run_teardown_output=sample,
            run_teardown_warnings=(sample,),
            session_teardown_failures=[],
            session_teardown_output=sample,
            session_teardown_warnings=(sample,),
            test_results=[outcome],
            total_duration=0,
        )
    )


@test(
    [
        Param(character, f"U+{ord(character):04X}")
        for character in "\x00\x08\x0b\x0c\x0e\x1f\ud800\udfff\ufffe\uffff"
    ],
    mark="fast",
)
def test_junit_replaces_forbidden_characters(character: str) -> None:
    """Controls, surrogates and BMP noncharacters cannot invalidate the document."""
    suite = fromstring(_render_sample(f"before{character}after"))  # noqa: S314
    output = assert_is_not_none(suite.find("testcase/system-out"))
    error = assert_is_not_none(suite.find("testcase/error"))
    assert_eq(output.text, "before\ufffdafter")
    assert_eq(error.attrib["message"], "before\ufffdafter")


@test(mark="fast")
def test_junit_preserves_valid_unicode() -> None:
    """Keep legal Unicode and let ElementTree escape XML metacharacters."""
    sample = "\t\n spaces & < > quotes '\" \u007f\u0085\ud7ff\ue000\ufffd\U00010000\U0010ffff"
    suite = fromstring(_render_sample(sample))  # noqa: S314
    output = assert_is_not_none(suite.find("testcase/system-out"))
    error = assert_is_not_none(suite.find("testcase/error"))
    assert_eq(output.text, sample)
    assert_eq(error.attrib["message"], sample)

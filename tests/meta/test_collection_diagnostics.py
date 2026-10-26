"""Collection failures through the real console and JSON command interfaces."""

import json
import subprocess
import sys

from snektest import (
    Param,
    assert_eq,
    assert_false,
    assert_in,
    assert_not_in,
    load_fixture,
    test,
)
from testutils.fixtures import tmp_dir_fixture
from testutils.helpers import create_test_file


@test([Param(None, name="local"), Param("1", name="worker")], mark="slow")
def test_missing_import_leads_with_user_context_without_framework_traceback(
    workers: str | None,
) -> None:
    directory = load_fixture(tmp_dir_fixture())
    path = create_test_file(
        directory,
        "import missing_dependency_for_collection_102\n",
        name="test_missing_import",
    )
    worker_args = [] if workers is None else ["--workers", workers]
    completed = subprocess.run(
        [sys.executable, "-m", "snektest", *worker_args, str(path)],
        capture_output=True,
        check=False,
        text=True,
        timeout=10,
    )
    output = completed.stdout + completed.stderr
    assert_eq(completed.returncode, 2, msg=output)
    assert_in("Collection error", output)
    assert_in(path.name, output)
    assert_in("ModuleNotFoundError", output)
    assert_in("missing_dependency_for_collection_102", output)
    assert_not_in("Traceback (most recent call last)", output)
    assert_not_in("asyncio/runners.py", output)
    assert_not_in("asyncio\\runners.py", output)


@test(
    [Param(None, name="local"), Param("1", name="worker")],
    [Param("", name="run"), Param("--collect-only", name="collect-only")],
    mark="slow",
)
def test_full_traceback_retains_collection_framework_frames(
    workers: str | None,
    action: str,
) -> None:
    directory = load_fixture(tmp_dir_fixture())
    path = create_test_file(
        directory,
        "raise RuntimeError('broken collection configuration')\n",
        name="test_full_collection_traceback",
    )
    worker_args = [] if workers is None else ["--workers", workers]
    action_args = [action] if action else []
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "snektest",
            "--full-traceback",
            *action_args,
            *worker_args,
            str(path),
        ],
        capture_output=True,
        check=False,
        text=True,
        timeout=10,
    )
    output = completed.stdout + completed.stderr
    assert_eq(completed.returncode, 2, msg=output)
    assert_in("RuntimeError: broken collection configuration", output)
    assert_in(path.name, output)
    assert_in("Traceback (most recent call last)", output)
    assert_in("collect_tests_from_filters", output)


@test([Param(None, name="local"), Param("1", name="worker")], mark="slow")
def test_syntax_error_retains_filename_source_line_and_caret(
    workers: str | None,
) -> None:
    directory = load_fixture(tmp_dir_fixture())
    path = create_test_file(
        directory,
        "from snektest import test\n\ndef invalid()\n    pass\n",
        name="test_syntax_error",
    )
    worker_args = [] if workers is None else ["--workers", workers]
    completed = subprocess.run(
        [sys.executable, "-m", "snektest", *worker_args, str(path)],
        capture_output=True,
        check=False,
        text=True,
        timeout=10,
    )
    output = completed.stdout + completed.stderr
    assert_eq(completed.returncode, 2, msg=output)
    assert_in(path.name, output)
    assert_in("SyntaxError", output)
    assert_in("line 3", output)
    assert_in("def invalid()", output)
    assert_in("^", output)
    assert_not_in("collect_tests_from_filters", output)


@test([Param(None, name="local"), Param("1", name="worker")], mark="slow")
def test_collection_diagnostic_preserves_meaningful_exception_causes(
    workers: str | None,
) -> None:
    directory = load_fixture(tmp_dir_fixture())
    path = create_test_file(
        directory,
        "try:\n    raise KeyError('database setting missing')\n"
        "except KeyError as cause:\n    raise RuntimeError('cannot configure service') from cause\n",
        name="test_collection_causes",
    )
    worker_args = [] if workers is None else ["--workers", workers]
    completed = subprocess.run(
        [sys.executable, "-m", "snektest", *worker_args, str(path)],
        capture_output=True,
        check=False,
        text=True,
        timeout=10,
    )
    output = completed.stdout + completed.stderr
    assert_eq(completed.returncode, 2, msg=output)
    assert_in("KeyError", output)
    assert_in("database setting missing", output)
    assert_in("RuntimeError: cannot configure service", output)
    assert_not_in("collect_tests_from_filters", output)


@test([Param(None, name="local"), Param("1", name="worker")], mark="slow")
def test_collection_exception_groups_keep_nested_types_messages_and_notes(
    workers: str | None,
) -> None:
    directory = load_fixture(tmp_dir_fixture())
    path = create_test_file(
        directory,
        "leaf = TypeError('port must be integer')\n"
        "leaf.add_note('check the settings file')\n"
        "raise ExceptionGroup('configuration invalid', [\n"
        "    ValueError('missing endpoint'), ExceptionGroup('field errors', [leaf])\n"
        "])\n",
        name="test_collection_group",
    )
    worker_args = [] if workers is None else ["--workers", workers]
    completed = subprocess.run(
        [sys.executable, "-m", "snektest", *worker_args, str(path)],
        capture_output=True,
        check=False,
        text=True,
        timeout=10,
    )
    output = completed.stdout + completed.stderr
    assert_eq(completed.returncode, 2, msg=output)
    assert_in("ExceptionGroup: configuration invalid", output)
    assert_in("ValueError: missing endpoint", output)
    assert_in("ExceptionGroup: field errors", output)
    assert_in("TypeError: port must be integer", output)
    assert_in("check the settings file", output)
    assert_not_in("collect_tests_from_filters", output)


@test(
    [Param(None, name="local"), Param("1", name="worker")],
    [Param("", name="concise"), Param("--full-traceback", name="full")],
    mark="slow",
)
def test_broken_exception_formatter_keeps_original_type_and_collection_exit(
    workers: str | None,
    full_flag: str,
) -> None:
    directory = load_fixture(tmp_dir_fixture())
    path = create_test_file(
        directory,
        "class BrokenConfigurationError(Exception):\n"
        "    def __str__(self) -> str:\n"
        "        raise RuntimeError('formatter failed')\n"
        "raise BrokenConfigurationError()\n",
        name="test_broken_collection_formatter",
    )
    arguments = [] if workers is None else ["--workers", workers]
    if full_flag:
        arguments.append(full_flag)
    completed = subprocess.run(
        [sys.executable, "-m", "snektest", *arguments, str(path)],
        capture_output=True,
        check=False,
        text=True,
        timeout=10,
    )
    output = completed.stdout + completed.stderr
    assert_eq(completed.returncode, 2, msg=output)
    assert_in("BrokenConfigurationError", output)
    assert_in(path.name, output)
    assert_not_in("Unexpected error", output)


@test(
    [Param(None, name="local"), Param("1", name="worker")],
    [Param("", name="concise"), Param("--full-traceback", name="full")],
    mark="slow",
)
def test_oversized_collection_diagnostics_are_bounded_and_explicitly_truncated(
    workers: str | None,
    full_flag: str,
) -> None:
    directory = load_fixture(tmp_dir_fixture())
    path = create_test_file(
        directory,
        "raise ExceptionGroup('too many configuration errors', [\n"
        "    ValueError('detail-' * 10000) for _ in range(120)\n"
        "])\n",
        name="test_large_collection_failure",
    )
    arguments = [] if workers is None else ["--workers", workers]
    if full_flag:
        arguments.append(full_flag)
    completed = subprocess.run(
        [sys.executable, "-m", "snektest", *arguments, str(path)],
        capture_output=True,
        check=False,
        text=True,
        timeout=15,
    )
    output = completed.stdout + completed.stderr
    assert_eq(completed.returncode, 2)
    assert_in("ExceptionGroup", output)
    assert_in("ValueError", output)
    assert_in("truncated", output.lower())
    assert_eq(
        len(output) < 65_536, True, msg=f"diagnostic has {len(output)} characters"
    )


@test([Param(None, name="local"), Param("1", name="worker")], mark="slow")
def test_collection_heading_identifies_selected_file_when_a_helper_import_fails(
    workers: str | None,
) -> None:
    directory = load_fixture(tmp_dir_fixture()) / "collection_context"
    directory.mkdir(exist_ok=True)
    _ = (directory / "__init__.py").write_text("")
    _ = (directory / "settings.py").write_text(
        "raise RuntimeError('invalid dependency')\n"
    )
    path = create_test_file(
        directory,
        "from .settings import VALUE\n",
        name="test_selected",
    )
    worker_args = [] if workers is None else ["--workers", workers]
    completed = subprocess.run(
        [sys.executable, "-m", "snektest", *worker_args, str(path)],
        capture_output=True,
        check=False,
        text=True,
        timeout=10,
    )
    output = completed.stdout + completed.stderr
    assert_eq(completed.returncode, 2, msg=output)
    assert_in(path.name, output.splitlines()[0])
    assert_in("settings.py", output)
    assert_in("RuntimeError: invalid dependency", output)


@test(
    [
        Param(
            ("from snektest import Param\nParam(value='sqlite')\n", "named-scalar"),
            name="missing-name",
        ),
        Param(
            ("from snektest import Param\nParam(1, 2, name='pair')\n", "tuple-value"),
            name="multiple-values",
        ),
    ],
    mark="slow",
)
def test_bad_parameter_construction_shows_corrective_canonical_spelling(
    invalid: tuple[str, str],
) -> None:
    source, case = invalid
    directory = load_fixture(tmp_dir_fixture())
    path = create_test_file(
        directory, source, name=f"test_invalid_param_{case.replace('-', '_')}"
    )
    completed = subprocess.run(
        [sys.executable, "-m", "snektest", str(path)],
        capture_output=True,
        check=False,
        text=True,
        timeout=10,
    )
    output = completed.stdout + completed.stderr
    assert_eq(completed.returncode, 2, msg=output)
    assert_in("TypeError", output)
    assert_in("Param(value=..., name=...)", output)
    if case == "tuple-value":
        assert_in("tuple", output)
    assert_not_in("collect_tests_from_filters", output)


@test([Param(None, name="local"), Param("1", name="worker")], mark="slow")
def test_application_collection_error_is_not_mistaken_for_framework_wrapper(
    workers: str | None,
) -> None:
    directory = load_fixture(tmp_dir_fixture())
    path = create_test_file(
        directory,
        "from snektest import CollectionError\n"
        "try:\n    raise ValueError('underlying failure')\n"
        "except ValueError as cause:\n    raise CollectionError('application refused collection') from cause\n",
        name="test_application_collection_error",
    )
    worker_args = [] if workers is None else ["--workers", workers]
    completed = subprocess.run(
        [sys.executable, "-m", "snektest", *worker_args, str(path)],
        capture_output=True,
        check=False,
        text=True,
        timeout=10,
    )
    output = completed.stdout + completed.stderr
    assert_eq(completed.returncode, 2, msg=output)
    assert_in("ValueError: underlying failure", output)
    assert_in("CollectionError: application refused collection", output)


@test([Param(None, name="local"), Param("1", name="worker")], mark="slow")
def test_full_traceback_does_not_change_collection_json_or_execute_any_body(
    workers: str | None,
) -> None:
    directory = load_fixture(tmp_dir_fixture())
    marker = directory / "collection-body-ran"
    marker.unlink(missing_ok=True)
    valid = create_test_file(
        directory,
        f"from pathlib import Path\nfrom snektest import test\n"
        f"@test(mark='medium')\ndef body() -> None:\n    Path({str(marker)!r}).touch()\n",
        name="test_valid_before_collection_failure",
    )
    invalid = create_test_file(
        directory,
        "import os, warnings\n"
        "print('collection output')\n"
        "warnings.warn('collection warning', stacklevel=1)\n"
        "_ = os.write(1, b'raw collection output\\n')\n"
        "raise ValueError('cannot finish collection')\n",
        name="test_json_collection_failure",
    )
    worker_args = [] if workers is None else ["--workers", workers]
    documents: list[dict[str, object]] = []
    for display in ([], ["--full-traceback"]):
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "snektest",
                "--json-output",
                *display,
                *worker_args,
                str(valid),
                str(invalid),
            ],
            capture_output=True,
            check=False,
            text=True,
            timeout=10,
        )
        assert_eq(completed.returncode, 2, msg=completed.stdout + completed.stderr)
        document = json.loads(completed.stdout)
        documents.append(document)
        assert_eq(document["schema_version"], 1)
        assert_eq(document["error"]["category"], "collection")
        assert_eq(document["error"]["cause"]["type"], "ValueError")
        assert_eq(document["error"]["cause"]["message"], "cannot finish collection")
        assert_eq(document["collection_output"], "collection output\n")
        assert_eq(len(document["collection_warnings"]), 1)
        assert_in("collection warning", document["collection_warnings"][0])
        assert_in("raw collection output", document["uncaptured_output"])
        assert_eq(completed.stdout.count("\n"), 1)
    assert_eq(documents[0], documents[1])
    assert_false(marker.exists())


@test(mark="slow")
def test_long_causal_chain_cannot_hide_the_actionable_final_error() -> None:
    directory = load_fixture(tmp_dir_fixture())
    path = create_test_file(
        directory,
        "cause = None\n"
        "for index in range(80):\n"
        "    error = ValueError('prior failure ' + str(index) + 'padding-' * 1000)\n"
        "    error.__cause__ = cause\n"
        "    cause = error\n"
        "raise RuntimeError('final configuration rejected') from cause\n",
        name="test_long_collection_chain",
    )
    completed = subprocess.run(
        [sys.executable, "-m", "snektest", str(path)],
        capture_output=True,
        check=False,
        text=True,
        timeout=10,
    )
    output = completed.stdout + completed.stderr
    assert_eq(completed.returncode, 2, msg=output)
    assert_in("RuntimeError: final configuration rejected", output)
    assert_in("truncated", output.lower())
    assert_eq(len(output) < 65_536, True)


@test(
    [
        Param(
            (
                "from snektest import test\n@test\ndef invalid():\n    pass\n",
                "BadRequestError",
                "@test()",
            ),
            name="bare-decorator",
        ),
        Param(
            (
                "from pydantic import BaseModel\nclass Unsupported: pass\nclass BrokenModel(BaseModel):\n    value: Unsupported\n",
                "PydanticSchemaGenerationError",
                "BrokenModel",
            ),
            name="application-model",
        ),
    ],
    mark="slow",
)
def test_authoring_errors_keep_actionable_user_context(
    invalid: tuple[str, str, str],
) -> None:
    source, error_type, context = invalid
    directory = load_fixture(tmp_dir_fixture())
    path = create_test_file(
        directory, source, name=f"test_authoring_{error_type.lower()}"
    )
    completed = subprocess.run(
        [sys.executable, "-m", "snektest", str(path)],
        capture_output=True,
        check=False,
        text=True,
        timeout=10,
    )
    output = completed.stdout + completed.stderr
    assert_eq(completed.returncode, 2, msg=output)
    assert_in(path.name, output)
    assert_in(error_type, output)
    assert_in(context, output)
    assert_not_in("collect_tests_from_filters", output)
    assert_not_in("_generate_schema.py", output)


@test([Param("", name="concise"), Param("--full-traceback", name="full")], mark="slow")
def test_suppressed_context_is_not_reintroduced_by_collection_reporting(
    full_flag: str,
) -> None:
    directory = load_fixture(tmp_dir_fixture())
    path = create_test_file(
        directory,
        "try:\n    raise ValueError('hidden earlier failure')\n"
        "except ValueError:\n    raise RuntimeError('public configuration error') from None\n",
        name="test_suppressed_collection_context",
    )
    arguments = [full_flag] if full_flag else []
    completed = subprocess.run(
        [sys.executable, "-m", "snektest", *arguments, str(path)],
        capture_output=True,
        check=False,
        text=True,
        timeout=10,
    )
    output = completed.stdout + completed.stderr
    assert_eq(completed.returncode, 2, msg=output)
    assert_in("RuntimeError: public configuration error", output)
    assert_not_in("ValueError: hidden earlier failure", output)

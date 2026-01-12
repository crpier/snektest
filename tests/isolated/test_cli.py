from __future__ import annotations

import asyncio
import sys
from typing import Any
from unittest.mock import patch

from snektest import assert_eq, assert_raises, test
from snektest.models import BadRequestError, CollectionError, UnreachableError


@test()
def test_parse_cli_args_invalid_option_returns_2() -> None:
    from snektest.cli import parse_cli_args

    result = parse_cli_args(["--nope"])
    assert_eq(result, 2)


@test()
def test_parse_cli_args_defaults_to_dot() -> None:
    from snektest.cli import parse_cli_args

    parsed = parse_cli_args([])
    assert not isinstance(parsed, int)

    potential_filter, options = parsed
    assert_eq(potential_filter, ["."])
    assert_eq(options.capture_output, True)
    assert_eq(options.json_output, False)
    assert_eq(options.pdb_on_failure, False)


@test()
def test_parse_cli_args_s_flag_disables_capture() -> None:
    from snektest.cli import parse_cli_args

    parsed = parse_cli_args(["-s", "."])
    assert not isinstance(parsed, int)

    _potential_filter, options = parsed
    assert_eq(options.capture_output, False)


@test()
async def test_run_script_returns_2_on_args_error() -> None:
    from snektest import cli

    with patch.object(sys, "argv", ["snektest", "does-not-exist"]):
        result = await cli.run_script()
    assert_eq(result, 2)


@test()
async def test_run_script_returns_parse_cli_args_exit_code() -> None:
    from snektest import cli

    with patch.object(sys, "argv", ["snektest", "--nope"]):
        result = await cli.run_script()
    assert_eq(result, 2)


@test()
async def test_run_script_returns_2_on_cancelled_error() -> None:
    from snektest import cli

    async def raise_cancelled(*args: Any, **kwargs: Any) -> Any:
        raise asyncio.CancelledError

    with (
        patch.object(sys, "argv", ["snektest", "."]),
        patch.object(cli, "run_tests_programmatic", raise_cancelled),
    ):
        result = await cli.run_script()

    assert_eq(result, 2)


@test()
def test_main_exit_paths() -> None:
    from snektest import cli

    def assert_exits(exc: BaseException | None) -> None:
        def fake_run(*args: Any, **kwargs: Any) -> int:
            # snektest.main closes the event loop; emulate the normal lifecycle.
            if args and hasattr(args[0], "close"):
                args[0].close()
            if exc is not None:
                raise exc
            return 0

        with (
            patch.object(cli.asyncio, "run", fake_run),
            patch.object(cli.sys, "exit", side_effect=SystemExit) as exit_mock,
        ):
            with assert_raises(SystemExit):
                cli.main()

        exit_mock.assert_called_once()

    assert_exits(None)
    assert_exits(CollectionError("x"))
    assert_exits(BadRequestError("x"))
    assert_exits(UnreachableError("x"))
    assert_exits(KeyboardInterrupt())
    assert_exits(RuntimeError("x"))

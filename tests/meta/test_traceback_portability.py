"""Cross-platform regressions for user traceback frame selection."""

import os
import subprocess
import sys
import tempfile
from pathlib import Path
from textwrap import dedent

from snektest import assert_eq, assert_in, assert_not_in, test


@test(mark="slow")
def test_package_name_prefix_does_not_hide_user_traceback_frame() -> None:
    """A sibling path sharing the package-name prefix remains user code."""
    package_directory = Path("snektest").resolve()
    with tempfile.TemporaryDirectory(
        prefix=f"{package_directory.name}-user-",
        dir=package_directory.parent,
    ) as tmp:
        test_file = Path(tmp) / "test_user_failure.py"
        _ = test_file.write_text(
            dedent("""
                from snektest import test

                @test()
                def test_user_failure() -> None:
                    raise RuntimeError("user failure")
            """)
        )

        completed = subprocess.run(
            [sys.executable, "-m", "snektest.cli", str(test_file)],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )

    normalized_output = os.path.normcase(completed.stdout + completed.stderr).casefold()
    assert_eq(completed.returncode, 1)
    assert_in(test_file.name.casefold(), normalized_output)
    assert_in("test_user_failure", normalized_output)
    assert_in('raise runtimeerror("user failure")', normalized_output)
    assert_in("runtimeerror: user failure", normalized_output)
    assert_not_in("execution.py", normalized_output)

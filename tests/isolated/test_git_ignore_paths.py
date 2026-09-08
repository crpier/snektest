"""Git ignore decisions belong to discovery names, not symlink targets."""

import asyncio
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory

from snektest import Param, assert_eq, assert_raises, skip, test
from snektest.collection import generate_file_list, git_ignored_files
from snektest.models import CollectionError, FilterItem


def _discover_symlinks(*, external: bool, ignored: bool, explicit: bool) -> list[str]:
    with TemporaryDirectory() as temporary:
        root = Path(temporary)
        repository = root / "repo"
        repository.mkdir()
        _ = subprocess.run(
            ["git", "init", "--quiet", str(repository)],
            check=True,
            capture_output=True,
        )
        target = (
            root / "test_external.py" if external else repository / "test_target.py"
        )
        _ = target.write_text("")
        _ = (repository / "test_visible.py").write_text("")
        _ = (repository / "test_generated.py").write_text("")
        _ = (repository / ".gitignore").write_text(
            "test_generated.py\n" + ("test_alias.py\n" if ignored else "")
        )
        alias = repository / "test_alias.py"
        try:
            alias.symlink_to(target)
        except OSError as error:
            skip(f"Symlink creation unavailable: {error}")

        selected = generate_file_list(
            FilterItem(str(alias if explicit else repository))
        )
        return [path.name for path in selected]


@test(
    [Param(True, "external"), Param(False, "internal")],
    [Param(True, "ignored"), Param(False, "included")],
    mark="slow",
)
async def test_symlink_ignore_uses_discovery_name(
    external: bool,  # noqa: FBT001 - positional parameterized test axis
    ignored: bool,  # noqa: FBT001 - positional parameterized test axes
) -> None:
    """Neither outside targets nor ignored aliases change unrelated selection."""
    selected = await asyncio.to_thread(
        _discover_symlinks, external=external, ignored=ignored, explicit=False
    )
    expected = (
        ([] if ignored else ["test_alias.py"])
        + ([] if external else ["test_target.py"])
        + ["test_visible.py"]
    )
    assert_eq(selected, expected)


@test(mark="slow")
async def test_explicit_ignored_symlink_runs() -> None:
    """An explicit file retains the existing Git-ignore bypass."""
    selected = await asyncio.to_thread(
        _discover_symlinks, external=True, ignored=True, explicit=True
    )
    assert_eq(selected, ["test_alias.py"])


@test(mark="slow")
async def test_invalid_git_candidate_is_a_collection_error() -> None:
    """A failed batch cannot silently admit every ignored candidate."""

    def check_invalid_candidate() -> None:
        with TemporaryDirectory() as temporary:
            repository = Path(temporary) / "repo"
            repository.mkdir()
            _ = subprocess.run(
                ["git", "init", "--quiet", str(repository)],
                check=True,
                capture_output=True,
            )
            with assert_raises(CollectionError):
                _ = git_ignored_files([Path(temporary) / "outside.py"], cwd=repository)

    await asyncio.to_thread(check_invalid_candidate)

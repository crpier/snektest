import asyncio
import importlib.util
import sys
from argparse import ArgumentParser
from pathlib import Path

from snektest.runner import test_session


async def cli(args) -> None:
    # TODO: support multiple paths
    file_path = Path(args.import_path)
    module_name = file_path.stem
    spec = importlib.util.spec_from_file_location(module_name, file_path)
    # TODO: does this show a good error message?
    if spec is None:
        sys.exit(1)
    if spec.loader is None:
        sys.exit(1)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    await test_session.run_tests()


def main() -> None:
    parser = ArgumentParser()
    parser.add_argument("import_path", help="Import path to the test")
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Show additional output during test runs",
    )
    args = parser.parse_args()

    asyncio.run(cli(args))


if __name__ == "__main__":
    main()

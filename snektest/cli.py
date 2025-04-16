import asyncio
import importlib.util
import sys
from argparse import ArgumentParser
from pathlib import Path

from snek.snektest.runner import test_session


async def cli(args):
    # TODO: I don't really like how using this path scheme looks
    # probably should just use the same scheme as pytest (except fixture params, I guess)
    # module_part = args.import_path
    # rest = ""
    # try:
    #     while module_part != "":
    #         try:
    #             module = import_module(module_part)
    #             break
    #         except ModuleNotFoundError:
    #             module_part, rest = module_part.rsplit(".", 1)
    #     else:
    #         raise ValueError(f"Failed to import module: {args.import_path}")
    # except ValueError:
    #     print(f"Could not import module: {args.import_path}")
    #     exit(1)
    #
    # if rest == "":
    #     await test_session.run_tests(verbose=args.verbose)
    # else:
    #     target = getattr(module, rest)
    #     match target:
    #         # if it's a function:
    #         case callable:
    #             await test_session.run_tests([target], verbose=args.verbose)
    #
    file_path = Path(args.import_path)
    module_name = file_path.stem
    spec = importlib.util.spec_from_file_location(module_name, file_path)
    if spec is None:
        print(f"Failed to import module: {args.import_path}")
        exit(1)
    if spec.loader is None:
        print(f"Failed to import module: {args.import_path}")
        exit(1)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    await test_session.run_tests()


def main():
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

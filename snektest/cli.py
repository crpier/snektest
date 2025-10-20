from argparse import ArgumentParser
from asyncio import run
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from sys import modules

from snektest.models import TestPath
from snektest.runner import global_session


def load_path(import_path: Path) -> None:
    if import_path.is_dir():
        for dirpath, _, filenames in import_path.walk():
            for name in filenames:
                if not name.endswith(".py"):
                    continue
                file_path = dirpath / name
                # TODO: I don't like that I have to convert to str here
                test_path = TestPath(str(file_path))

                # TODO: either check for duplicates earlier, or ensure that the module name is unique here
                unique_path = str(file_path).replace(".", "_")
                module_name = f"snektest_loaded_{unique_path.replace('/', '_').replace('\\', '_')}"

                spec = spec_from_file_location(module_name, file_path)
                if spec is None:
                    msg = f"Failed to load module spec from `{file_path}`."
                    raise ValueError(msg)
                if spec.loader is None:
                    msg = f"Failed to get module spec loader from `{file_path}`."
                    raise ValueError(msg)

                module = module_from_spec(spec)

                module.__dict__["test_path"] = test_path
                modules[module_name] = module
                spec.loader.exec_module(module)
    # TODO:  holy mother of duplicates
    else:
        file_path = import_path
        test_path = TestPath(str(file_path))
        # TODO: either check for duplicates earlier, or ensure that the module name is unique here
        unique_path = str(file_path).replace(".", "_")
        module_name = (
            f"snektest_loaded_{unique_path.replace('/', '_').replace('\\', '_')}"
        )

        spec = spec_from_file_location(module_name, file_path)
        if spec is None:
            msg = f"Failed to load module spec from `{file_path}`."
            raise ValueError(msg)
        if spec.loader is None:
            msg = f"Failed to get module spec loader from `{file_path}`."
            raise ValueError(msg)

        module = module_from_spec(spec)

        module.__dict__["test_path"] = test_path
        modules[module_name] = module
        spec.loader.exec_module(module)


async def main_call() -> None:
    # TODO: use snekargs instead
    parser = ArgumentParser()
    _ = parser.add_argument("import_paths", help="Import path to the test", nargs="+")
    args = parser.parse_args()
    import_paths = args.import_paths
    # TODO: this can raise an error, but how to display it in a nice way?
    for import_path in import_paths:
        load_path(Path(import_path))
    await global_session.run_tests()


def main() -> None:
    run(main_call())


if __name__ == "__main__":
    main()

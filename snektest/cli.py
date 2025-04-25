from argparse import ArgumentParser
from importlib.util import module_from_spec, spec_from_file_location
from sys import modules

from snektest.models import TestPath
from snektest.runner import global_session


def load_path(test_path: TestPath) -> None:
    if not test_path.file.is_file():
        msg = f"Tried to load {test_path.file}, but it is not a file."
        raise NotImplementedError(msg)

    # TODO: either check for duplicates earlier, or ensure that the module name is unique here
    unique_path = str(test_path.file).replace(".", "_")
    module_name = f"snektest_loaded_{unique_path.replace('/', '_').replace('\\', '_')}"

    spec = spec_from_file_location(module_name, test_path.file)
    if spec is None:
        msg = f"Failed to load module spec from `{test_path.file}`."
        raise ValueError(msg)
    if spec.loader is None:
        msg = f"Failed to get module spec loader from `{test_path.file}`."
        raise ValueError(msg)

    module = module_from_spec(spec)
    module.__dict__["test_path"] = test_path
    modules[module_name] = module
    spec.loader.exec_module(module)


def main() -> None:
    # TODO: use snekargs instead
    parser = ArgumentParser()
    _ = parser.add_argument("import_paths", help="Import path to the test", nargs="+")
    args = parser.parse_args()
    # TODO: this can raise an error, but how to display it in a nice way?
    test_paths = [TestPath(uri) for uri in args.import_paths]
    for test_path in test_paths:
        load_path(test_path)
    global_session.run_tests()


if __name__ == "__main__":
    main()

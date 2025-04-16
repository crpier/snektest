import random

from snek.snektest.stubs import fixture


def load_seed() -> int:
    return 420


def get_some_value(seed: int) -> int:
    return seed


def side_effect() -> None:
    print("side effect startup")


class StringFixtures:
    def __init__(self) -> None:
        self.root_string = fixture(lambda: random.choice(["a", "b", "c"]) * 10)

    def upper_string(self) -> str:
        return self.root_string.upper()

    def lower_string(self) -> str:
        return self.root_string.lower()

    def root_plus_some_value(self) -> str:
        some_value = fixture(get_some_value, seed=len(self.root_string))
        return self.root_string + str(some_value)


class FunctionTests:
    def value_from_same_class(self) -> int:
        return 42

    def __init__(self) -> None:
        self.seed = fixture(load_seed)
        self.value = fixture(get_some_value, seed=self.seed)
        self.some_other_value = fixture(self.value_from_same_class)
        self.side_effect = fixture(side_effect)
        self.string_fixtures = fixture(StringFixtures)

    def test_1(self) -> None:
        print(self.value)
        print(self.side_effect)
        print(self.string_fixtures.upper_string)
        print(self.string_fixtures.lower_string)
        assert True

    def test_2(self) -> None:
        assert self.value == 420

    def test_3(self) -> None:
        assert True


function_tests = FunctionTests().test_2()

import asyncio

from snektest.runner import (
    fixture_async,
    load_fixture_async,
    test_async,
)

root_fixture_started_up = False
root_fixture_torn_down = False
child_fixture_started_up = False
child_fixture_torn_down = False

# TODO: also test that fixture setup and teardowns are called only once


async def async_work():
    await asyncio.sleep(0)


@fixture_async()
async def load_root_fixture():
    await async_work()
    global root_fixture_started_up
    root_fixture_started_up = True

    yield 1

    global root_fixture_torn_down
    root_fixture_torn_down = True


@test_async()
async def root_fixture_passes_correct_value():
    root_fixture = await load_fixture_async(load_root_fixture)
    assert root_fixture == 1


@test_async()
async def root_fixture_is_started_up():
    await load_fixture_async(load_root_fixture)
    assert root_fixture_started_up is True

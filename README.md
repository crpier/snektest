# `snektest`

## How to use
Like in `pytest`, try to not run code at import time.

## Upcoming features
- Support for individual tests/test classes
- Show more stuff in the traceback

## Potential features
- A `None` scope that lets you load a fixture multiple times in the same test

## Design
Need to take a stance on naming:
- Must tests and test files start with `test_`?

## Misc TODOs
- Create a test that proves that if a test fails, the next tests are run
- Would it make sense to have no more functions that return `None`?

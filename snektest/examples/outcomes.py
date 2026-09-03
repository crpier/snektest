"""Skip unavailable environments and track known assertion defects."""

import os

from snektest import assert_eq, skip, test


@test(mark="fast")
def test_optional_payment_service() -> None:
    """Skip when this machine has no configured payment service."""
    if os.environ.get("PAYMENTS_URL") is None:
        skip("PAYMENTS_URL is not configured")


@test(mark="fast", xfail="comparison normalization is not fixed yet")
def test_known_comparison_defect() -> None:
    """Keep a known defect visible until its assertion starts passing."""
    assert_eq("snektest", "SNEKTEST")

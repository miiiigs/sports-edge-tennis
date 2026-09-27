from __future__ import annotations

import pytest

from sports_edge_tennis.live import provider_failures as pf


@pytest.mark.parametrize(
    "kind",
    ["HTTP_429", "TIMEOUT", "NETWORK", "HTTP_500", "HTTP_502", "HTTP_503", "HTTP_599"],
)
def test_availability_failures_are_failover_eligible(kind: str) -> None:
    classified = pf.classify_provider_failure(kind)
    assert classified.failover_eligible is True
    assert pf.is_failover_eligible(kind) is True


def test_rate_limit_class() -> None:
    assert (
        pf.classify_provider_failure("http_429").failure_class
        is pf.ProviderFailureClass.RATE_LIMIT
    )


@pytest.mark.parametrize(
    "kind,expected",
    [
        ("HTTP_401", pf.ProviderFailureClass.AUTH),
        ("HTTP_403", pf.ProviderFailureClass.FORBIDDEN),
        ("PARSE", pf.ProviderFailureClass.CONTRACT),
        ("PROVIDER_ERROR", pf.ProviderFailureClass.CONTRACT),
        ("HTTP_404", pf.ProviderFailureClass.CLIENT_ERROR),
        ("HTTP_422", pf.ProviderFailureClass.CLIENT_ERROR),
        ("SOMETHING_WEIRD", pf.ProviderFailureClass.UNKNOWN),
        ("", pf.ProviderFailureClass.UNKNOWN),
    ],
)
def test_non_availability_failures_fail_closed(
    kind: str, expected: pf.ProviderFailureClass
) -> None:
    classified = pf.classify_provider_failure(kind)
    assert classified.failure_class is expected
    assert classified.failover_eligible is False
    assert pf.is_failover_eligible(kind) is False


def test_http_5xx_is_matched_only_for_real_status_codes() -> None:
    assert pf.classify_provider_failure("HTTP_5XX").failover_eligible is False
    assert pf.classify_provider_failure("HTTP_50").failover_eligible is False

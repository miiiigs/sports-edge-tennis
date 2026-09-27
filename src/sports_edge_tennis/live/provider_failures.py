"""Central classification of live provider failures.

This module is the single authority deciding whether a provider failure is
eligible for automatic failover. Only explicitly recognized, non-deterministic
availability failures are eligible:

* rate limiting (HTTP 429)
* transport timeouts
* network/connection failures
* provider 5xx server errors

Every other failure -- authentication (401), authorization (403), malformed or
schema-invalid payloads, unsupported response semantics, provider contract
errors, and unknown exceptions -- fails closed. Treating those as provider
downtime would mask defects in our own code, credentials, or provider contract.

The classifier is intentionally pure: it maps a stable error ``kind`` string to
a classification. It performs no I/O and never sees credentials.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ProviderFailureClass(str, Enum):
    RATE_LIMIT = "RATE_LIMIT"
    TIMEOUT = "TIMEOUT"
    NETWORK = "NETWORK"
    SERVER_ERROR = "SERVER_ERROR"
    AUTH = "AUTH"
    FORBIDDEN = "FORBIDDEN"
    CONTRACT = "CONTRACT"
    CLIENT_ERROR = "CLIENT_ERROR"
    UNKNOWN = "UNKNOWN"


# Only these classes may open the circuit and switch the provider pair.
FAILOVER_ELIGIBLE_CLASSES = frozenset(
    {
        ProviderFailureClass.RATE_LIMIT,
        ProviderFailureClass.TIMEOUT,
        ProviderFailureClass.NETWORK,
        ProviderFailureClass.SERVER_ERROR,
    }
)


@dataclass(frozen=True)
class ClassifiedProviderFailure:
    """Result of classifying one provider failure kind."""

    failure_class: ProviderFailureClass
    failover_eligible: bool
    kind: str

    @property
    def label(self) -> str:
        return self.failure_class.value


def _classify_http_status(status: int) -> ProviderFailureClass:
    if status == 429:
        return ProviderFailureClass.RATE_LIMIT
    if status == 401:
        return ProviderFailureClass.AUTH
    if status == 403:
        return ProviderFailureClass.FORBIDDEN
    if 500 <= status <= 599:
        return ProviderFailureClass.SERVER_ERROR
    if 400 <= status <= 499:
        return ProviderFailureClass.CLIENT_ERROR
    return ProviderFailureClass.UNKNOWN


def _map_kind(normalized: str) -> ProviderFailureClass:
    if not normalized:
        return ProviderFailureClass.UNKNOWN
    if normalized == "TIMEOUT":
        return ProviderFailureClass.TIMEOUT
    if normalized == "NETWORK":
        return ProviderFailureClass.NETWORK
    if normalized == "PARSE":
        # Malformed JSON / schema contract violations fail closed.
        return ProviderFailureClass.CONTRACT
    if normalized == "PROVIDER_ERROR":
        # The provider answered but reported failure; this is an
        # unsupported-response-semantics / contract condition, not recognized
        # temporary downtime, so it fails closed.
        return ProviderFailureClass.CONTRACT
    if normalized.startswith("HTTP_"):
        remainder = normalized[len("HTTP_") :]
        if remainder.isdigit():
            return _classify_http_status(int(remainder))
    return ProviderFailureClass.UNKNOWN


def classify_provider_failure(kind: str) -> ClassifiedProviderFailure:
    """Classify one provider failure ``kind`` (e.g. ``HTTP_429``)."""
    normalized = str(kind or "").strip().upper()
    failure_class = _map_kind(normalized)
    return ClassifiedProviderFailure(
        failure_class=failure_class,
        failover_eligible=failure_class in FAILOVER_ELIGIBLE_CLASSES,
        kind=normalized,
    )


def is_failover_eligible(kind: str) -> bool:
    return classify_provider_failure(kind).failover_eligible


__all__ = [
    "ClassifiedProviderFailure",
    "FAILOVER_ELIGIBLE_CLASSES",
    "ProviderFailureClass",
    "classify_provider_failure",
    "is_failover_eligible",
]

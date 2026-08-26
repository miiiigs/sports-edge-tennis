from datetime import UTC, datetime
from decimal import Decimal

from sports_edge_tennis.data.hashing import canonical_hash_payload, source_hash


def test_source_hash_is_order_independent():
    left = source_hash(
        "SACKMANN",
        {
            "winner_name": "Rafael Nadal",
            "loser_name": "Novak Djokovic",
            "best_of": 3,
        },
    )
    right = source_hash(
        "SACKMANN",
        {
            "best_of": 3,
            "loser_name": "Novak Djokovic",
            "winner_name": "Rafael Nadal",
        },
    )
    assert left == right


def test_source_hash_normalizes_unicode_whitespace_decimal_and_timestamps():
    payload = canonical_hash_payload(
        "TENNIS_DATA",
        {
            "alias": "  Nadal\u00a0R.\u3000",
            "price": Decimal("2.5000"),
            "observed_at": datetime(2026, 8, 24, 5, 30, tzinfo=UTC),
            "nullable": None,
        },
    )
    assert payload["fields"] == [
        ["alias", "Nadal R."],
        ["nullable", None],
        ["observed_at", "2026-08-24T05:30:00.000000Z"],
        ["price", "2.5"],
    ]

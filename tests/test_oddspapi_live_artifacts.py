from __future__ import annotations

import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_ODDS_ARTIFACT = (
    REPO_ROOT
    / "ops"
    / "validation"
    / "oddspapi-live-probe-20260922-fixture-odds.json"
)


def _artifact() -> dict:
    return json.loads(FIXTURE_ODDS_ARTIFACT.read_text(encoding="utf-8"))


def test_fixture_odds_artifact_pins_real_response_shape() -> None:
    artifact = _artifact()
    assert artifact["bulk_http_status"] == 400
    assert artifact["fixture_http_status"] == 200
    assert artifact["session_billable_requests"] == 2
    assert artifact["returned_bookmakers"] == ["1xbet", "pinnacle+30"]
    assert artifact["candidate_match_winner_market"] == {
        "bookmaker": "1xbet",
        "market_id": "121",
        "outcome_ids": ["121", "122"],
        "participant_1_id": "498580",
        "participant_1_maps_to": "121",
        "participant_2_id": "74333",
        "participant_2_maps_to": "122",
        "status": "CURRENT_ASSUMPTION_VERIFIED_BY_STRUCTURE",
    }
    assert artifact["observed_market_ids"]["121"] == 2
    assert artifact["observed_outcome_ids"]["121"] == 2
    assert artifact["observed_outcome_ids"]["122"] == 2


def test_fixture_odds_artifact_pins_timestamp_and_state_fields() -> None:
    sample = _artifact()["sanitized_odds_sample"]
    pinnacle = sample["bookmakerOdds"]["pinnacle+30"]
    xbet = sample["bookmakerOdds"]["1xbet"]

    assert pinnacle["bookmakerIsActive"] is True
    assert pinnacle["suspended"] is False
    assert pinnacle["match_winner"]["marketActive"] is False
    assert pinnacle["match_winner"]["outcomes"]["121"]["active"] is False
    assert pinnacle["match_winner"]["outcomes"]["121"]["price"] > 1
    assert pinnacle["match_winner"]["outcomes"]["121"]["bookmakerChangedAt"]
    assert pinnacle["match_winner"]["outcomes"]["121"]["changedAt"]

    assert xbet["bookmakerIsActive"] is True
    assert xbet["suspended"] is True
    assert xbet["match_winner"]["outcomes"]["121"]["price"] > 1
    assert xbet["match_winner"]["outcomes"]["121"]["changedAt"]


def test_fixture_odds_artifact_contains_no_secret_material() -> None:
    text = FIXTURE_ODDS_ARTIFACT.read_text(encoding="utf-8")
    assert "apiKey" not in text
    assert "ODDSPAPI_API_KEY" not in text
    assert "Authorization" not in text

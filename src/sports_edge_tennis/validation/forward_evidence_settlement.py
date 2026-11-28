"""Inactive append-only settlement and census candidate.

Official source identity/orientation and bookmaker grading are inputs to verify
externally. No provider polling, authority assertion or wager is performed here.
"""
from __future__ import annotations

import json

from sports_edge_tennis.validation.prospective_policy_registry import CHECKPOINTS, MIN_CALENDAR_DAYS, MIN_FIRST_ISSUED_SETTLED

from sports_edge_tennis.validation.forward_evidence_outbox import EvidenceConflict, digest

OUTCOMES = {"COMPLETED", "RETIRED", "WALKOVER", "DEFAULTED", "CANCELLED", "UNRESOLVED"}
GRADES = {"WON", "LOST", "VOID", "CANCELLED", "UNRESOLVED", "NO_SELECTION"}


def _record(payload):
    original = json.loads(payload)
    fixture = json.loads(original["fixture_json"])
    participants = (fixture["player_1_id"], fixture["player_2_id"])
    if "candidate_decision" in original or original.get("classification") == "SYNTHETIC_REHEARSAL_ONLY":
        from sports_edge_tennis.validation.corrected_rehearsal_persistence import PREFIX
        from sports_edge_tennis.validation.prospective_policy_registry import POLICIES
        candidate = original.get("candidate")
        if candidate not in ("B", "C", "D"):
            raise EvidenceConflict("unknown corrected candidate")
        policy = POLICIES[candidate]
        decision = original.get("candidate_decision")
        if (original.get("classification") != "SYNTHETIC_REHEARSAL_ONLY"
                or original.get("experiment_version") != PREFIX + policy.experiment_id
                or original.get("eligible_for_prospective_evidence") is not False
                or original.get("can_promote") is not False
                or not isinstance(decision, dict)
                or decision.get("experiment_id") != policy.experiment_id
                or decision.get("threshold") != policy.threshold
                or decision.get("status") not in ("SELECTED", "BELOW_FROZEN_THRESHOLD",
                                                   "MISSING", "ABSTAINED")):
            raise EvidenceConflict("invalid synthetic candidate evidence")
        side = decision.get("side")
        if (decision["status"] == "SELECTED") != (type(side) is int and side in (1, 2)):
            raise EvidenceConflict("inconsistent candidate selection")
        if decision["status"] != "SELECTED" and side is not None:
            raise EvidenceConflict("unselected candidate carries a side")
    else:
        manager = json.loads(original["manager_json"][0])
        side = manager.get("selected_side")
    if (side is not None and (type(side) is not int or side not in (1, 2))
            or any(type(p) is not int or p <= 0 for p in participants)
            or participants[0] == participants[1]):
        raise EvidenceConflict("invalid original orientation")
    return participants, participants[side-1] if side is not None else None


def sporting_grade(payload, *, outcome_kind, source_participants, winner_player_id):
    participants, selected = _record(payload)
    if (outcome_kind not in OUTCOMES or len(source_participants) != 2
            or any(type(p) is not int for p in source_participants)
            or set(source_participants) != set(participants)):
        raise EvidenceConflict("unmatched sporting result identity")
    if winner_player_id is not None and (
            type(winner_player_id) is not int or winner_player_id not in participants):
        raise EvidenceConflict("winner outside original fixture")
    if outcome_kind == "COMPLETED" and winner_player_id is None:
        raise EvidenceConflict("completed result without winner")
    if outcome_kind in ("CANCELLED", "UNRESOLVED") and winner_player_id is not None:
        raise EvidenceConflict("unresolved or cancelled result with winner")
    if selected is None:
        return "NO_SELECTION"
    if outcome_kind == "COMPLETED":
        return "WON" if selected == winner_player_id else "LOST"
    # Sporting accuracy cohort intentionally excludes non-completed matches.
    # Preserve actual outcome kind and winner in evidence, never infer a market grade.
    return {"CANCELLED":"CANCELLED", "UNRESOLVED":"UNRESOLVED"}.get(outcome_kind, "VOID")


def append_settlement(conn, version, match_id, *, pathway, market_key, evidence_key,
                      evidence_bytes, supersedes_id=None, approved_bookmaker_rules=None):
    """Explicit source evidence with optimistic predecessor guard.

    Source adapters must authenticate official/bookmaker data before calling.
    Bookmaker grading is accepted only as an explicitly supplied market grade
    under an explicitly approved bookmaker/rule/reference, never inferred from a retirement.
    An omitted or empty registry permits no bookmaker grading.
    """
    if not conn.autocommit or pathway not in ("SPORTING", "BOOKMAKER"):
        raise ValueError("fresh autocommit connection and supported pathway required")
    if not isinstance(evidence_bytes, bytes):
        raise ValueError("exact source evidence bytes required")
    evidence = json.loads(evidence_bytes)
    kind = evidence["outcome_kind"]
    if kind not in OUTCOMES or not evidence.get("source_reference"):
        raise ValueError("outcome kind and source reference required")
    with conn.transaction():
        row = conn.execute(
            """SELECT payload,payload_sha256 FROM forward_originals
               WHERE experiment_version=%s AND match_id=%s FOR UPDATE""",
            (version,match_id),
        ).fetchone()
        if row is None or digest(bytes(row[0])) != row[1]:
            raise EvidenceConflict("missing or damaged original")
        payload = bytes(row[0])
        if pathway == "SPORTING":
            if market_key != "official_match_winner":
                raise ValueError("sporting pathway requires official_match_winner")
            grade = sporting_grade(payload, outcome_kind=kind,
                                   source_participants=evidence["source_participants"],
                                   winner_player_id=evidence.get("winner_player_id"))
        else:
            participants, selected = _record(payload)
            bookmaker, rule = evidence.get("bookmaker"), evidence.get("rule_version")
            rule_reference = evidence.get("rule_reference")
            if (not isinstance(bookmaker, str) or not bookmaker.strip()
                    or not isinstance(rule, str) or not rule.strip()
                    or not isinstance(rule_reference, str) or not rule_reference.strip()
                    or not approved_bookmaker_rules
                    or approved_bookmaker_rules.get((bookmaker, rule)) != rule_reference):
                raise EvidenceConflict("bookmaker rule not explicitly approved")
            source_participants = evidence.get("source_participants")
            if (not evidence.get("market_reference")
                    or evidence["market_reference"] != market_key
                    or not isinstance(source_participants, list)
                    or len(source_participants) != 2
                    or any(type(p) is not int for p in source_participants)
                    or set(source_participants) != set(participants)
                    or evidence.get("selection_player_id") != selected):
                raise EvidenceConflict("unmatched bookmaker market or rule provenance")
            if json.loads(payload).get("classification") == "SYNTHETIC_REHEARSAL_ONLY":
                raise EvidenceConflict("synthetic rehearsal cannot grade bookmaker evidence")
            grade = evidence["bookmaker_grade"]
            if grade not in GRADES or (selected is None) != (grade == "NO_SELECTION"):
                raise ValueError("invalid explicit bookmaker grade")
        existing = conn.execute(
            """SELECT settlement_id,evidence,supersedes_id FROM forward_settlement_events
               WHERE experiment_version=%s AND match_id=%s AND pathway=%s
               AND market_key=%s AND evidence_key=%s""",
            (version,match_id,pathway,market_key,evidence_key),
        ).fetchone()
        if existing:
            if bytes(existing[1]) != evidence_bytes or existing[2] != supersedes_id:
                raise EvidenceConflict("conflicting settlement retry")
            return existing[0]
        previous = conn.execute(
            """SELECT settlement_id FROM forward_settlement_events
               WHERE experiment_version=%s AND match_id=%s AND pathway=%s AND market_key=%s
               ORDER BY settlement_id DESC LIMIT 1""",
            (version,match_id,pathway,market_key),
        ).fetchone()
        if supersedes_id != (previous[0] if previous else None):
            raise EvidenceConflict("correction must name current predecessor")
        return conn.execute(
            """INSERT INTO forward_settlement_events
               (experiment_version,match_id,pathway,market_key,evidence_key,supersedes_id,
                status,outcome_kind,evidence,evidence_sha256)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING settlement_id""",
            (version,match_id,pathway,market_key,evidence_key,supersedes_id,
             grade,kind,evidence_bytes,digest(evidence_bytes)),
        ).fetchone()[0]


def record_expected_fixture(conn, version, match_id, *, expected_at, source_bytes):
    """Record a predeclared candidate fixture. Missing means no original exists."""
    if not conn.autocommit or not isinstance(source_bytes, bytes) or not source_bytes:
        raise ValueError("explicit census evidence and fresh connection required")
    with conn.transaction():
        conn.execute(
            """INSERT INTO forward_fixture_expectations VALUES (%s,%s,%s,%s,%s)
               ON CONFLICT DO NOTHING""",
            (version,match_id,expected_at,source_bytes,digest(source_bytes)),
        )
        prior = conn.execute(
            """SELECT expected_at,source_evidence FROM forward_fixture_expectations
               WHERE experiment_version=%s AND match_id=%s""", (version,match_id),
        ).fetchone()
        if prior[0] != expected_at or bytes(prior[1]) != source_bytes:
            raise EvidenceConflict("conflicting fixture census")


def reconcile_candidate(conn, version):
    """Descriptive counts only, never independent prospective acceptance."""
    expected, missing = conn.execute(
        """SELECT count(*), count(*) FILTER (WHERE o.match_id IS NULL)
           FROM forward_fixture_expectations e LEFT JOIN forward_originals o
           USING (experiment_version,match_id) WHERE e.experiment_version=%s""",
        (version,),
    ).fetchone()
    rows = conn.execute(
        """SELECT o.payload,o.payload_sha256,
            (SELECT state FROM forward_delivery_events d WHERE d.experiment_version=o.experiment_version
             AND d.match_id=o.match_id AND channel='ARCHIVE' ORDER BY event_id DESC LIMIT 1),
            (SELECT state FROM forward_delivery_events d WHERE d.experiment_version=o.experiment_version
             AND d.match_id=o.match_id AND channel='TELEGRAM' ORDER BY event_id DESC LIMIT 1),
            (SELECT status FROM forward_settlement_events s WHERE s.experiment_version=o.experiment_version
             AND s.match_id=o.match_id AND pathway='SPORTING' ORDER BY settlement_id DESC LIMIT 1)
           FROM forward_originals o WHERE experiment_version=%s""", (version,),
    ).fetchall()
    # Corrupted evidence is an operational failure, not a graded selection.
    valid = [row for row in rows if digest(bytes(row[0])) == row[1]]
    wins = sum(row[4] == "WON" for row in valid)
    losses = sum(row[4] == "LOST" for row in valid)
    settled_selections = wins + losses
    return {
        "expected":expected, "missing":missing, "committed":len(rows),
        "archived":sum(row[2] in ("DELIVERED","RECONCILED") for row in rows),
        "published":sum(row[3] in ("DELIVERED","RECONCILED") for row in rows),
        "uncertain":sum("DELIVERY_UNCERTAIN" in row[2:4] for row in rows),
        "settled":sum(row[4] in ("WON","LOST","VOID","CANCELLED","NO_SELECTION") for row in rows),
        "unsettled":sum(row[4] in (None,"UNRESOLVED") for row in rows),
        "inconsistent_digest":sum(digest(bytes(row[0])) != row[1] for row in rows),
        "sporting_wins":wins, "sporting_losses":losses,
        "descriptive_accuracy":wins/(wins+losses) if wins+losses else None,
        "checkpoint_target":300, "independent_qualified_settled":0,
        "settled_selections_descriptive": settled_selections,
        "checkpoint_progress": [
            {"target": target, "descriptive_count": settled_selections,
             "descriptive_count_reached": settled_selections >= target,
             "independent_qualified_count": 0, "qualified": False}
            for target in CHECKPOINTS
        ],
        "minimum_calendar_days": MIN_CALENDAR_DAYS,
        "minimum_first_issued_settled": MIN_FIRST_ISSUED_SETTLED,
        "calendar_duration_verified": False,
        "external_denominator_verified": False,
        "checkpoint_qualified":False, "activation_eligible":False,
    }

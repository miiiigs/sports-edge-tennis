"""Offline, permission-gated competitor receipt and scoring extension.

No web requests, production writes or automatic settlement. The external
operator supplies authorized observations and the independently frozen canonical
fixture census. Receipt clock and rights are assertions, not independent proof.
"""
from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

from sports_edge_tennis.validation.competitor_forward_audit import utc, _mean, _logloss

SOURCES = ("pronocast", "tennispredictions_ai", "bananapoint", "tennisbrain",
           "courtcruncher", "tennisforesight", "sports_edge_original", "bookmaker")
CLASSES = ("market_independent", "market_assisted", "bookmaker", "unknown")
CUTOFF = timedelta(minutes=30)
SCHEMA = """
CREATE TABLE IF NOT EXISTS observations (
  seq INTEGER PRIMARY KEY, source TEXT NOT NULL, match_id TEXT NOT NULL,
  digest TEXT NOT NULL, received TEXT NOT NULL, payload BLOB NOT NULL,
  fixture BLOB NOT NULL, grant_ref TEXT NOT NULL,
  UNIQUE(source, match_id, digest)
);
CREATE TRIGGER IF NOT EXISTS observations_no_update BEFORE UPDATE ON observations
BEGIN SELECT RAISE(ABORT,'append only'); END;
CREATE TRIGGER IF NOT EXISTS observations_no_delete BEFORE DELETE ON observations
BEGIN SELECT RAISE(ABORT,'append only'); END;
"""


def _now():
    return datetime.now(timezone.utc)


def _nonempty(value):
    return isinstance(value, str) and bool(value.strip())


def _decode(raw):
    def unique(pairs):
        result = {}
        for k, v in pairs:
            if k in result:
                raise ValueError("duplicate JSON key")
            result[k] = v
        return result

    def bad(value):
        raise ValueError("nonfinite number: " + value)

    if not isinstance(raw, bytes) or not 0 < len(raw) <= 1024 * 1024:
        raise ValueError("invalid payload")
    record = json.loads(raw, object_pairs_hook=unique, parse_constant=bad)
    if not isinstance(record, dict):
        raise ValueError("expected object")
    return record


def _url(value):
    u = urlsplit(value)
    if u.scheme != "https" or not u.hostname or u.username or u.password:
        raise ValueError("invalid source URL")
    return u.hostname.lower()


def _fixture(f):
    if (f.get("tour") not in ("ATP_TOUR", "CHALLENGER")
            or f.get("sex") != "M" or f.get("discipline") != "SINGLES"
            or f.get("draw") not in ("MAIN_DRAW", "QUALIFYING")
            or f.get("eligible") is not True):
        raise ValueError("outside canonical eligible population")
    for key in ("match_id", "player1_id", "player2_id", "tournament_id", "census_ref"):
        if not _nonempty(f.get(key)):
            raise ValueError("missing canonical " + key)
    if f["player1_id"] == f["player2_id"]:
        raise ValueError("identical participants")
    return utc(f["frozen_start_utc"])


def _validate(record, f, grant, received):
    start = _fixture(f)
    source = record.get("source")
    if source not in SOURCES or record.get("match_id") != f["match_id"]:
        raise ValueError("unknown source or canonical match")
    if (grant.get("source") != source or grant.get("commercial_research") is not True
            or grant.get("prospective_capture") is not True
            or not _nonempty(grant.get("reference"))
            or not utc(grant["valid_from_utc"]) <= received < utc(grant["valid_until_utc"])):
        raise ValueError("no active permission")
    if _url(record["source_url"]) not in grant.get("allowed_hosts", []):
        raise ValueError("source host outside permission")
    if record.get("market") != "MATCH_WINNER" or record.get("market_class") not in CLASSES:
        raise ValueError("invalid market")
    if (source == "bookmaker") != (record["market_class"] == "bookmaker"):
        raise ValueError("bookmaker class mismatch")
    a, b = record.get("player1_id"), record.get("player2_id")
    if (a, b) not in ((f["player1_id"], f["player2_id"]), (f["player2_id"], f["player1_id"])):
        raise ValueError("participant mismatch")
    if record.get("predicted_winner_id") not in (a, b):
        raise ValueError("invalid winner")
    p = record.get("p_player1")
    if p is not None:
        if type(p) not in (int, float) or not math.isfinite(p) or not 0 < p < 1:
            raise ValueError("invalid probability")
        if p != .5 and record["predicted_winner_id"] != (a if p > .5 else b):
            raise ValueError("winner probability disagreement")
    if record.get("probability_kind") != ("win_probability" if p is not None else "unavailable"):
        raise ValueError("confidence is not a probability")
    pub = record.get("published_at_utc")
    if pub is not None and utc(pub) > received:
        raise ValueError("publication after receipt")
    if received > start - CUTOFF:
        raise ValueError("late observation")
    if utc(f["census_observed_at_utc"]) > received:
        raise ValueError("fixture not independently visible at receipt")
    digest = record.get("evidence_sha256")
    if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
        raise ValueError("missing permitted evidence digest")
    if not _nonempty(record.get("evidence_ref")):
        raise ValueError("missing evidence reference")
    if record.get("model_version") is not None and not _nonempty(record["model_version"]):
        raise ValueError("invalid model version")
    if record.get("outcomes_available") is not False:
        raise ValueError("outcome contamination")


class ObservationStore:
    def __init__(self, path):
        self.path = Path(path)
        if not self.path.is_absolute():
            raise ValueError("absolute local storage path required")
        with closing(self._connect()) as conn, conn:
            conn.executescript(SCHEMA)

    def _connect(self):
        conn = sqlite3.connect(self.path, timeout=5)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=FULL")
        return conn

    def receive(self, raw, fixture, grant):
        received = _now()
        record = _decode(raw)
        digest = hashlib.sha256(raw).hexdigest()
        source, mid = record["source"], record["match_id"]
        with closing(self._connect()) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            prior = conn.execute(
                "SELECT seq,received,payload FROM observations WHERE source=? AND match_id=? AND digest=?",
                (source, mid, digest)).fetchone()
            if prior:
                if prior[2] != raw:
                    raise ValueError("digest conflict")
                return {"seq": prior[0], "received_at_utc": prior[1], "duplicate": True}
            _validate(record, fixture, grant, received)
            previous = conn.execute(
                "SELECT received,fixture FROM observations WHERE source=? AND match_id=? ORDER BY seq DESC LIMIT 1",
                (source, mid)).fetchone()
            frozen = json.dumps(fixture, sort_keys=True).encode()
            if previous and (received < utc(previous[0]) or previous[1] != frozen):
                raise ValueError("clock regression or frozen fixture drift")
            cur = conn.execute(
                "INSERT INTO observations(source,match_id,digest,received,payload,fixture,grant_ref) VALUES(?,?,?,?,?,?,?)",
                (source, mid, digest, received.isoformat(), raw, frozen, grant["reference"]))
            seq = cur.lastrowid
        return {"seq": seq, "received_at_utc": received.isoformat(), "duplicate": False,
                "revision": previous is not None, "payload_sha256": digest,
                "independent_clock_verified": False}

    def first(self):
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT source,match_id,received,payload,fixture,digest,grant_ref FROM observations "
                "WHERE seq IN (SELECT min(seq) FROM observations GROUP BY source,match_id) ORDER BY seq"
            ).fetchall()
        result = []
        for source, mid, received, raw, fixture, digest, grant_ref in rows:
            if hashlib.sha256(raw).hexdigest() != digest:
                raise ValueError("stored payload digest mismatch")
            result.append({"source": source, "match_id": mid, "received_at_utc": received,
                           "forecast": _decode(raw), "fixture": json.loads(fixture),
                           "grant_ref": grant_ref})
        return result


def _metrics(points):
    probs = [(p, y) for _, p, y in points if p is not None]
    calibration = []
    for low in (0, .1, .2, .3, .4, .5, .6, .7, .8, .9):
        band = [(p, y) for p, y in probs if round(p * 10, 12) >= low * 10
                and round(p * 10, 12) < (low + .1) * 10]
        calibration.append({"lower": low, "n": len(band), "mean_p": _mean([p for p,y in band]),
                            "observed": _mean([y for p,y in band])})
    return {"settled": len(points), "correct": sum(hit for hit,_,_ in points),
            "accuracy": _mean([hit for hit,_,_ in points]), "probability_n": len(probs),
            "brier": _mean([(p-y)**2 for p,y in probs]),
            "log_loss": _mean([_logloss(p,y) for p,y in probs]), "calibration": calibration}


def score(fixtures, first, settlements, *, as_of, sources=SOURCES, baseline="sports_edge_original"):
    """Descriptive evaluation of first observations only; never select revisions.

    Settlement input must come from the existing official result contract.
    This function validates shape/timing, not provider authenticity. Missing
    actual start, result evidence or completed outcome prevents grading.
    """
    now = utc(as_of)
    universe = {}
    for f in fixtures:
        _fixture(f)
        mid = f["match_id"]
        if mid in universe:
            raise ValueError("duplicate census identity")
        if utc(f["census_observed_at_utc"]) > now:
            raise ValueError("future census")
        universe[mid] = f
    observed = {}
    for row in first:
        mid, source = row["match_id"], row["source"]
        key = source, mid
        if key in observed or mid not in universe or source not in sources:
            raise ValueError("duplicate or unknown observation")
        if row["fixture"] != universe[mid]:
            raise ValueError("census drift")
        received = utc(row["received_at_utc"])
        if received > now or received > _fixture(universe[mid]) - CUTOFF:
            raise ValueError("future or late observation")
        observed[key] = row
    settled = {}
    for mid, s in settlements.items():
        if mid not in universe:
            raise ValueError("unknown settlement identity")
        if s.get("status") != "COMPLETED":
            continue
        f = universe[mid]
        if (s.get("winner_id") not in (f["player1_id"], f["player2_id"])
                or not _nonempty(s.get("official_evidence_ref")) or not s.get("actual_start_utc")):
            continue
        actual = utc(s["actual_start_utc"])
        if not actual < utc(s["result_observed_at_utc"]) <= now:
            continue
        settled[mid] = (s["winner_id"], actual)

    def point(row, mid):
        winner, actual = settled[mid]
        if utc(row["received_at_utc"]) > actual - CUTOFF:
            return None
        rec = row["forecast"]
        p = rec.get("p_player1")
        if p is not None and rec["player1_id"] != universe[mid]["player1_id"]:
            p = 1-p
        return (int(rec["predicted_winner_id"] == winner), p,
                int(winner == universe[mid]["player1_id"]))

    reports = []
    for tier in ("ALL", "ATP_TOUR", "CHALLENGER"):
        ids = {mid for mid,f in universe.items() if tier == "ALL" or f["tour"] == tier}
        for source in sources:
            rows = {mid:r for (src,mid),r in observed.items() if src == source and mid in ids}
            for cls in CLASSES:
                selected = {mid:r for mid,r in rows.items() if r["forecast"]["market_class"] == cls}
                values = {}
                for mid,r in selected.items():
                    if mid in settled:
                        p = point(r,mid)
                        if p is not None:
                            values[mid] = p
                common, base_values = [], []
                for mid,p in sorted(values.items()):
                    other = observed.get((baseline,mid))
                    if source != baseline and other:
                        bp = point(other,mid)
                        if bp is not None:
                            common.append(p)
                            base_values.append(bp)
                reports.append({"tour":tier,"source":source,"market_class":cls,
                                "eligible":len(ids),"captured":len(selected),
                                "missing":len(ids)-len(selected),
                                "coverage":len(selected)/len(ids) if ids else None,
                                "own":_metrics(list(values.values())),
                                "common_source":_metrics(common),"common_baseline":_metrics(base_values)})
    return {"reports":reports,"independent_clock_verified":False,
            "official_settlement_authenticity_verified":False,
            "activation_eligible":False}

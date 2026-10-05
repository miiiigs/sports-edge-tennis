"""Offline Model Pick probability-calibration evaluation (research only).

Reads an aligned-Model-Pick ledger (JSON produced from persisted live production
evidence) and evaluates raw vs Platt vs Isotonic calibration with a chronological
train/validation/holdout split, separately for Manager and Wide-Spread.

It writes a small research artifact and NEVER touches live selection, stored
probabilities, thresholds, Manager/Market/Veto/staking, or settlement.

Usage:
    python ops/calibration_evaluation.py --ledger path/to/ledger.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import subprocess
import sys
from datetime import date

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from sports_edge_tennis.performance.calibration import (  # noqa: E402
    Sample,
    brier,
    calibration_slope_intercept,
    chronological_split,
    expected_calibration_error,
    fit_isotonic,
    fit_platt,
    log_loss,
    reliability_bins,
)

REPO = pathlib.Path(__file__).resolve().parents[1]
ARTIFACT = REPO / "artifacts" / "calibration" / "model_pick_calibration.json"
MAPPING_RAWS = (0.60, 0.65, 0.70, 0.75, 0.80, 0.85, 0.90, 0.95)


def _f(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _git_sha() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True
        ).stdout.strip()
    except Exception:
        return "unknown"


def _load(ledger_path: str) -> list[dict]:
    rows = json.load(open(ledger_path, encoding="utf-8-sig"))["data"]
    out = []
    for r in rows:
        raw = _f(r["aligned_probability"])
        result = r["result_status"]
        if raw is None or result not in ("WON", "LOST"):
            continue
        out.append(
            {
                "date": r["picked_at"][:10],
                "seq": r["picked_at"],
                "day": r["manila_date"],
                "match_id": r["match_id"],
                "raw": raw,
                "y": 1 if result == "WON" else 0,
                "ptype": r["aligned_pick_type"],
                "tier": r["tier"],
                "surface": r["surface"],
                "a1": _f(r["a1_prob"]),
                "a2": _f(r["a2_prob"]),
                "a3": r["a3_status"],
                "vb": bool(r["is_value_bet"]),
                "odds": _f(r.get("market_decimal_price")),
                "clv": _f(r.get("clv_pct")),
            }
        )
    out.sort(key=lambda z: (z["seq"], z["match_id"]))
    return out


def _pairs(samples, model=None):
    if model is None:
        return [(s.raw, s.outcome) for s in samples]
    return [(model.predict(s.raw), s.outcome) for s in samples]


def _metrics(samples, model):
    pairs = _pairs(samples, model)
    intercept, slope = calibration_slope_intercept(
        [Sample(pred, out) for pred, out in pairs]
    )
    return {
        "n": len(samples),
        "brier": brier(pairs),
        "log_loss": log_loss(pairs),
        "ece": expected_calibration_error(pairs),
        "slope": slope,
        "intercept": intercept,
        "reliability": [
            {
                "bin": b.label,
                "n": b.n,
                "avg_predicted": b.average_predicted,
                "observed": b.observed,
                "gap": b.gap,
            }
            for b in reliability_bins(pairs)
        ],
    }


def _evaluate_group(name: str, rows: list[dict]) -> dict:
    samples = [Sample(r["raw"], r["y"]) for r in rows]
    train, validation, holdout = chronological_split(samples)

    result = {
        "name": name,
        "raw": _metrics(samples, None),
        "split": {
            "train_n": len(train),
            "validation_n": len(validation),
            "holdout_n": len(holdout),
            "train_start": rows[0]["date"] if rows else None,
            "train_end": rows[max(0, len(train) - 1)]["date"] if train else None,
            "validation_end": rows[max(0, len(train) + len(validation) - 1)]["date"]
            if validation
            else None,
            "holdout_end": rows[-1]["date"] if rows else None,
        },
        "methods": {},
    }

    candidates = {}
    platt = fit_platt(train)
    isotonic = fit_isotonic(train)
    if platt is not None:
        candidates["platt"] = platt
    if isotonic is not None:
        candidates["isotonic"] = isotonic

    for method_name, model in candidates.items():
        result["methods"][method_name] = {
            "validation": _metrics(validation, model) if validation else None,
            "holdout": _metrics(holdout, model) if holdout else None,
            "params": (
                {"intercept": model.intercept, "slope": model.slope}
                if method_name == "platt"
                else {
                    "thresholds": list(model.thresholds),
                    "values": list(model.values),
                }
            ),
        }

    # Prefer the method whose holdout Brier beats raw holdout Brier; else raw.
    raw_holdout_brier = brier(_pairs(holdout, None)) if holdout else None
    best_method = "raw"
    best_brier = raw_holdout_brier
    for method_name in candidates:
        m = result["methods"][method_name]["holdout"]
        if m and m["brier"] is not None and (best_brier is None or m["brier"] < best_brier):
            best_brier = m["brier"]
            best_method = method_name
    result["best_method"] = best_method

    # Mapping table and high-confidence validation use a model fit on all data.
    if best_method == "platt":
        final_model = fit_platt(samples)
    elif best_method == "isotonic":
        final_model = fit_isotonic(samples)
    else:
        final_model = None
    result["final_model"] = best_method
    result["mapping"] = {
        f"{raw:.2f}": (
            raw if final_model is None else final_model.predict(raw)
        )
        for raw in MAPPING_RAWS
    }
    result["high_confidence"] = {}
    for threshold in (0.80, 0.85, 0.90):
        subset = [s for s in samples if s.raw >= threshold]
        if not subset:
            continue
        raw_pairs = _pairs(subset, None)
        cal_pairs = _pairs(subset, final_model)
        result["high_confidence"][f"{threshold:.2f}"] = {
            "n": len(subset),
            "observed": sum(s.outcome for s in subset) / len(subset),
            "raw_expected": sum(p for p, _ in raw_pairs) / len(subset),
            "calibrated_expected": sum(p for p, _ in cal_pairs) / len(subset),
        }

    # Daily top pick behaviour.
    by_day: dict[str, list[dict]] = {}
    for r in rows:
        by_day.setdefault(r["day"], []).append(r)
    daily_raw = []
    daily_cal = []
    preserved = 0
    days = 0
    for day, sub in by_day.items():
        if not any(r["y"] in (0, 1) for r in sub):
            continue
        days += 1
        top_raw = max(sub, key=lambda z: (z["raw"], -z["match_id"]))
        top_cal_raw = (
            top_raw["raw"] if final_model is None else final_model.predict(top_raw["raw"])
        )
        best_cal = max(
            sub,
            key=lambda z: (
                z["raw"] if final_model is None else final_model.predict(z["raw"]),
                -z["match_id"],
            ),
        )
        if best_cal["match_id"] == top_raw["match_id"]:
            preserved += 1
        daily_raw.append(top_raw["raw"])
        daily_cal.append(top_cal_raw)
    result["daily_top"] = {
        "days": days,
        "mean_raw": sum(daily_raw) / len(daily_raw) if daily_raw else None,
        "mean_calibrated": sum(daily_cal) / len(daily_cal) if daily_cal else None,
        "ranking_preserved_days": preserved,
    }

    # A1/A2 divergence (Wide-Spread focus; compute where both present).
    div_groups = {"<0.025": [], "0.025-0.05": [], "0.05-0.10": [], ">0.10": []}
    for r in rows:
        if r["a1"] is None or r["a2"] is None:
            continue
        d = abs(r["a1"] - r["a2"])
        key = "<0.025" if d < 0.025 else "0.025-0.05" if d < 0.05 else "0.05-0.10" if d < 0.10 else ">0.10"
        div_groups[key].append(r)
    result["a1_a2_divergence"] = {
        key: {
            "n": len(v),
            "observed": (sum(x["y"] for x in v) / len(v)) if v else None,
            "avg_raw": (sum(x["raw"] for x in v) / len(v)) if v else None,
        }
        for key, v in div_groups.items()
    }

    # Error correlation between A1 and A2.
    def _corr(a, b):
        if len(a) < 2:
            return None
        ma, mb = sum(a) / len(a), sum(b) / len(b)
        cov = sum((x - ma) * (y - mb) for x, y in zip(a, b))
        va = sum((x - ma) ** 2 for x in a)
        vb = sum((y - mb) ** 2 for y in b)
        return cov / (va * vb) ** 0.5 if va > 0 and vb > 0 else None

    pairs_full = [r for r in rows if r["a1"] is not None and r["a2"] is not None]
    e1 = [r["a1"] - r["y"] for r in pairs_full]
    e2 = [r["a2"] - r["y"] for r in pairs_full]
    p1 = [r["a1"] for r in pairs_full]
    p2 = [r["a2"] for r in pairs_full]
    result["a1_a2_correlation"] = {
        "n": len(pairs_full),
        "probability_correlation": _corr(p1, p2),
        "error_correlation": _corr(e1, e2),
    }

    # Tier / surface.
    def _slice(dim: str) -> dict:
        out: dict[str, dict] = {}
        for r in rows:
            key = r[dim] or "UNKNOWN"
            out.setdefault(key, []).append(r)
        return {
            k: {
                "n": len(v),
                "observed": sum(x["y"] for x in v) / len(v),
                "avg_raw": sum(x["raw"] for x in v) / len(v),
            }
            for k, v in out.items()
        }

    result["tier"] = _slice("tier")
    result["surface"] = _slice("surface")

    # Market / CLV.
    with_odds = [r for r in rows if r["odds"]]
    with_clv = [r for r in rows if r["clv"] is not None]
    result["market"] = {
        "n_with_odds": len(with_odds),
        "mean_raw_minus_implied": (
            sum(r["raw"] - 1.0 / r["odds"] for r in with_odds) / len(with_odds)
            if with_odds
            else None
        ),
        "mean_calibrated_minus_implied": (
            sum(
                (r["raw"] if final_model is None else final_model.predict(r["raw"]))
                - 1.0 / r["odds"]
                for r in with_odds
            )
            / len(with_odds)
            if with_odds
            else None
        ),
    }
    clv_buckets = {"60-70": (0.60, 0.70), "70-80": (0.70, 0.80), "80-90": (0.80, 0.90), "90+": (0.90, 1.01)}
    result["clv"] = {
        "n": len(with_clv),
        "mean": (sum(r["clv"] for r in with_clv) / len(with_clv)) if with_clv else None,
        "positive": sum(1 for r in with_clv if r["clv"] > 0),
        "negative": sum(1 for r in with_clv if r["clv"] < 0),
        "buckets": {
            k: {
                "n": len(v),
                "mean": (sum(x["clv"] for x in v) / len(v)) if v else None,
            }
            for k, (lo, hi) in clv_buckets.items()
            for v in [[r for r in with_clv if lo <= r["raw"] < hi]]
        },
    }

    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Offline Model Pick calibration evaluation")
    parser.add_argument("--ledger", required=True)
    args = parser.parse_args(argv)

    rows = _load(args.ledger)
    manager = [r for r in rows if r["ptype"] == "MANAGER_CONSENSUS"]
    wide = [r for r in rows if r["ptype"] == "A1_A2_WIDE_SPREAD"]

    artifact = {
        "generated_at": date.today().isoformat(),
        "git_sha": _git_sha(),
        "dataset": {
            "rows_scored": len(rows),
            "manager_n": len(manager),
            "wide_n": len(wide),
            "date_min": rows[0]["date"] if rows else None,
            "date_max": rows[-1]["date"] if rows else None,
            "hash": hashlib.sha256(
                "".join(
                    f"{r['match_id']}:{r['raw']:.6f}:{r['y']}\n"
                    for r in sorted(rows, key=lambda z: (z["date"], z["match_id"]))
                ).encode("utf-8")
            ).hexdigest(),
        },
        "combined": _evaluate_group("combined", rows),
        "manager": _evaluate_group("manager", manager),
        "wide": _evaluate_group("wide", wide),
        "note": (
            "Offline research artifact. Not wired into live prediction selection. "
            "Manager and Wide-Spread are evaluated separately; Beta calibration "
            "omitted (no dependency)."
        ),
    }

    ARTIFACT.parent.mkdir(parents=True, exist_ok=True)
    ARTIFACT.write_text(json.dumps(artifact, indent=2, default=str), encoding="utf-8")

    summary = {
        k: {
            "n": artifact[k]["raw"]["n"],
            "raw_brier": artifact[k]["raw"]["brier"],
            "raw_ece": artifact[k]["raw"]["ece"],
            "best_method": artifact[k]["best_method"],
            "holdout_brier": (
                artifact[k]["methods"].get(artifact[k]["best_method"], {}).get("holdout")
                or {}
            ).get("brier")
            if artifact[k]["best_method"] != "raw"
            else artifact[k]["raw"]["brier"],
        }
        for k in ("combined", "manager", "wide")
    }
    print(json.dumps(summary, indent=2))
    print(f"artifact={ARTIFACT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

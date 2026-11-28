import csv
from ops.validation.valuebetennis_export_audit_20261128 import REQUIRED, audit


def row(mid, **changes):
    return {**dict.fromkeys(REQUIRED, ""), "match_id": str(mid),
            "date": "2026-11-20 13:00:00", "tournoi_id": "1",
            "categorie": "Challenger", "genre": "atp", "surface": "HARD",
            "joueur1": "Player A", "joueur2": "Player B",
            "joueur1_id": "1", "joueur2_id": "2",
            "vainqueur_id": "1", "score": "6-4 6-3", **changes}


def write(path, rows):
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=sorted(REQUIRED), delimiter=";")
        writer.writeheader()
        writer.writerows(rows)


def test_audit_quarantines_conflicts_and_separates_competitions(tmp_path):
    f = tmp_path/"2026.csv"
    write(f,[row(1),row(1,vainqueur_id="2"),row(2),
             row(3,genre="wta"),row(4,categorie="ITF"),
             row(5,categorie="Grand Slam"),row(6,vainqueur_id="999"),
             row(7,date="2026-11-20",categorie="Challenger")])
    result = audit([f])
    assert result["counts"]["raw_rows"] == 8
    assert result["unique_match_ids"] == 7
    assert result["conflicting_duplicate_ids"] == 1
    assert result["groups"]["CHALLENGER"]["structurally_complete_rows"] == 2
    assert result["groups"]["CHALLENGER"]["date_DATE_ONLY"] == 1
    assert result["groups"]["WOMEN"]["unique_matches"] == 1
    assert result["groups"]["ITF"]["unique_matches"] == 1
    assert result["groups"]["ATP_MAIN"]["unique_matches"] == 1
    assert result["usable_for_commercial_training"] is False


def test_cross_file_exact_duplicates_and_missing_score(tmp_path):
    a,b = tmp_path/"a.csv",tmp_path/"b.csv"
    write(a,[row(1),row(2,score="")])
    write(b,[row(1)])
    result = audit([a,b])
    assert result["counts"]["duplicate_rows"] == 1
    assert result["conflicting_duplicate_ids"] == 0
    assert result["groups"]["CHALLENGER"]["MISSING_SCORE"] == 1
    assert result["groups"]["CHALLENGER"]["structurally_complete_rows"] == 1

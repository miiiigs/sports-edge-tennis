from __future__ import annotations

from datetime import date

import pytest
from replay_helpers import model_pick, run_result, utc, value_bet

from sports_edge_tennis.replay.artifacts import (
    ReplayArtifactExistsError,
    write_replay_artifact,
)
from sports_edge_tennis.replay.contracts import REPLAY_ID, REPLAY_SCHEMA_VERSION, ReplayResult
from sports_edge_tennis.replay.manifest import (
    ManifestMetadata,
    ReplayVersionSet,
    build_manifest,
    reproducibility_sha256,
)
from sports_edge_tennis.replay.serde import sha256_hex

VERSIONS = ReplayVersionSet(
    a1_config_version="a1-v1",
    a2_config_version="a2-v1",
    a3_config_version="a3-v1",
    manager_policy_version="v1",
    market_policy_version="v1",
    veto_policy_version="tennis_veto_v1",
    staking_rule_version="QUARTER_KELLY_CAP_2_V1",
)


def _artifact(tmp_path):
    pick = model_pick(match_id=1)
    bet = value_bet(match_id=1, actual_result="WIN", units=None)
    result = ReplayResult(
        replay_id=REPLAY_ID,
        runs=(run_result(1, model_picks=(pick,), value_bets=(bet,)),),
        model_picks=(pick,),
        value_bets=(bet,),
        exclusions=(),
        opportunities=(),
        provisional=True,
        provisional_label="PROVISIONAL - HISTORICAL RECOVERY SNAPSHOT NOT FROZEN",
    )
    manifest = build_manifest(
        result,
        metadata=ManifestMetadata(
            git_sha="deadbeef",
            generated_at=utc(20),
            historical_range_start=date(2026, 9, 10),
            historical_range_end=date(2026, 9, 24),
            input_snapshot_ids=("snap-a",),
        ),
        versions=VERSIONS,
        replay_schema_version=REPLAY_SCHEMA_VERSION,
    )
    return result, manifest


def test_write_artifact_is_immutable(tmp_path):
    result, manifest = _artifact(tmp_path)
    paths = write_replay_artifact(tmp_path, result=result, manifest=manifest)

    assert paths.manifest_path.exists()
    assert paths.model_picks_path.exists()
    assert paths.value_bets_path.exists()
    assert paths.exclusions_path.exists()
    assert paths.opportunities_path.exists()
    assert paths.checksums_path.exists()
    assert paths.reproducibility_sha256 == reproducibility_sha256(manifest)

    with pytest.raises(ReplayArtifactExistsError):
        write_replay_artifact(tmp_path, result=result, manifest=manifest)

    # The refused overwrite must not have altered the original artifact.
    assert paths.manifest_path.exists()


def test_checksums_match_written_files(tmp_path):
    result, manifest = _artifact(tmp_path)
    paths = write_replay_artifact(tmp_path, result=result, manifest=manifest)

    lines = paths.checksums_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 5
    for line in lines:
        digest, filename = line.split("  ", 1)
        actual = sha256_hex((paths.root / filename).read_text(encoding="utf-8"))
        assert digest == actual

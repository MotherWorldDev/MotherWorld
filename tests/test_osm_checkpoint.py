from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import sys
import uuid
from pathlib import Path

import geopandas as gpd
import pytest
from shapely.geometry import box


REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / ".cache" / "osm-runtime"))

osmium = pytest.importorskip("osmium")

from osm_checkpoint import (  # noqa: E402
    CheckpointInterrupted,
    _count_selected_stream,
    checkpointed_process_pbf,
)


@pytest.fixture
def f_workspace():
    root = REPO / ".cache" / "osm-checkpoint-tests" / uuid.uuid4().hex
    root.mkdir(parents=True)
    try:
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)


@pytest.fixture
def regions():
    return gpd.GeoDataFrame(
        {"regionId": ["eco_fixture"], "areaKm2": [1000.0]},
        geometry=[box(-1, -1, 12, 12)],
        crs="EPSG:4326",
    )


def _fixture_pbf(path: Path, *, label="initial") -> None:
    """Write a real multi-Blob PBF with nodes, ways, and multipolygon refs."""

    nodes = {
        1: (1.0, 1.0, {"amenity": " Waste_Disposal ", "name": label}),
        2: (2.0, 2.0, {}),
        3: (3.0, 2.0, {}),
        4: (3.0, 3.0, {}),
        5: (2.0, 3.0, {}),
        10: (5.0, 5.0, {}),
        11: (7.0, 5.0, {}),
        12: (7.0, 7.0, {}),
        13: (7.0, 7.0, {}),
        14: (5.0, 7.0, {}),
        15: (5.0, 7.0, {}),
        16: (5.0, 5.0, {}),
        17: (5.0, 5.0, {}),
        50: (8.0, 8.0, {}),
        51: (9.0, 8.0, {}),
        70: (10.0, 2.0, {}),
        71: (11.0, 2.0, {}),
        72: (11.0, 3.0, {}),
    }
    with osmium.SimpleWriter(str(path), bufsz=256, overwrite=True) as writer:
        for node_id, (lon, lat, tags) in nodes.items():
            writer.add_node(
                osmium.osm.mutable.Node(
                    id=node_id,
                    location=(lon, lat),
                    tags=tags,
                )
            )
        writer.add_way(
            osmium.osm.mutable.Way(
                id=10,
                nodes=[2, 3, 4, 5, 2],
                tags={"landuse": "landfill"},
            )
        )
        # An outer ring split over four ways tests relation refs and ordering.
        for way_id, refs in (
            (20, [10, 11, 12]),
            (21, [12, 13, 14]),
            (22, [14, 15, 16]),
            (23, [16, 17, 10]),
        ):
            writer.add_way(osmium.osm.mutable.Way(id=way_id, nodes=refs, tags={}))
        writer.add_way(
            osmium.osm.mutable.Way(
                id=30,
                nodes=[50, 51],
                tags={"landuse": "landfill"},
            )
        )
        # This tagged closed way has a missing node. The area builder must
        # skip it exactly as it does in the uninterrupted source pass.
        writer.add_way(
            osmium.osm.mutable.Way(
                id=40,
                nodes=[70, 71, 72, 999, 70],
                tags={"landuse": "landfill"},
            )
        )
        writer.add_relation(
            osmium.osm.mutable.Relation(
                id=100,
                members=[
                    ("w", 20, "outer"),
                    ("w", 21, "outer"),
                    ("w", 22, "outer"),
                    ("w", 23, "outer"),
                ],
                tags={"type": "multipolygon", "landuse": "landfill"},
            )
        )
        # The parent relation has no target tags. Its inclusion is required to
        # preserve the area manager's relation closure without adding a site.
        writer.add_relation(
            osmium.osm.mutable.Relation(
                id=200,
                members=[("w", 10, "outer")],
                tags={"type": "multipolygon"},
            )
        )


def _oracle(path: Path, regions: gpd.GeoDataFrame) -> dict:
    pool = osmium.io.ThreadPool(1, 1)
    return _count_selected_stream(path, regions, pool)


def _run(
    path: Path,
    regions: gpd.GeoDataFrame,
    workspace: Path,
    **kwargs,
) -> dict:
    return checkpointed_process_pbf(
        path,
        regions,
        checkpoint_root=workspace / "checkpoints",
        temp_root=workspace / "tmp",
        reserve_gib=0,
        checkpoint_blocks=1,
        **kwargs,
    )


def _assert_counts_equal(actual: dict, expected: dict) -> None:
    assert actual["candidateSiteCount"] == expected["candidateSiteCount"]
    assert actual["skippedGeometryCount"] == expected["skippedGeometryCount"]
    assert actual["countsByRegionIndex"].keys() == expected["countsByRegionIndex"].keys()
    for region_index in actual["countsByRegionIndex"]:
        assert actual["countsByRegionIndex"][region_index] == pytest.approx(
            expected["countsByRegionIndex"][region_index], rel=0, abs=1e-9
        )


@pytest.mark.parametrize("interrupted_stage", ["discovery", "ways", "nodes"])
def test_checkpoint_resume_matches_uninterrupted_fileprocessor_oracle(f_workspace, regions, interrupted_stage, monkeypatch):
    source = f_workspace / "fixture.osm.pbf"
    _fixture_pbf(source)
    oracle = _oracle(source, regions)
    assert oracle["candidateSiteCount"] == 4
    assert oracle["countsByRegionIndex"]["0"]["landfill"] == 3
    assert oracle["countsByRegionIndex"]["0"]["waste_disposal"] == 1
    assert oracle["countsByRegionIndex"]["0"]["landfillAreaKm2"] > 0
    blocks = list(__import__("osm_checkpoint").iter_pbf_blocks(source))
    assert len(blocks) >= 3

    target_ordinal = 1
    if interrupted_stage in {"ways", "nodes"}:
        entity = osmium.osm.WAY if interrupted_stage == "ways" else osmium.osm.NODE
        for block in blocks[1:]:
            stream = osmium.FileProcessor(osmium.io.FileBuffer(blocks[0].data + block.data, "pbf"), entities=entity)
            if any(True for _ in stream):
                target_ordinal = block.ordinal
                break

    def after_first_commit(stage, ordinal, committed):
        if stage == interrupted_stage and committed and ordinal == target_ordinal:
            raise CheckpointInterrupted("simulated loss after durable commit")

    with pytest.raises(CheckpointInterrupted):
        _run(source, regions, f_workspace, checkpoint_hook=after_first_commit)

    database = next((f_workspace / "checkpoints").glob("*.sqlite"))
    with sqlite3.connect(database) as conn:
        committed_blocks, committed_offset = conn.execute(
            "SELECT blocks_committed,next_offset FROM stage_state WHERE stage=?", (interrupted_stage,)
        ).fetchone()
    assert committed_blocks > 0
    assert committed_offset > 0
    # Observe actual source seeks: the interrupted stage must start at its
    # saved offset rather than replaying the committed continental prefix.
    import osm_checkpoint as module
    original_blocks = module.iter_pbf_blocks
    starts = []
    def observed_blocks(path, **kwargs):
        starts.append(kwargs.get("start_offset", 0))
        yield from original_blocks(path, **kwargs)
    monkeypatch.setattr(module, "iter_pbf_blocks", observed_blocks)
    resumed = _run(source, regions, f_workspace)
    assert starts[0] == committed_offset
    _assert_counts_equal(resumed, oracle)

    # A second invocation takes the completed checkpoint fast path and keeps
    # exactly the same aggregate rather than adding another provider pass.
    starts.clear()
    reused = _run(source, regions, f_workspace)
    _assert_counts_equal(reused, resumed)
    assert not starts, "Completed stages must not reparse continental frames"


def test_mid_batch_rollback_and_real_child_exit_resume_without_duplicates(f_workspace, regions):
    source = f_workspace / "fixture.osm.pbf"
    _fixture_pbf(source)
    oracle = _oracle(source, regions)
    env = os.environ.copy()
    env.update(
        {
            "PYTHONPATH": os.pathsep.join(
                [str(REPO / ".cache" / "osm-runtime"), str(REPO / "scripts")]
            ),
            "TEMP": str(f_workspace / "tmp"),
            "TMP": str(f_workspace / "tmp"),
            "PYTHONDONTWRITEBYTECODE": "1",
        }
    )
    child_code = f"""
import os, sys
from pathlib import Path
import geopandas as gpd
from shapely.geometry import box
sys.path.insert(0, {str(REPO / 'scripts')!r})
from osm_checkpoint import checkpointed_process_pbf
source = Path({str(source)!r})
regions = gpd.GeoDataFrame({{'regionId':['eco_fixture'],'areaKm2':[1000.0]}}, geometry=[box(-1,-1,12,12)], crs='EPSG:4326')
def hook(stage, ordinal, committed):
    if stage == 'discovery' and not committed and ordinal == 1:
        os._exit(86)
checkpointed_process_pbf(source, regions, checkpoint_root=Path({str(f_workspace / 'checkpoints')!r}), temp_root=Path({str(f_workspace / 'tmp')!r}), reserve_gib=0, checkpoint_blocks=3, checkpoint_hook=hook)
"""
    child = subprocess.run([sys.executable, "-c", child_code], env=env, cwd=str(REPO))
    assert child.returncode == 86
    resumed = _run(source, regions, f_workspace)
    _assert_counts_equal(resumed, oracle)

    # A fresh mid-batch interruption rolls back all three frames in that
    # transaction; recovery still returns the same one-count-per-feature data.
    source2 = f_workspace / "fixture-midbatch.osm.pbf"
    _fixture_pbf(source2)

    def in_batch(stage, ordinal, committed):
        if stage == "discovery" and not committed and ordinal == 1:
            raise CheckpointInterrupted("simulated loss before batch commit")

    with pytest.raises(CheckpointInterrupted):
        checkpointed_process_pbf(
            source2,
            regions,
            checkpoint_root=f_workspace / "mid-checkpoints",
            temp_root=f_workspace / "mid-tmp",
            reserve_gib=0,
            checkpoint_blocks=3,
            checkpoint_hook=in_batch,
        )
    resumed2 = checkpointed_process_pbf(
        source2,
        regions,
        checkpoint_root=f_workspace / "mid-checkpoints",
        temp_root=f_workspace / "mid-tmp",
        reserve_gib=0,
        checkpoint_blocks=3,
    )
    _assert_counts_equal(resumed2, _oracle(source2, regions))
    dbs = list((f_workspace / "mid-checkpoints").glob("*.sqlite"))
    assert dbs
    with sqlite3.connect(dbs[0]) as conn:
        assert conn.execute("SELECT COUNT(*) FROM frames").fetchone()[0] >= 3


def test_input_geometry_and_region_order_identity_do_not_reuse_old_checkpoint(f_workspace, regions):
    source = f_workspace / "fixture.osm.pbf"
    _fixture_pbf(source)
    first = _run(source, regions, f_workspace)
    first_key = first["checkpoint"]["key"]

    changed_regions = regions.copy()
    changed_regions.loc[0, "geometry"] = box(-1, -1, 11, 12)
    changed = _run(source, changed_regions, f_workspace)
    assert changed["checkpoint"]["key"] != first_key
    assert len(list((f_workspace / "checkpoints").glob("*.sqlite"))) == 2

    reordered = gpd.GeoDataFrame(
        {"regionId": ["eco_other", "eco_fixture"], "areaKm2": [1000.0, 1000.0]},
        geometry=[box(30, 30, 31, 31), box(-1, -1, 12, 12)],
        crs="EPSG:4326",
    )
    reordered_result = _run(source, reordered, f_workspace)
    assert reordered_result["checkpoint"]["key"] not in {first_key, changed["checkpoint"]["key"]}
    reversed_regions = reordered.iloc[::-1].reset_index(drop=True)
    reversed_result = _run(source, reversed_regions, f_workspace)
    assert reversed_result["checkpoint"]["key"] != reordered_result["checkpoint"]["key"]
    changed_crs = regions.set_crs("EPSG:3857", allow_override=True)
    assert _run(source, changed_crs, f_workspace)["checkpoint"]["key"] != first_key

    # Replacing the source with a different valid PBF changes its identity.
    # Deliberately corrupting compressed bytes would test decoder errors,
    # rather than successful invalidation of an otherwise valid input.
    _fixture_pbf(source, label="valid replacement input")
    mutated = _run(source, regions, f_workspace)
    assert mutated["checkpoint"]["key"] != first_key
    assert len(list((f_workspace / "checkpoints").glob("*.sqlite"))) >= 4



def test_progress_snapshots_do_not_rescan_completed_frame_history(f_workspace, regions, monkeypatch):
    source = f_workspace / "fixture.osm.pbf"
    _fixture_pbf(source)
    result = _run(source, regions, f_workspace)
    import osm_checkpoint as module
    def unexpected_history_scan(conn):
        raise AssertionError("A progress snapshot rescanned the full frame history")
    monkeypatch.setattr(module, "_frame_chain_digest", unexpected_history_scan)
    with sqlite3.connect(result["checkpoint"]["database"]) as conn:
        conn.row_factory = sqlite3.Row
        first = module._manifest_payload(conn)
        second = module._manifest_payload(conn)
    assert first["frameChainSha256"] == second["frameChainSha256"]
    assert first["frameChainSha256"] == result["input"]["frameChainSha256"]

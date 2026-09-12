"""Durable, block resumable preprocessing for OpenStreetMap PBF files.

The normal :class:`osmium.FileProcessor` API is deliberately kept for the
last, small geometry pass.  A continental PBF is first split at complete
OSM-binary Blob frames and each frame is parsed from an ``osmium.io.FileBuffer``.
The discovery, relation-closure, selected-way, and selected-node stages are
committed to a SQLite database on F:.  A killed process can therefore resume
at the last committed Blob frame instead of replaying the whole input prefix.

Only candidate and dependency objects are retained.  In particular, this
module never creates an all-node location table for the source PBF.
"""

from __future__ import annotations

import base64
import hashlib
import json
import math
import os
import re
import shutil
import sqlite3
import struct
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable, Iterator, Mapping, Sequence

import geopandas as gpd
from pyproj import Transformer
from shapely.geometry import Point, shape
from shapely.ops import transform as project_shape
from shapely.strtree import STRtree


PARSER_VERSION = "osmium-block-checkpoint-v3"
SCHEMA_VERSION = 3
DEFAULT_CHECKPOINT_BLOCKS = 8
DEFAULT_RESERVE_GIB = 40
_FRAME_HASH_BYTES = 32
_QUICK_SAMPLE_BYTES = 1024 * 1024
_MAX_PROTO_HEADER_BYTES = 64 * 1024
_MAX_BLOB_BYTES = 32 * 1024 * 1024
_JSON_SEPARATORS = (",", ":")
SITE_TYPES = ("landfill", "waste_disposal", "waste_transfer_station")
_KIND_ORDER = {"n": 0, "w": 1, "r": 2}


class CheckpointError(RuntimeError):
    """Raised when a durable checkpoint cannot be trusted or continued."""


class CheckpointInterrupted(RuntimeError):
    """Test-only interruption used to exercise transaction recovery."""


@dataclass(frozen=True)
class PbfBlock:
    ordinal: int
    offset: int
    length: int
    data: bytes
    blob_type: str


CheckpointHook = Callable[[str, int, bool], None]


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=_JSON_SEPARATORS)


def _decode_json(value: str | bytes | None, default):
    if value is None:
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return default


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _resolved(path: Path | str) -> Path:
    return Path(path).expanduser().resolve()


def _ensure_f_path(path: Path | str, label: str) -> Path:
    resolved = _resolved(path)
    # The production workspace is on F:.  Refusing another drive prevents a
    # fallback temp directory or SQLite journal from silently landing on C:.
    if resolved.drive.upper() != "F:":
        raise CheckpointError(f"{label} must be on F:, got {resolved}")
    return resolved


def _ensure_under(root: Path, candidate: Path, label: str) -> Path:
    root = _ensure_f_path(root, "checkpoint root")
    candidate = _ensure_f_path(candidate, label)
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise CheckpointError(f"{label} escaped checkpoint root: {candidate}") from exc
    return candidate


def _fsync_path(path: Path) -> None:
    try:
        with path.open("rb") as stream:
            os.fsync(stream.fileno())
    except OSError:
        # Windows may reject fsync on a file that has just been replaced.  The
        # SQLite FULL transaction and replace operation still provide the
        # required crash boundary in that case.
        pass


def _atomic_write_json(path: Path, payload: Mapping) -> None:
    path = _ensure_f_path(path, "checkpoint manifest")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, raw_tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    tmp = Path(raw_tmp)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, ensure_ascii=False, sort_keys=True, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
        _fsync_path(path)
    finally:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass


def _check_headroom(temp_root: Path | str, reserve_gib: int = DEFAULT_RESERVE_GIB) -> None:
    temp_root = _ensure_f_path(temp_root, "OSM temporary root")
    temp_root.mkdir(parents=True, exist_ok=True)
    if int(reserve_gib) < 0:
        raise CheckpointError("reserve_gib cannot be negative")
    free = shutil.disk_usage(temp_root).free
    reserve = int(reserve_gib) * 1024**3
    if free < reserve:
        raise CheckpointError(
            f"OSM stopped at the {reserve_gib} GiB reserve; durable checkpoints remain safe. "
            f"Free on {temp_root.drive}: {free / 1024**3:.2f} GiB"
        )


def _quick_input_fingerprint(path: Path) -> dict:
    path = _ensure_f_path(path, "OSM input")
    stat = path.stat()
    sample = hashlib.sha256()
    with path.open("rb") as stream:
        first = stream.read(min(_QUICK_SAMPLE_BYTES, stat.st_size))
        sample.update(struct.pack(">Q", len(first)))
        sample.update(first)
        if stat.st_size > _QUICK_SAMPLE_BYTES:
            stream.seek(max(0, stat.st_size - _QUICK_SAMPLE_BYTES))
            last = stream.read(_QUICK_SAMPLE_BYTES)
            sample.update(struct.pack(">Q", len(last)))
            sample.update(last)
    return {
        "path": str(path),
        "bytes": int(stat.st_size),
        "mtimeNs": int(stat.st_mtime_ns),
        "quickSha256": sample.hexdigest(),
    }


def _full_file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while True:
            chunk = stream.read(8 * 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _verify_committed_frames(path: Path, conn: sqlite3.Connection) -> None:
    """Verify every durable frame prefix before reusing a checkpoint."""

    with path.open("rb") as stream:
        for row in conn.execute(
            "SELECT ordinal,offset,length,sha256 FROM frames ORDER BY ordinal"
        ):
            ordinal, offset, length, expected = (
                int(row[0]),
                int(row[1]),
                int(row[2]),
                bytes(row[3]),
            )
            stream.seek(offset)
            data = stream.read(length)
            if len(data) != length or hashlib.sha256(data).digest() != expected:
                raise CheckpointError(
                    f"OSM input frame {ordinal} changed; refusing checkpoint reuse"
                )


def _file_frame_chain_digest(path: Path) -> str:
    digest = hashlib.sha256()
    for block in iter_pbf_blocks(path):
        digest.update(struct.pack(">QQQ", block.ordinal, block.offset, block.length))
        blob_type = block.blob_type.encode("utf-8")
        digest.update(struct.pack(">Q", len(blob_type)))
        digest.update(blob_type)
        digest.update(hashlib.sha256(block.data).digest())
    return digest.hexdigest()


def _canonical_geometry_fingerprints(regions: gpd.GeoDataFrame) -> dict:
    """Fingerprint the exact ordered analysis layer used by the overlay."""

    try:
        from shapely import normalize, to_wkb
    except ImportError:  # pragma: no cover - Shapely 1 fallback
        normalize = None
        to_wkb = None

    if "regionId" not in regions.columns:
        raise CheckpointError("Canonical region layer is missing regionId")
    geometry_hash = hashlib.sha256()
    order_hash = hashlib.sha256()
    ids: list[str] = []
    crs_value = ""
    if getattr(regions, "crs", None) is not None:
        try:
            crs_value = str(regions.crs.to_wkt())
        except AttributeError:
            crs_value = str(regions.crs)
    crs_bytes = crs_value.encode("utf-8")
    geometry_hash.update(struct.pack(">Q", len(crs_bytes)))
    geometry_hash.update(crs_bytes)
    for _, record in regions[["regionId", "geometry"]].iterrows():
        region_id = str(record["regionId"])
        geometry = record["geometry"]
        ids.append(region_id)
        encoded_id = region_id.encode("utf-8")
        order_hash.update(struct.pack(">Q", len(encoded_id)))
        order_hash.update(encoded_id)
        if geometry is None or getattr(geometry, "is_empty", True):
            wkb = b""
        else:
            normalized = normalize(geometry) if normalize is not None else geometry
            if to_wkb is not None:
                wkb = bytes(to_wkb(normalized, output_dimension=2, byte_order=1, include_srid=False))
            else:
                wkb = bytes(normalized.wkb)
        geometry_hash.update(struct.pack(">Q", len(encoded_id)))
        geometry_hash.update(encoded_id)
        geometry_hash.update(struct.pack(">Q", len(wkb)))
        geometry_hash.update(wkb)
    return {
        "count": len(ids),
        "orderedRegionIds": ids,
        "crs": crs_value,
        "geometrySha256": geometry_hash.hexdigest(),
        "orderSha256": order_hash.hexdigest(),
    }


def _checkpoint_key(input_fingerprint: Mapping, canonical: Mapping) -> str:
    identity = {
        "parserVersion": PARSER_VERSION,
        "input": dict(input_fingerprint),
        "geometrySha256": canonical["geometrySha256"],
        "orderSha256": canonical["orderSha256"],
    }
    return hashlib.sha256(_json(identity).encode("utf-8")).hexdigest()[:24]


def _safe_name(path: Path) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", path.name)


def _parse_varint(data: bytes, cursor: int) -> tuple[int, int]:
    value = 0
    shift = 0
    while cursor < len(data):
        byte = data[cursor]
        cursor += 1
        value |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return value, cursor
        shift += 7
        if shift > 63:
            break
    raise CheckpointError("Malformed protobuf varint in OSM PBF BlobHeader")


def _blob_header_fields(data: bytes) -> tuple[str, int]:
    cursor = 0
    blob_type = ""
    datasize = None
    while cursor < len(data):
        key, cursor = _parse_varint(data, cursor)
        field_number = key >> 3
        wire_type = key & 7
        if wire_type == 0:
            value, cursor = _parse_varint(data, cursor)
            if field_number == 3:
                datasize = value
        elif wire_type == 1:
            cursor += 8
        elif wire_type == 2:
            length, cursor = _parse_varint(data, cursor)
            end = cursor + length
            if end > len(data):
                raise CheckpointError("Malformed length-delimited BlobHeader field")
            if field_number == 1:
                try:
                    blob_type = data[cursor:end].decode("utf-8")
                except UnicodeDecodeError as exc:
                    raise CheckpointError("OSM BlobHeader type is not UTF-8") from exc
            cursor = end
        elif wire_type == 5:
            cursor += 4
        else:
            raise CheckpointError(f"Unsupported protobuf wire type {wire_type} in BlobHeader")
        if cursor > len(data):
            raise CheckpointError("Malformed OSM PBF BlobHeader")
    if not blob_type or datasize is None:
        raise CheckpointError("OSM PBF BlobHeader lacks type or datasize")
    if datasize <= 0 or datasize > _MAX_BLOB_BYTES:
        raise CheckpointError(f"OSM PBF Blob payload size is outside the safe bound: {datasize}")
    return blob_type, int(datasize)


def iter_pbf_blocks(path: Path | str, *, start_offset: int = 0, start_ordinal: int = 0) -> Iterator[PbfBlock]:
    """Yield complete OSM PBF frames from a validated frame boundary.

    ``start_offset`` is only ever obtained from a committed frame cursor.  It
    is a safe seek to a complete BlobHeader, not a seek into protobuf/object
    bytes; each yielded frame is independently parsed through FileBuffer.
    """

    path = _ensure_f_path(path, "OSM input")
    if start_offset < 0:
        raise CheckpointError("Negative OSM frame offset")
    with path.open("rb") as stream:
        stream.seek(start_offset)
        ordinal = int(start_ordinal)
        while True:
            offset = stream.tell()
            prefix = stream.read(4)
            if not prefix:
                return
            if len(prefix) != 4:
                raise CheckpointError(f"Truncated OSM PBF frame length at byte {offset}")
            header_length = struct.unpack(">I", prefix)[0]
            if header_length <= 0 or header_length > _MAX_PROTO_HEADER_BYTES:
                raise CheckpointError(f"Invalid OSM PBF BlobHeader length at byte {offset}: {header_length}")
            header = stream.read(header_length)
            if len(header) != header_length:
                raise CheckpointError(f"Truncated OSM PBF BlobHeader at byte {offset}")
            blob_type, payload_length = _blob_header_fields(header)
            payload = stream.read(payload_length)
            if len(payload) != payload_length:
                raise CheckpointError(f"Truncated OSM PBF Blob payload at byte {offset}")
            data = prefix + header + payload
            yield PbfBlock(ordinal, offset, len(data), data, blob_type)
            ordinal += 1


def _require_osmium():
    try:
        import osmium
    except ImportError as exc:  # pragma: no cover - depends on optional runtime
        raise CheckpointError("Reading .osm.pbf requires the F:-resident pyosmium runtime") from exc
    return osmium


def _iter_block_objects(
    block: PbfBlock,
    thread_pool,
    *,
    header_frame=None,
    entities=None,
    native_filter=None,
):
    """Parse one complete Blob with its original OSMHeader prefix."""

    osmium = _require_osmium()
    if block.blob_type == "OSMHeader":
        framed = block.data
    else:
        if header_frame is None:
            raise CheckpointError(
                f"OSM data Blob {block.ordinal} has no committed OSMHeader frame"
            )
        framed = header_frame + block.data
    buffer = osmium.io.FileBuffer(framed, "pbf")
    if entities is None:
        entities = osmium.osm.ALL
    processor = osmium.FileProcessor(buffer, entities=entities, thread_pool=thread_pool)
    if native_filter is not None:
        processor = processor.with_filter(native_filter)
    yield from processor


def _tags(obj) -> dict[str, str]:
    try:
        return {str(key): str(value) for key, value in obj.tags}
    except (AttributeError, TypeError, RuntimeError):
        return {}


def _site_type(tags: Mapping[str, str]) -> str | None:
    landuse = str(tags.get("landuse") or "").strip().lower()
    amenity = str(tags.get("amenity") or "").strip().lower()
    if landuse == "landfill":
        return "landfill"
    if amenity == "waste_disposal":
        return "waste_disposal"
    if amenity == "waste_transfer_station":
        return "waste_transfer_station"
    return None


def _is_area_relation(tags: Mapping[str, str]) -> bool:
    relation_type = str(tags.get("type") or "").strip().lower()
    boundary = str(tags.get("boundary") or "").strip().lower()
    area = str(tags.get("area") or "").strip().lower()
    return relation_type in {"multipolygon", "boundary"} or bool(boundary) or area == "yes"


def _object_kind(obj) -> str | None:
    name = type(obj).__name__.lower()
    if name == "node":
        return "n"
    if name == "way":
        return "w"
    if name == "relation":
        return "r"
    return None


def _node_payload(obj) -> dict | None:
    try:
        location = obj.location
        lon = float(location.lon)
        lat = float(location.lat)
        if not (math.isfinite(lon) and math.isfinite(lat) and -180 <= lon <= 180 and -90 <= lat <= 90):
            return None
        return {"id": int(obj.id), "lon": lon, "lat": lat, "tags": _tags(obj)}
    except (AttributeError, TypeError, ValueError, RuntimeError):
        return None


def _way_payload(obj) -> dict:
    try:
        nodes = [int(node.ref) for node in obj.nodes]
    except (AttributeError, TypeError, RuntimeError):
        nodes = []
    return {"id": int(obj.id), "nodes": nodes, "tags": _tags(obj)}


def _relation_payload(obj) -> dict:
    members = []
    try:
        for member in obj.members:
            members.append([str(member.type), int(member.ref), str(member.role or "")])
    except (AttributeError, TypeError, RuntimeError):
        members = []
    return {"id": int(obj.id), "members": members, "tags": _tags(obj)}


def _db_connect(path: Path) -> sqlite3.Connection:
    path = _ensure_f_path(path, "checkpoint database")
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=60, isolation_level=None)
    conn.execute("PRAGMA journal_mode=DELETE")
    conn.execute("PRAGMA synchronous=FULL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=60000")
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS stage_state (
            stage TEXT PRIMARY KEY,
            status TEXT NOT NULL,
            next_ordinal INTEGER NOT NULL DEFAULT 0,
            next_offset INTEGER NOT NULL DEFAULT 0,
            object_count INTEGER NOT NULL DEFAULT 0,
            blocks_committed INTEGER NOT NULL DEFAULT 0,
            frame_bytes INTEGER NOT NULL DEFAULT 0,
            started_at TEXT,
            completed_at TEXT
        );
        CREATE TABLE IF NOT EXISTS frames (
            ordinal INTEGER PRIMARY KEY,
            offset INTEGER NOT NULL,
            length INTEGER NOT NULL,
            blob_type TEXT NOT NULL,
            sha256 BLOB NOT NULL
        );
        CREATE TABLE IF NOT EXISTS relations (
            id INTEGER PRIMARY KEY,
            tags_json TEXT NOT NULL,
            object_order INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS relation_members (
            relation_id INTEGER NOT NULL,
            member_order INTEGER NOT NULL,
            member_type TEXT NOT NULL,
            member_id INTEGER NOT NULL,
            role TEXT NOT NULL,
            PRIMARY KEY (relation_id, member_order),
            FOREIGN KEY (relation_id) REFERENCES relations(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS relation_members_by_reference
            ON relation_members(member_type, member_id);
        CREATE TABLE IF NOT EXISTS seed_nodes (
            id INTEGER PRIMARY KEY,
            site_type TEXT NOT NULL,
            tags_json TEXT NOT NULL,
            object_order INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS seed_ways (
            id INTEGER PRIMARY KEY,
            site_type TEXT NOT NULL,
            tags_json TEXT NOT NULL,
            nodes_json TEXT NOT NULL,
            object_order INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS seed_relations (
            id INTEGER PRIMARY KEY,
            site_type TEXT NOT NULL,
            object_order INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS selected (
            kind TEXT NOT NULL,
            id INTEGER NOT NULL,
            reason TEXT NOT NULL,
            PRIMARY KEY (kind, id)
        );
        CREATE INDEX IF NOT EXISTS selected_by_kind ON selected(kind, id);
        CREATE TABLE IF NOT EXISTS objects (
            kind TEXT NOT NULL,
            id INTEGER NOT NULL,
            object_order INTEGER NOT NULL,
            payload_json TEXT NOT NULL,
            PRIMARY KEY(kind, id)
        );
        CREATE INDEX IF NOT EXISTS objects_order ON objects(kind, object_order);
        """
    )
    for stage in ("discovery", "ways", "nodes"):
        conn.execute(
            "INSERT OR IGNORE INTO stage_state(stage,status) VALUES (?, 'pending')",
            (stage,),
        )
    return conn


def _set_meta(conn: sqlite3.Connection, key: str, value) -> None:
    conn.execute(
        "INSERT INTO meta(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value if isinstance(value, str) else _json(value)),
    )


def _get_meta(conn: sqlite3.Connection, key: str, default=None):
    row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    if row is None:
        return default
    return _decode_json(row[0], row[0])


def _stage(conn: sqlite3.Connection, stage: str) -> sqlite3.Row:
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM stage_state WHERE stage=?", (stage,)).fetchone()
    if row is None:
        raise CheckpointError(f"Missing checkpoint stage {stage}")
    return row


def _update_stage(
    conn: sqlite3.Connection,
    stage: str,
    *,
    status: str | None = None,
    next_ordinal: int | None = None,
    next_offset: int | None = None,
    object_count: int | None = None,
    blocks_committed: int | None = None,
    frame_bytes: int | None = None,
    started_at: str | None = None,
    completed_at: str | None = None,
) -> None:
    current = _stage(conn, stage)
    values = {
        "status": status if status is not None else current["status"],
        "next_ordinal": next_ordinal if next_ordinal is not None else current["next_ordinal"],
        "next_offset": next_offset if next_offset is not None else current["next_offset"],
        "object_count": object_count if object_count is not None else current["object_count"],
        "blocks_committed": blocks_committed if blocks_committed is not None else current["blocks_committed"],
        "frame_bytes": frame_bytes if frame_bytes is not None else current["frame_bytes"],
        "started_at": started_at if started_at is not None else current["started_at"],
        "completed_at": completed_at if completed_at is not None else current["completed_at"],
    }
    conn.execute(
        """
        UPDATE stage_state SET status=:status,next_ordinal=:next_ordinal,next_offset=:next_offset,
            object_count=:object_count,blocks_committed=:blocks_committed,frame_bytes=:frame_bytes,
            started_at=:started_at,completed_at=:completed_at WHERE stage=:stage
        """,
        {"stage": stage, **values},
    )


def _call_hook(hook: CheckpointHook | None, stage: str, ordinal: int, committed: bool) -> None:
    if hook is not None:
        hook(stage, ordinal, committed)


def _begin(conn: sqlite3.Connection) -> None:
    conn.execute("BEGIN IMMEDIATE")


def _rollback(conn: sqlite3.Connection) -> None:
    try:
        conn.rollback()
    except sqlite3.Error:
        pass


def _commit(conn: sqlite3.Connection, db_path: Path) -> None:
    conn.commit()
    _fsync_path(db_path)


def _discover(
    conn: sqlite3.Connection,
    db_path: Path,
    path: Path,
    *,
    checkpoint_blocks: int,
    temp_root: Path,
    reserve_gib: int,
    hook: CheckpointHook | None,
    thread_pool,
) -> None:
    stage = _stage(conn, "discovery")
    if stage["status"] == "complete":
        return
    next_ordinal = int(stage["next_ordinal"])
    next_offset = int(stage["next_offset"])
    object_count = int(stage["object_count"])
    committed_blocks = int(stage["blocks_committed"])
    frame_bytes = int(stage["frame_bytes"])
    start_time = stage["started_at"] or _utc_now()

    encoded_header = _get_meta(conn, "osmHeaderFrame", None)
    header_frame = None
    if encoded_header:
        try:
            header_frame = base64.b64decode(str(encoded_header), validate=True)
        except (ValueError, TypeError):
            raise CheckpointError("Checkpoint OSMHeader frame is corrupt")

    osmium = _require_osmium()
    tag_filter = osmium.filter.KeyFilter("landuse", "amenity")
    tag_filter.enable_for(osmium.osm.NODE | osmium.osm.WAY)
    entities = osmium.osm.NODE | osmium.osm.WAY | osmium.osm.RELATION
    block_batch: list[PbfBlock] = []

    def commit_batch(batch: list[PbfBlock]) -> None:
        nonlocal object_count, committed_blocks, frame_bytes
        nonlocal next_ordinal, next_offset, header_frame
        if not batch:
            return
        _check_headroom(temp_root, reserve_gib)
        _begin(conn)
        try:
            for block in batch:
                if block.blob_type == "OSMHeader":
                    header_frame = block.data
                    _set_meta(conn, "osmHeaderFrame", base64.b64encode(header_frame).decode("ascii"))
                elif header_frame is None:
                    raise CheckpointError(
                        f"OSM data Blob {block.ordinal} appears before its OSMHeader"
                    )
                block_objects = 0
                if block.blob_type != "OSMHeader":
                    for local_index, obj in enumerate(
                        _iter_block_objects(
                            block,
                            thread_pool,
                            header_frame=header_frame,
                            entities=entities,
                            native_filter=tag_filter,
                        )
                    ):
                        kind = _object_kind(obj)
                        if kind is None:
                            continue
                        sequence = (int(block.ordinal) << 32) | int(local_index)
                        if kind == "r":
                            payload = _relation_payload(obj)
                            tags = payload["tags"]
                            conn.execute(
                                "INSERT OR REPLACE INTO relations(id,tags_json,object_order) VALUES (?,?,?)",
                                (payload["id"], _json(tags), sequence),
                            )
                            conn.execute("DELETE FROM relation_members WHERE relation_id=?", (payload["id"],))
                            conn.executemany(
                                "INSERT INTO relation_members(relation_id,member_order,member_type,member_id,role) VALUES (?,?,?,?,?)",
                                [
                                    (payload["id"], member_order, member[0], int(member[1]), member[2])
                                    for member_order, member in enumerate(payload["members"])
                                    if member[0] in {"n", "w", "r"}
                                ],
                            )
                            site_type = _site_type(tags)
                            if site_type:
                                conn.execute(
                                    "INSERT OR REPLACE INTO seed_relations(id,site_type,object_order) VALUES (?,?,?)",
                                    (payload["id"], site_type, sequence),
                                )
                            block_objects += 1
                        elif kind == "w":
                            payload = _way_payload(obj)
                            site_type = _site_type(payload["tags"])
                            if site_type:
                                conn.execute(
                                    "INSERT OR REPLACE INTO seed_ways(id,site_type,tags_json,nodes_json,object_order) VALUES (?,?,?,?,?)",
                                    (
                                        payload["id"],
                                        site_type,
                                        _json(payload["tags"]),
                                        _json(payload["nodes"]),
                                        sequence,
                                    ),
                                )
                            block_objects += 1
                        elif kind == "n":
                            payload = _node_payload(obj)
                            if payload is not None:
                                site_type = _site_type(payload["tags"])
                                if site_type:
                                    conn.execute(
                                        "INSERT OR REPLACE INTO seed_nodes(id,site_type,tags_json,object_order) VALUES (?,?,?,?)",
                                        (payload["id"], site_type, _json(payload["tags"]), sequence),
                                    )
                                block_objects += 1
                object_count += block_objects
                digest = hashlib.sha256(block.data).digest()
                conn.execute(
                    "INSERT OR REPLACE INTO frames(ordinal,offset,length,blob_type,sha256) VALUES (?,?,?,?,?)",
                    (block.ordinal, block.offset, block.length, block.blob_type, digest),
                )
                committed_blocks += 1
                frame_bytes += block.length
                next_ordinal = block.ordinal + 1
                next_offset = block.offset + block.length
                _call_hook(hook, "discovery", block.ordinal, False)
            _update_stage(
                conn,
                "discovery",
                status="running",
                next_ordinal=next_ordinal,
                next_offset=next_offset,
                object_count=object_count,
                blocks_committed=committed_blocks,
                frame_bytes=frame_bytes,
                started_at=start_time,
            )
            _commit(conn, db_path)
        except BaseException:
            _rollback(conn)
            raise
        _call_hook(hook, "discovery", batch[-1].ordinal, True)
        _write_manifest(conn)

    for block in iter_pbf_blocks(path, start_offset=next_offset, start_ordinal=next_ordinal):
        block_batch.append(block)
        if len(block_batch) >= checkpoint_blocks:
            commit_batch(block_batch)
            block_batch = []
            stage = _stage(conn, "discovery")
            next_ordinal = int(stage["next_ordinal"])
            next_offset = int(stage["next_offset"])
    commit_batch(block_batch)
    if header_frame is None:
        raise CheckpointError("OSM PBF contains no OSMHeader Blob")
    _begin(conn)
    try:
        _update_stage(conn, "discovery", status="complete", completed_at=_utc_now())
        _commit(conn, db_path)
    except BaseException:
        _rollback(conn)
        raise
    _write_manifest(conn)


def _insert_initial_selection(conn: sqlite3.Connection) -> None:
    conn.execute("INSERT OR IGNORE INTO selected(kind,id,reason) SELECT 'n',id,'tagged-node' FROM seed_nodes")
    conn.execute("INSERT OR IGNORE INTO selected(kind,id,reason) SELECT 'w',id,'tagged-way' FROM seed_ways")
    conn.execute("INSERT OR IGNORE INTO selected(kind,id,reason) SELECT 'r',id,'tagged-relation' FROM seed_relations")


def _closure(conn: sqlite3.Connection, db_path: Path) -> None:
    if _get_meta(conn, "closureComplete", False):
        return
    _begin(conn)
    try:
        _insert_initial_selection(conn)
        # A relation is retained when it is an area-capable parent of a
        # selected object, or when it is already a tagged candidate.  Then all
        # of its members are retained.  Repeating to a fixed point handles
        # nested multipolygons and old-style tagged outer ways.
        while True:
            before = conn.execute("SELECT COUNT(*) FROM selected").fetchone()[0]
            conn.execute(
                """
                INSERT OR IGNORE INTO selected(kind,id,reason)
                SELECT 'r', rm.relation_id, 'area-parent'
                FROM relation_members rm
                JOIN relations rel ON rel.id=rm.relation_id
                JOIN selected child ON child.id=rm.member_id AND child.kind=rm.member_type
                WHERE rm.member_type IN ('n','w','r')
                  AND (json_extract(rel.tags_json,'$.type') IN ('multipolygon','boundary')
                       OR json_extract(rel.tags_json,'$.boundary') IS NOT NULL
                       OR json_extract(rel.tags_json,'$.area')='yes')
                """
            )
            conn.execute(
                """
                INSERT OR IGNORE INTO selected(kind,id,reason)
                SELECT rm.member_type, rm.member_id, 'relation-member'
                FROM relation_members rm
                JOIN selected parent ON parent.kind='r' AND parent.id=rm.relation_id
                WHERE rm.member_type IN ('n','w','r')
                """
            )
            after = conn.execute("SELECT COUNT(*) FROM selected").fetchone()[0]
            if after == before:
                break
        # Copy relation payloads into the same durable object table used by the
        # way/node stages.  Their IDs/members/tags are exactly those observed
        # in discovery, including empty tags on old-style parent relations.
        conn.execute(
            """
            INSERT OR REPLACE INTO objects(kind,id,object_order,payload_json)
            SELECT 'r', rel.id, rel.object_order,
                   json_object(
                       'id', rel.id,
                       'tags', json(rel.tags_json),
                       'members', COALESCE((
                           SELECT json_group_array(json_array(member_type,member_id,role))
                           FROM relation_members rm WHERE rm.relation_id=rel.id
                           ORDER BY member_order
                       ), json('[]'))
                   )
            FROM relations rel JOIN selected s ON s.kind='r' AND s.id=rel.id
            """
        )
        _set_meta(conn, "closureComplete", True)
        _commit(conn, db_path)
    except BaseException:
        _rollback(conn)
        raise
    _write_manifest(conn)


def _selected_ids_for_block(conn: sqlite3.Connection, objects: Sequence[tuple[str, int]]) -> set[tuple[str, int]]:
    if not objects:
        return set()
    selected: set[tuple[str, int]] = set()
    # SQLite's host-parameter limit is commonly 999.  The query is per frame,
    # so memory stays bounded by one decompressed PBF Blob.
    for kind in ("n", "w", "r"):
        ids = [int(object_id) for object_kind, object_id in objects if object_kind == kind]
        for start in range(0, len(ids), 800):
            chunk = ids[start : start + 800]
            if not chunk:
                continue
            placeholders = ",".join("?" for _ in chunk)
            rows = conn.execute(
                f"SELECT id FROM selected WHERE kind=? AND id IN ({placeholders})",
                [kind, *chunk],
            )
            selected.update((kind, int(row[0])) for row in rows)
    return selected


def _checkpoint_header_frame(conn: sqlite3.Connection) -> bytes:
    encoded = _get_meta(conn, "osmHeaderFrame", None)
    if not encoded:
        raise CheckpointError("Checkpoint has no durable OSMHeader frame")
    try:
        return base64.b64decode(str(encoded), validate=True)
    except (ValueError, TypeError):
        raise CheckpointError("Checkpoint OSMHeader frame is corrupt")


def _mark_stage_complete(conn: sqlite3.Connection, db_path: Path, stage: str) -> None:
    _begin(conn)
    try:
        _update_stage(conn, stage, status="complete", completed_at=_utc_now())
        _commit(conn, db_path)
    except BaseException:
        _rollback(conn)
        raise
    _write_manifest(conn)


def _scan_ways(
    conn: sqlite3.Connection,
    db_path: Path,
    path: Path,
    *,
    checkpoint_blocks: int,
    temp_root: Path,
    reserve_gib: int,
    hook: CheckpointHook | None,
    thread_pool,
) -> None:
    stage = _stage(conn, "ways")
    if stage["status"] == "complete":
        return
    way_ids = {
        int(row[0])
        for row in conn.execute("SELECT id FROM selected WHERE kind='w' ORDER BY id")
    }
    if not way_ids:
        _mark_stage_complete(conn, db_path, "ways")
        return
    header_frame = _checkpoint_header_frame(conn)
    osmium = _require_osmium()
    native_filter = osmium.filter.IdFilter(sorted(way_ids))
    native_filter.enable_for(osmium.osm.WAY)
    entities = osmium.osm.WAY

    next_ordinal = int(stage["next_ordinal"])
    next_offset = int(stage["next_offset"])
    object_count = int(stage["object_count"])
    committed_blocks = int(stage["blocks_committed"])
    frame_bytes = int(stage["frame_bytes"])
    start_time = stage["started_at"] or _utc_now()
    batch: list[PbfBlock] = []

    def commit_batch(current: list[PbfBlock]) -> None:
        nonlocal object_count, committed_blocks, frame_bytes, next_ordinal, next_offset
        if not current:
            return
        _check_headroom(temp_root, reserve_gib)
        _begin(conn)
        try:
            for block in current:
                if block.blob_type != "OSMHeader":
                    for local_index, obj in enumerate(
                        _iter_block_objects(
                            block,
                            thread_pool,
                            header_frame=header_frame,
                            entities=entities,
                            native_filter=native_filter,
                        )
                    ):
                        if _object_kind(obj) != "w":
                            continue
                        payload = _way_payload(obj)
                        if payload["id"] not in way_ids:
                            continue
                        sequence = (int(block.ordinal) << 32) | int(local_index)
                        conn.execute(
                            "INSERT OR REPLACE INTO objects(kind,id,object_order,payload_json) VALUES ('w',?,?,?)",
                            (payload["id"], sequence, _json(payload)),
                        )
                        conn.executemany(
                            "INSERT OR IGNORE INTO selected(kind,id,reason) VALUES ('n',?,'way-node')",
                            [(int(node_id),) for node_id in payload["nodes"]],
                        )
                        object_count += 1
                committed_blocks += 1
                frame_bytes += block.length
                next_ordinal = block.ordinal + 1
                next_offset = block.offset + block.length
                _call_hook(hook, "ways", block.ordinal, False)
            _update_stage(
                conn,
                "ways",
                status="running",
                next_ordinal=next_ordinal,
                next_offset=next_offset,
                object_count=object_count,
                blocks_committed=committed_blocks,
                frame_bytes=frame_bytes,
                started_at=start_time,
            )
            _commit(conn, db_path)
        except BaseException:
            _rollback(conn)
            raise
        _call_hook(hook, "ways", current[-1].ordinal, True)
        _write_manifest(conn)

    for block in iter_pbf_blocks(path, start_offset=next_offset, start_ordinal=next_ordinal):
        batch.append(block)
        if len(batch) >= checkpoint_blocks:
            commit_batch(batch)
            batch = []
            stage = _stage(conn, "ways")
            next_ordinal = int(stage["next_ordinal"])
            next_offset = int(stage["next_offset"])
    commit_batch(batch)
    _mark_stage_complete(conn, db_path, "ways")


def _scan_nodes(
    conn: sqlite3.Connection,
    db_path: Path,
    path: Path,
    *,
    checkpoint_blocks: int,
    temp_root: Path,
    reserve_gib: int,
    hook: CheckpointHook | None,
    thread_pool,
) -> None:
    stage = _stage(conn, "nodes")
    if stage["status"] == "complete":
        return
    node_ids = {
        int(row[0])
        for row in conn.execute("SELECT id FROM selected WHERE kind='n' ORDER BY id")
    }
    if not node_ids:
        _mark_stage_complete(conn, db_path, "nodes")
        return
    header_frame = _checkpoint_header_frame(conn)
    osmium = _require_osmium()
    native_filter = osmium.filter.IdFilter(sorted(node_ids))
    native_filter.enable_for(osmium.osm.NODE)
    entities = osmium.osm.NODE

    next_ordinal = int(stage["next_ordinal"])
    next_offset = int(stage["next_offset"])
    object_count = int(stage["object_count"])
    committed_blocks = int(stage["blocks_committed"])
    frame_bytes = int(stage["frame_bytes"])
    start_time = stage["started_at"] or _utc_now()
    batch: list[PbfBlock] = []

    def commit_batch(current: list[PbfBlock]) -> None:
        nonlocal object_count, committed_blocks, frame_bytes, next_ordinal, next_offset
        if not current:
            return
        _check_headroom(temp_root, reserve_gib)
        _begin(conn)
        try:
            for block in current:
                if block.blob_type != "OSMHeader":
                    for local_index, obj in enumerate(
                        _iter_block_objects(
                            block,
                            thread_pool,
                            header_frame=header_frame,
                            entities=entities,
                            native_filter=native_filter,
                        )
                    ):
                        if _object_kind(obj) != "n":
                            continue
                        payload = _node_payload(obj)
                        if payload is None or payload["id"] not in node_ids:
                            continue
                        sequence = (int(block.ordinal) << 32) | int(local_index)
                        conn.execute(
                            "INSERT OR REPLACE INTO objects(kind,id,object_order,payload_json) VALUES ('n',?,?,?)",
                            (payload["id"], sequence, _json(payload)),
                        )
                        object_count += 1
                committed_blocks += 1
                frame_bytes += block.length
                next_ordinal = block.ordinal + 1
                next_offset = block.offset + block.length
                _call_hook(hook, "nodes", block.ordinal, False)
            _update_stage(
                conn,
                "nodes",
                status="running",
                next_ordinal=next_ordinal,
                next_offset=next_offset,
                object_count=object_count,
                blocks_committed=committed_blocks,
                frame_bytes=frame_bytes,
                started_at=start_time,
            )
            _commit(conn, db_path)
        except BaseException:
            _rollback(conn)
            raise
        _call_hook(hook, "nodes", current[-1].ordinal, True)
        _write_manifest(conn)

    for block in iter_pbf_blocks(path, start_offset=next_offset, start_ordinal=next_ordinal):
        batch.append(block)
        if len(batch) >= checkpoint_blocks:
            commit_batch(batch)
            batch = []
            stage = _stage(conn, "nodes")
            next_ordinal = int(stage["next_ordinal"])
            next_offset = int(stage["next_offset"])
    commit_batch(batch)
    _mark_stage_complete(conn, db_path, "nodes")


def _object_rows(conn: sqlite3.Connection, kind: str) -> Iterator[dict]:
    # SimpleWriter requires monotonically increasing IDs within each entity
    # kind.  Source PBFs are normally ordered this way; sorting the compact
    # selected table also keeps recovery output valid for tiny fixtures and
    # hand-built extracts.
    for row in conn.execute(
        "SELECT payload_json FROM objects WHERE kind=? ORDER BY id",
        (kind,),
    ):
        yield _decode_json(row[0], {})


def _write_extracted_pbf(conn: sqlite3.Connection, path: Path, temp_root: Path) -> Path:
    osmium = _require_osmium()
    temp_root = _ensure_f_path(temp_root, "OSM temporary root")
    temp_root.mkdir(parents=True, exist_ok=True)
    extracted = temp_root / f"{_safe_name(path)}.{_get_meta(conn, 'checkpointKey', 'unknown')}.selected.osm.pbf"
    extracted = _ensure_under(temp_root, extracted, "selected OSM stream")

    saved_bytes = _get_meta(conn, "selectedStreamBytes", None)
    saved_sha = _get_meta(conn, "selectedStreamSha256", None)
    if extracted.exists() and saved_bytes is not None and saved_sha:
        try:
            if extracted.stat().st_size == int(saved_bytes):
                if _full_file_sha256(extracted) == str(saved_sha):
                    return extracted
        except (OSError, ValueError):
            pass

    fd, raw_tmp = tempfile.mkstemp(
        prefix=f".{extracted.name}.{os.getpid()}.",
        suffix=".tmp.osm.pbf",
        dir=str(temp_root),
    )
    os.close(fd)
    temporary = Path(raw_tmp)
    try:
        temporary.unlink()
    except FileNotFoundError:
        pass
    try:
        header = osmium.io.Header()
        header.set("generator", "MotherWorld durable OSM checkpoint")
        with osmium.SimpleWriter(str(temporary), header=header, overwrite=True) as writer:
            for payload in _object_rows(conn, "n"):
                writer.add_node(
                    osmium.osm.mutable.Node(
                        id=int(payload["id"]),
                        location=(float(payload["lon"]), float(payload["lat"])),
                        tags=payload.get("tags") or {},
                    )
                )
            for payload in _object_rows(conn, "w"):
                writer.add_way(
                    osmium.osm.mutable.Way(
                        id=int(payload["id"]),
                        nodes=[int(node_id) for node_id in payload.get("nodes") or []],
                        tags=payload.get("tags") or {},
                    )
                )
            for payload in _object_rows(conn, "r"):
                writer.add_relation(
                    osmium.osm.mutable.Relation(
                        id=int(payload["id"]),
                        members=[
                            (str(member[0]), int(member[1]), str(member[2] or ""))
                            for member in payload.get("members") or []
                            if len(member) >= 3 and str(member[0]) in {"n", "w", "r"}
                        ],
                        tags=payload.get("tags") or {},
                    )
                )
        _fsync_path(temporary)
        os.replace(temporary, extracted)
        _fsync_path(extracted)
        selected_bytes = int(extracted.stat().st_size)
        selected_sha = _full_file_sha256(extracted)
        db_path = _resolved(_get_meta(conn, "database", ""))
        _begin(conn)
        try:
            _set_meta(conn, "selectedStreamBytes", selected_bytes)
            _set_meta(conn, "selectedStreamSha256", selected_sha)
            _commit(conn, db_path)
        except BaseException:
            _rollback(conn)
            raise
        return extracted
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _node_point(obj) -> Point | None:
    try:
        location = obj.location
        lon = float(location.lon)
        lat = float(location.lat)
        if not (math.isfinite(lon) and math.isfinite(lat) and -180 <= lon <= 180 and -90 <= lat <= 90):
            return None
        return Point(lon, lat)
    except (AttributeError, TypeError, ValueError, RuntimeError):
        return None


def _way_is_closed(obj) -> bool:
    try:
        nodes = list(obj.nodes)
        return len(nodes) > 2 and int(nodes[0].ref) == int(nodes[-1].ref)
    except (AttributeError, TypeError, RuntimeError):
        return False


def _open_way_point(obj) -> Point | None:
    try:
        for node in obj.nodes:
            location = node.location
            lon = float(location.lon)
            lat = float(location.lat)
            if math.isfinite(lon) and math.isfinite(lat) and -180 <= lon <= 180 and -90 <= lat <= 90:
                return Point(lon, lat)
    except (AttributeError, TypeError, ValueError, RuntimeError):
        return None
    return None


def _area_geometry(factory, obj):
    try:
        return shape(json.loads(factory.create_multipolygon(obj)))
    except (RuntimeError, ValueError, TypeError, json.JSONDecodeError):
        return None


def _add_geometry(
    geometry,
    site_type: str,
    counts: dict[str, dict[str, float]],
    region_geometries: list,
    projected_region_geometries: list,
    spatial_index: STRtree,
    project_to_equal_area,
) -> None:
    if geometry is None or geometry.is_empty:
        return
    representative = geometry if geometry.geom_type == "Point" else geometry.representative_point()
    if representative.is_empty:
        return
    for index in spatial_index.query(representative):
        index = int(index)
        region = region_geometries[index]
        if not region.covers(representative):
            continue
        entry = counts.setdefault(
            str(index),
            {"landfill": 0, "waste_disposal": 0, "waste_transfer_station": 0, "landfillAreaKm2": 0.0},
        )
        entry[site_type] += 1
    if site_type != "landfill" or geometry.geom_type not in {"Polygon", "MultiPolygon"}:
        return
    projected = project_to_equal_area(geometry)
    for index in spatial_index.query(geometry):
        index = int(index)
        region = region_geometries[index]
        if not region.intersects(geometry):
            continue
        entry = counts.setdefault(
            str(index),
            {"landfill": 0, "waste_disposal": 0, "waste_transfer_station": 0, "landfillAreaKm2": 0.0},
        )
        intersection = projected.intersection(projected_region_geometries[index])
        if not intersection.is_empty:
            entry["landfillAreaKm2"] += float(intersection.area) / 1_000_000.0


def _count_selected_stream(extracted: Path, regions: gpd.GeoDataFrame, thread_pool) -> dict:
    osmium = _require_osmium()
    regions_wgs84 = [geometry for geometry in regions.geometry]
    equal_area_regions = regions.to_crs("EPSG:6933")
    regions_equal_area = [geometry for geometry in equal_area_regions.geometry]
    spatial_index = STRtree(regions_wgs84)
    transformer = Transformer.from_crs("EPSG:4326", "EPSG:6933", always_xy=True)
    project_to_equal_area = lambda geometry: project_shape(transformer.transform, geometry)
    factory = osmium.geom.GeoJSONFactory()
    counts: dict[str, dict[str, float]] = {}
    object_count = 0
    candidate_site_count = 0
    skipped_geometry_count = 0
    processor = osmium.FileProcessor(str(extracted), thread_pool=thread_pool).with_locations("sparse_file_array").with_areas()
    for obj in processor:
        object_count += 1
        kind = type(obj).__name__
        tags = _tags(obj)
        site_type = _site_type(tags)
        if site_type is None:
            continue
        geometry = None
        if kind == "Node":
            geometry = _node_point(obj)
        elif kind == "Area":
            geometry = _area_geometry(factory, obj)
        elif kind == "Way":
            # with_areas emits a closed way as Area too.  Counting its source
            # Way would double count one mapped feature.
            if not _way_is_closed(obj):
                geometry = _open_way_point(obj)
        if geometry is None:
            skipped_geometry_count += 1
            continue
        candidate_site_count += 1
        _add_geometry(
            geometry,
            site_type,
            counts,
            regions_wgs84,
            regions_equal_area,
            spatial_index,
            project_to_equal_area,
        )
    return {
        "schemaVersion": SCHEMA_VERSION,
        "providerId": "osm-waste",
        "parserVersion": PARSER_VERSION,
        "objectCount": object_count,
        "candidateSiteCount": candidate_site_count,
        "skippedGeometryCount": skipped_geometry_count,
        "mappedRegionCount": len(counts),
        "countsByRegionIndex": counts,
    }


def _frame_chain_digest(conn: sqlite3.Connection) -> str:
    digest = hashlib.sha256()
    for row in conn.execute("SELECT ordinal,offset,length,blob_type,sha256 FROM frames ORDER BY ordinal"):
        digest.update(struct.pack(">QQQ", int(row[0]), int(row[1]), int(row[2])))
        blob_type = str(row[3]).encode("utf-8")
        digest.update(struct.pack(">Q", len(blob_type)))
        digest.update(blob_type)
        digest.update(bytes(row[4]))
    return digest.hexdigest()


def _completed_frame_chain_digest(conn: sqlite3.Connection) -> str:
    """Cache the immutable discovery digest; progress updates must stay O(1).

    Explicit input verification still calls the uncached digest function.
    Discovery frames do not change after the stage is marked complete.
    """
    cached = _get_meta(conn, "completedFrameChainSha256", None)
    if cached is None:
        cached = _frame_chain_digest(conn)
        _set_meta(conn, "completedFrameChainSha256", cached)
    return str(cached)


def _manifest_payload(conn: sqlite3.Connection) -> dict:
    input_identity = _get_meta(conn, "inputFingerprint", {})
    canonical = _get_meta(conn, "canonicalFingerprint", {})
    stages = {}
    for stage_name in ("discovery", "ways", "nodes"):
        row = _stage(conn, stage_name)
        stages[stage_name] = {
            "status": row["status"],
            "nextOrdinal": row["next_ordinal"],
            "nextOffset": row["next_offset"],
            "objectCount": row["object_count"],
            "blocksCommitted": row["blocks_committed"],
            "frameBytes": row["frame_bytes"],
            "startedAt": row["started_at"],
            "completedAt": row["completed_at"],
        }
    result = _get_meta(conn, "result", None)
    return {
        "schemaVersion": SCHEMA_VERSION,
        "parserVersion": PARSER_VERSION,
        "checkpointKey": _get_meta(conn, "checkpointKey", None),
        "database": _get_meta(conn, "database", None),
        "input": input_identity,
        "sourceSha256": _get_meta(conn, "initialFileSha256", None),
        "verifiedSourceSha256": _get_meta(conn, "verifiedFileSha256", None),
        "canonical": canonical,
        "frameChainSha256": _completed_frame_chain_digest(conn) if stages["discovery"]["status"] == "complete" else None,
        "closureComplete": bool(_get_meta(conn, "closureComplete", False)),
        "stages": stages,
        "result": result,
        "updatedAt": _utc_now(),
    }


def _write_manifest(conn: sqlite3.Connection) -> None:
    manifest = _get_meta(conn, "manifestPath", None)
    if not manifest:
        return
    _atomic_write_json(_resolved(manifest), _manifest_payload(conn))


def _initialize_checkpoint(
    db_path: Path,
    manifest_path: Path,
    path: Path,
    canonical: Mapping,
    input_identity: Mapping,
    checkpoint_key: str,
    initial_file_sha256: str | None = None,
) -> sqlite3.Connection:
    conn = _db_connect(db_path)
    existing = _get_meta(conn, "checkpointKey", None)
    if existing and existing != checkpoint_key:
        conn.close()
        raise CheckpointError(f"Checkpoint identity collision at {db_path}; use a fresh checkpoint key")
    _begin(conn)
    try:
        _set_meta(conn, "checkpointKey", checkpoint_key)
        _set_meta(conn, "database", str(db_path))
        _set_meta(conn, "manifestPath", str(manifest_path))
        _set_meta(conn, "inputFingerprint", dict(input_identity))
        _set_meta(conn, "canonicalFingerprint", dict(canonical))
        if initial_file_sha256 is not None and _get_meta(conn, "initialFileSha256", None) is None:
            _set_meta(conn, "initialFileSha256", str(initial_file_sha256))
        _set_meta(conn, "createdAt", _get_meta(conn, "createdAt", _utc_now()))
        _commit(conn, db_path)
    except BaseException:
        _rollback(conn)
        conn.close()
        raise
    _write_manifest(conn)
    return conn


def _validate_existing_checkpoint(conn: sqlite3.Connection, input_identity: Mapping, canonical: Mapping) -> None:
    saved_input = _get_meta(conn, "inputFingerprint", {})
    saved_canonical = _get_meta(conn, "canonicalFingerprint", {})
    if saved_input != dict(input_identity):
        raise CheckpointError("OSM checkpoint input fingerprint does not match the current file")
    if (
        saved_canonical.get("geometrySha256") != canonical.get("geometrySha256")
        or saved_canonical.get("orderSha256") != canonical.get("orderSha256")
        or saved_canonical.get("count") != canonical.get("count")
        or saved_canonical.get("crs") != canonical.get("crs")
    ):
        raise CheckpointError("OSM checkpoint canonical geometry/order fingerprint does not match")


def checkpointed_process_pbf(
    path: Path | str,
    regions: gpd.GeoDataFrame,
    *,
    checkpoint_root: Path | str,
    temp_root: Path | str,
    checkpoint_blocks: int = DEFAULT_CHECKPOINT_BLOCKS,
    reserve_gib: int = DEFAULT_RESERVE_GIB,
    verify_input: bool = False,
    checkpoint_hook: CheckpointHook | None = None,
) -> dict:
    """Process one PBF and return counts keyed by canonical region row index.

    ``checkpoint_hook`` is intentionally injectable for tests.  It receives
    ``(stage, block_ordinal, committed)``; raising before ``committed=True``
    simulates a kill in a transaction and raising after it tests restart from
    the next Blob frame.
    """

    path = _ensure_f_path(path, "OSM input")
    checkpoint_root = _ensure_f_path(checkpoint_root, "checkpoint root")
    temp_root = _ensure_f_path(temp_root, "OSM temporary root")
    if not path.exists() or not path.is_file() or path.stat().st_size <= 0:
        raise CheckpointError(f"OSM input is missing or zero-byte: {path}")
    if path.name.lower().endswith(".part"):
        raise CheckpointError(f"Refusing incomplete OSM artifact: {path}")
    if not isinstance(checkpoint_blocks, int) or checkpoint_blocks < 1:
        raise CheckpointError("checkpoint_blocks must be at least one complete PBF Blob")
    _check_headroom(temp_root, reserve_gib)
    # pyosmium's sparse location array follows the process temp variables.
    # Keep its transient files beside the durable F: checkpoint artifacts.
    for temp_name in ("TEMP", "TMP", "TMPDIR"):
        os.environ[temp_name] = str(temp_root)
    canonical = _canonical_geometry_fingerprints(regions)
    input_identity = _quick_input_fingerprint(path)
    key = _checkpoint_key(input_identity, canonical)
    checkpoint_root.mkdir(parents=True, exist_ok=True)
    stem = f"{_safe_name(path)}.{key}"
    db_path = _ensure_under(checkpoint_root, checkpoint_root / f"{stem}.sqlite", "checkpoint database")
    manifest_path = _ensure_under(checkpoint_root, checkpoint_root / f"{stem}.manifest.json", "checkpoint manifest")
    checkpoint_preexisting = db_path.exists() and db_path.stat().st_size > 0
    initial_file_sha256 = None if checkpoint_preexisting else _full_file_sha256(path)
    conn = _initialize_checkpoint(
        db_path,
        manifest_path,
        path,
        canonical,
        input_identity,
        key,
        initial_file_sha256=initial_file_sha256,
    )
    try:
        _validate_existing_checkpoint(conn, input_identity, canonical)
        if verify_input:
            _verify_committed_frames(path, conn)
            current_file_sha = _full_file_sha256(path)
            saved_file_sha = _get_meta(conn, "initialFileSha256", None)
            if saved_file_sha is None:
                saved_file_sha = _get_meta(conn, "verifiedFileSha256", None)
            if saved_file_sha is None:
                discovery_status = _stage(conn, "discovery")["status"]
                if discovery_status != "complete":
                    raise CheckpointError(
                        "Checkpoint has no initial source SHA-256; cannot verify its uncommitted suffix"
                    )
            elif str(saved_file_sha) != current_file_sha:
                raise CheckpointError(
                    "Verified OSM input SHA-256 changed; refusing checkpoint reuse"
                )
            discovery_status = _stage(conn, "discovery")["status"]
            current_chain = None
            if discovery_status == "complete":
                current_chain = _file_frame_chain_digest(path)
                if current_chain != _frame_chain_digest(conn):
                    raise CheckpointError(
                        "OSM input frame chain changed; refusing checkpoint reuse"
                    )
            _begin(conn)
            try:
                _set_meta(conn, "verifiedFileSha256", current_file_sha)
                if current_chain is not None:
                    _set_meta(conn, "verifiedInputFrameChainSha256", current_chain)
                _commit(conn, db_path)
            except BaseException:
                _rollback(conn)
                raise
        osmium = _require_osmium()
        thread_pool = osmium.io.ThreadPool(1, 1)
        print(f"OSM checkpoint {path.name}: {key}; complete Blob frames resume at F:", flush=True)
        _discover(
            conn,
            db_path,
            path,
            checkpoint_blocks=checkpoint_blocks,
            temp_root=temp_root,
            reserve_gib=reserve_gib,
            hook=checkpoint_hook,
            thread_pool=thread_pool,
        )
        _closure(conn, db_path)
        _scan_ways(
            conn,
            db_path,
            path,
            checkpoint_blocks=checkpoint_blocks,
            temp_root=temp_root,
            reserve_gib=reserve_gib,
            hook=checkpoint_hook,
            thread_pool=thread_pool,
        )
        _scan_nodes(
            conn,
            db_path,
            path,
            checkpoint_blocks=checkpoint_blocks,
            temp_root=temp_root,
            reserve_gib=reserve_gib,
            hook=checkpoint_hook,
            thread_pool=thread_pool,
        )
        extracted = _write_extracted_pbf(conn, path, temp_root)
        result = _count_selected_stream(extracted, regions, thread_pool)
        result["input"] = {
            **input_identity,
            "sourceSha256": _get_meta(conn, "initialFileSha256", None),
            "frameChainSha256": _frame_chain_digest(conn),
            "frameCount": conn.execute("SELECT COUNT(*) FROM frames").fetchone()[0],
        }
        result["canonical"] = canonical
        result["checkpoint"] = {
            "key": key,
            "database": str(db_path),
            "manifest": str(manifest_path),
            "selectedStream": str(extracted),
            "stages": _manifest_payload(conn)["stages"],
        }
        _begin(conn)
        try:
            _set_meta(conn, "result", result)
            _set_meta(conn, "sourceFrameChainSha256", result["input"]["frameChainSha256"])
            if verify_input:
                _set_meta(conn, "verifiedFrameChainSha256", result["input"]["frameChainSha256"])
            _commit(conn, db_path)
        except BaseException:
            _rollback(conn)
            raise
        _write_manifest(conn)
        return result
    finally:
        conn.close()


__all__ = [
    "CheckpointError",
    "CheckpointInterrupted",
    "DEFAULT_CHECKPOINT_BLOCKS",
    "DEFAULT_RESERVE_GIB",
    "PARSER_VERSION",
    "checkpointed_process_pbf",
    "iter_pbf_blocks",
]

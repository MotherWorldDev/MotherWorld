#!/usr/bin/env python
"""Run the NOAA vegetation builder as a resumable, F:-only background worker.

The worker delegates all scientific work to ``build_vegetation_backbone_direct``.
It only owns process lifecycle, an append-only log, and a small status record so
an interrupted run can be resumed from the builder's annual checkpoints.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys


DEFAULT_RAW_ROOT = Path("F:/BiomeSummary/MotherWorld-v8-dataset-downloader/earth_health_backbone_raw")
DEFAULT_OUTPUT_ROOT = Path("F:/BiomeSummary/.cache/motherworld/v8-build/direct-noaa-nasa")
DEFAULT_RESERVE_BYTES = 40 * 1024**3
DEFAULT_MAX_OWNED_RAW_BYTES = 32 * 1024**3
DEFAULT_MAX_SINGLE_RAW_BYTES = 128 * 1024**2


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def write_json_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_name(f".{path.name}.part")
    part.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    part.replace(path)


def require_f_path(label: str, path: Path) -> Path:
    resolved = path.expanduser().resolve()
    if resolved.drive.upper() != "F:":
        raise SystemExit(f"{label} must stay on F: (got {resolved})")
    return resolved


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Durable F:-only NOAA AVHRR/VIIRS vegetation worker")
    p.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1])
    p.add_argument("--raw-root", type=Path, default=DEFAULT_RAW_ROOT)
    p.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    p.add_argument("--work-root", type=Path)
    p.add_argument("--start-year", type=int, default=1993)
    p.add_argument("--end-year", type=int, default=2025)
    p.add_argument("--baseline-start", type=int, default=1982)
    p.add_argument("--baseline-end", type=int, default=1992)
    p.add_argument("--vegetated-threshold", type=float, default=0.10)
    p.add_argument("--coarsen", type=int, default=8)
    p.add_argument("--retain-raw", action="store_true")
    p.add_argument("--skip-download", action="store_true")
    p.add_argument("--timeout", type=int, default=180)
    p.add_argument("--max-owned-raw-bytes", type=int, default=DEFAULT_MAX_OWNED_RAW_BYTES)
    p.add_argument("--reserve-bytes", type=int, default=DEFAULT_RESERVE_BYTES)
    p.add_argument("--max-single-raw-bytes", type=int, default=DEFAULT_MAX_SINGLE_RAW_BYTES)
    p.add_argument("--log-file", type=Path)
    p.add_argument("--status-file", type=Path)
    p.add_argument("--pid-file", type=Path)
    return p


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    repo = args.repo.resolve()
    raw_root = require_f_path("--raw-root", args.raw_root)
    output_root = require_f_path("--output-root", args.output_root)
    work_root = require_f_path("--work-root", args.work_root or (output_root / "vegetation-work"))
    log_file = require_f_path("--log-file", args.log_file or (output_root / "vegetation-worker.log"))
    status_file = require_f_path("--status-file", args.status_file or (output_root / "vegetation-worker-status.json"))
    pid_file = require_f_path("--pid-file", args.pid_file or (output_root / "vegetation-worker.pid"))
    builder = (repo / "scripts" / "build_vegetation_backbone_direct.py").resolve()
    if not builder.is_file():
        raise SystemExit(f"builder not found: {builder}")
    output_root.mkdir(parents=True, exist_ok=True)
    pid_file.write_text(str(os.getpid()) + "\n", encoding="ascii")
    command = [
        sys.executable,
        str(builder),
        "--raw-root",
        str(raw_root),
        "--output-root",
        str(output_root),
        "--work-root",
        str(work_root),
        "--start-year",
        str(args.start_year),
        "--end-year",
        str(args.end_year),
        "--baseline-start",
        str(args.baseline_start),
        "--baseline-end",
        str(args.baseline_end),
        "--vegetated-threshold",
        str(args.vegetated_threshold),
        "--coarsen",
        str(args.coarsen),
        "--timeout",
        str(args.timeout),
        "--max-owned-raw-bytes",
        str(args.max_owned_raw_bytes),
        "--reserve-bytes",
        str(args.reserve_bytes),
        "--max-single-raw-bytes",
        str(args.max_single_raw_bytes),
    ]
    if args.retain_raw:
        command.append("--retain-raw")
    if args.skip_download:
        command.append("--skip-download")
    env = os.environ.copy()
    temp_root = output_root / "tmp"
    temp_root.mkdir(parents=True, exist_ok=True)
    env["TEMP"] = str(temp_root)
    env["TMP"] = str(temp_root)
    vendor = repo / ".cache" / "python"
    if vendor.is_dir():
        env["PYTHONPATH"] = str(vendor) + os.pathsep + env.get("PYTHONPATH", "")
    status = {
        "schemaVersion": 1,
        "status": "running",
        "startedAt": utc_now(),
        "workerPid": os.getpid(),
        "builderPid": None,
        "command": command,
        "rawRoot": str(raw_root),
        "outputRoot": str(output_root),
        "workRoot": str(work_root),
        "logFile": str(log_file),
    }
    write_json_atomic(status_file, status)
    return_code = None
    try:
        with log_file.open("a", encoding="utf-8") as log:
            log.write(json.dumps({"event": "worker_started", "at": utc_now(), "command": command}) + "\n")
            log.flush()
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            process = subprocess.Popen(
                command,
                cwd=str(repo),
                env=env,
                stdout=log,
                stderr=subprocess.STDOUT,
                creationflags=creationflags,
            )
            status["builderPid"] = process.pid
            write_json_atomic(status_file, status)
            return_code = process.wait()
        status.update(
            {
                "status": "complete" if return_code == 0 else "failed",
                "finishedAt": utc_now(),
                "exitCode": return_code,
            }
        )
        write_json_atomic(status_file, status)
        return int(return_code)
    except BaseException as exc:
        status.update({"status": "failed", "finishedAt": utc_now(), "error": repr(exc), "exitCode": return_code})
        write_json_atomic(status_file, status)
        raise
    finally:
        if pid_file.exists():
            pid_file.unlink()


if __name__ == "__main__":
    raise SystemExit(main())
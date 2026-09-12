# OSM scan interruption and checkpoint recovery

## Incident

On 2026-09-12 the Europe waste-site scan stopped after reporting 1,774,000,000
objects. The legacy worker saved only completed continental aggregates. Its
in-memory counts, node-location index and area-assembly state were not a
resumable checkpoint. Five completed continental partials and the source PBF
files survived, but Europe had to restart.

A saved status of `running` is not proof of a live process. Check the process
and advancing log/checkpoint before reporting progress. The cause of the
simultaneous worker termination was not established; it must not be described
as a confirmed network error or reboot.

## Required recovery contract

The replacement pipeline must save complete PBF block boundaries and the
candidate/dependency state together. Resuming must seek to the committed block
rather than replaying the entire continental prefix. Within each large-file
scan, only the unfinished batch may be replayed. The final geometry pass can
replay the much smaller extracted candidate/dependency file; it does not
rebuild dependencies from the continental source. Raw byte offsets inside
compressed blocks are not valid restart points.

OSM ways reference nodes and multipolygon relations reference ways. These
references must survive recovery; a counter or a file offset alone cannot
reconstruct their geometry. The final extracted candidate/dependency stream
must retain the original geometry and counting semantics, including avoiding
counting both a closed way and its generated area.

Checkpoints must be transactional and tied to the source file, parser version,
ordered canonical region IDs and analysis geometry. A mismatch must not be
silently accepted. Raw inputs, checkpoints, temporary files and logs stay on
F:, with the existing 40 GiB free-space reserve.

## Existing boundary hold

The legacy partial audit verifies canonical row ordering but identifies
`eco_509` and `eco_609` as requiring recalculation with corrected geometry.
Loading the current adapter does not correct previously aggregated counts.
Those results remain withheld until recalculated; the checkpoint change does
not authorize publication of the old aggregates. See
[the boundary-correction issue](REGION_BOUNDARY_CORRECTIONS.md).

## Validation required before switching workers

Use real small PBF fixtures with references crossing block boundaries. Compare
an uninterrupted legacy geometry run with both uninterrupted and interrupted
replacement runs. Include tagged nodes, open and closed ways, multipolygons,
and missing references. Exercise interruption before and after commit,
input/geometry/order mismatch, and verify that resumed reading skips committed
blocks. Preserve the active worker until the replacement passes these checks.

## Runner and storage

The tracked entry point is `scripts/build_osm_waste_sites_stream.py`; the
Windows launcher is `scripts/start_osm_checkpoint_worker.ps1`. The launcher
starts Europe first, refuses a duplicate OSM process, opens no visible window,
and puts the current `scripts` directory ahead of older worktrees on the
Python import path.

From `F:\BiomeSummary`, run:

```powershell
.\scripts\start_osm_checkpoint_worker.ps1
```

The default batch is eight complete PBF frames. Use `-CheckpointBlocks` to
change it. SQLite checkpoints and manifests live under
`.cache/motherworld/v8-build/local-diagnostics/provider-fragments/land-pollution/osm-waste/checkpoints`.
Logs and launch receipts live in `.cache/osm-recovery`.

The first run computes a full source SHA-256 once. Normal restarts use file
size, modification time and sampled-content identity so they do not reread the
full continental prefix. `-VerifyInput` requests the
slower full verification path. The checkpoint also records canonical geometry,
CRS and region ordering. Old continental count files cannot bypass these checks;
the new pipeline rebuilds from the existing local source PBFs when necessary.

## Validation and rollout

Verified and switched on 2026-09-12. Six tests pass in
`tests/test_osm_checkpoint.py`, covering interrupted discovery/ways/nodes,
actual saved-offset seeking, an abrupt child-process exit inside a transaction,
geometry/CRS/region-order/input changes, known fixture counts, and constant-cost
progress snapshots after discovery. Completed frame-history hashes are cached
for progress reporting; explicit source verification still recomputes them.

The actual Europe probe committed frames 0–7, then resumed and committed
through frame 15. The second batch took 1.586 seconds after canonical-layer
setup, without repeating the initial full-file fingerprint scan.

A subsequent real worker restart preserved 41,176 committed frames
(2,821,332,391 input bytes) and advanced to 48,232 frames (3,321,822,892 bytes).
The receipt is `.cache/osm-recovery/restart-validation-20260912.json`.
These byte counts describe progress within discovery, not completion of all
three passes or a released environmental dataset.

The legacy `.cache/osm-recovery/start.ps1` now forwards to the tracked launcher.
The checkpointed Europe worker is active; no site publication was performed.
Runtime status can change after this dated verification, so confirm the live
process and advancing SQLite/manifest cursor for subsequent updates.
# BiomeSummary MVP Architecture

> **Analysis geometry:** offline regional reducers must preserve authoritative footprints and maintained corrections. Current exceptions and the seven-region fix are tracked in [Region boundary corrections](docs/REGION_BOUNDARY_CORRECTIONS.md).

## Data Flow

1. `./Ecoregions2017/Ecoregions2017.shp` is the source dataset.
2. `scripts/preprocess_ecoregions.py`:
   - repairs invalid polygon geometries
   - computes derived area (km²) + representative point
   - exports a cleaned intermediate feature set
   - runs `mapshaper` topology-preserving simplification
   - exports:
     - `frontend/public/data/lod0/ecoregions_lod0.topojson` (global startup layer)
     - `frontend/public/data/lod1_realms/*.topojson` (per-realm detail layers)
     - `frontend/public/data/regions.index.json` (metadata + geometry manifest)
3. Frontend (`frontend/public/`) loads:
   - metadata index first
   - land, marine, and lake overview TopoJSON layers
   - lazy-loads global LOD0 boundaries after zooming in; optional realm LOD1 is disabled by default
4. Cesium renders polygons and the sidebar reads summaries via a data service abstraction (`local` JSON now, API later).

## Why TopoJSON (Now)

- The first version used one large simplified GeoJSON, which was expensive to parse and created visible border seams.
- TopoJSON is smaller on the wire and stores shared edges once.
- `mapshaper` simplifies with topology awareness, which keeps neighboring region borders aligned instead of drifting apart.
- Cesium can ingest TopoJSON via `GeoJsonDataSource`, so we get the transport/quality improvement without adding a frontend build pipeline.

## LOD Strategy (Current)

- Startup loads three overview layers: land, marine, and lakes (7,105 polygon entities).
- After camera movement ends at or below 6,200 km altitude, global LOD0 is loaded for the visible datasets. The overview remains active for each dataset until its detailed layer is ready. Failed loads can be retried on a subsequent zoom or dataset change.
- Above 7,000 km, the overview becomes active again. The gap between thresholds prevents repeated switching near the boundary.
- Concurrent requests share an in-flight promise per layer. Completed layers stay cached; late responses use the current camera and dataset mode. Resetting geometry invalidates earlier requests.
- Only active sources participate in camera culling and per-entity visibility updates. Inactive cached data sources are hidden as a whole. Selection overlays are rebuilt when the active source changes.
- Hover uses a single pick with a 100 ms throttle and pauses during pointer dragging or camera movement. Clicks retain the full overlap and geometric fallback lookup.
- Moving resolution is 75% of the idle scale, restored when the camera settles.
- Per-realm LOD1 remains available behind configuration flags, but is currently disabled.

## Extending Region Summaries Later

The frontend already uses `frontend/public/js/regionDataService.js` with two modes:

- `local`: reads `regions.index.json`
- `api`: fetches `GET /api/regions/{id}`

To add real biodiversity summaries later:

1. Build backend enrichment jobs keyed by `region_id` / `ECO_ID`.
2. Store cached region summaries in a local DB or JSON artifacts.
3. Extend `GET /api/regions/{id}` with optional summary blocks (species counts, protected area stats, threats, last-updated).
4. Render those blocks conditionally in the sidebar.

The interaction model (hover/select/sidebar) stays stable while data sophistication grows behind the API.

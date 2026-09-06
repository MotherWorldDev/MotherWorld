# BiomeSummary MVP Architecture

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
   - LOD0 TopoJSON startup layer only
   - lazy-loads realm LOD1 TopoJSON on selection
4. Cesium renders polygons and the sidebar reads summaries via a data service abstraction (`local` JSON now, API later).

## Why TopoJSON (Now)

- The first version used one large simplified GeoJSON, which was expensive to parse and created visible border seams.
- TopoJSON is smaller on the wire and stores shared edges once.
- `mapshaper` simplifies with topology awareness, which keeps neighboring region borders aligned instead of drifting apart.
- Cesium can ingest TopoJSON via `GeoJsonDataSource`, so we get the transport/quality improvement without adding a frontend build pipeline.

## LOD Strategy (Current)

- `LOD0` (startup):
  - aggressively simplified
  - loads fast for whole-globe interaction
- `LOD1 by realm`:
  - medium detail
  - lazy-loaded on region selection
  - corresponding LOD0 realm features are hidden while LOD1 is active to avoid duplicate rendering/picking

This keeps startup responsive while preserving detail where the user is actively exploring.

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

# BiomeSummary MVP (Cesium + WWF/RESOLVE Ecoregions2017)

Fast, minimal biodiversity explorer MVP:

- 3D globe (CesiumJS)
- WWF/RESOLVE ecoregion boundaries from local `Ecoregions2017` shapefile
- biome-based coloring
- hover + click interactions
- modern right sidebar summary
- topology-preserving LOD geometry pipeline (TopoJSON)
- local JSON summary mode with optional FastAPI API scaffold

## Project Structure

```text
Ecoregions2017/                    # Source shapefile files (.shp/.shx/.dbf/.prj)
scripts/preprocess_ecoregions.py   # Geo preprocessing + topology LOD generation
frontend/public/                   # Static app (HTML/CSS/Vanilla JS + Cesium)
  data/
    lod0/                          # Startup TopoJSON
    lod1_realms/                   # Lazy-loaded per-realm TopoJSON
    regions.index.json             # Metadata + geometry manifest
backend/app/main.py                # Optional FastAPI summary API scaffold
requirements.txt                   # Python deps (preprocess + optional backend)
package.json                       # Node deps (mapshaper for topology simplification)
```

## What The Preprocessing Script Does

- Loads `Ecoregions2017.shp`
- Keeps useful fields (`ECO_ID`, `ECO_NAME`, biome/realm fields, NNH fields)
- Repairs invalid geometries (`make_valid` / polygon-only cleanup)
- Creates stable frontend region IDs (`eco_<ECO_ID>`)
- Computes derived area in km² (`EPSG:6933`)
- Computes representative point (for sidebar display)
- Builds topology-preserving simplified geometry with `mapshaper`
- Exports:
  - `frontend/public/data/lod0/ecoregions_lod0.topojson`
  - `frontend/public/data/lod1_realms/*.topojson`
  - `frontend/public/data/regions.index.json`

## Run (Frontend Only, Local JSON Mode)

1. Install Python dependencies:

```bash
python -m pip install -r requirements.txt
```

2. Install Node dependency used for topology simplification (`mapshaper`):

```bash
npm install
```

3. Generate web-ready data (required first run):

```bash
python scripts/preprocess_ecoregions.py
```

Optional tuning:

```bash
python scripts/preprocess_ecoregions.py --lod0-retain-pct 1.5 --lod1-retain-pct 10
```

- Lower retain % = more aggressive simplification (smaller/faster, less detail)
- Higher retain % = more detail (larger/slower, cleaner local shapes)

4. Build Black Marble night imagery assets (if source TIFFs changed):

```bash
python scripts/build_black_marble_tiles.py --levels 5 --tile-format jpg --quality 84 --base-image frontend/public/assets/imagery/black-marble-01deg.jpg
python scripts/build_black_marble_ultra_tiles.py --levels 6,7 --tile-format jpg --quality 82
```

- Expects source files in `./blackmarble/`:
  - `BlackMarble_2016_01deg_geo.tif` (global base)
  - `BlackMarble_2016_3km_geo.tif` (detail)
  - `BlackMarble_2016_A1_geo.tif` ... `BlackMarble_2016_D2_geo.tif` (ultra tiles)
- Recommended split: keep night base/detail as `jpg`. For smooth zoom/pan behavior, generate both ultra levels `6,7` (matching day-side strategy).

5. Stage sky textures (if `./Starmap` source files changed):

```bash
magick ./Starmap/starmap_2020_8k.exr -auto-level -colorspace sRGB ./frontend/public/assets/sky/starmap_2020_8k.jpg
magick ./Starmap/constellation_figures_8k.tif -colorspace sRGB ./frontend/public/assets/sky/constellation_figures_8k.png
```

- The app reads `frontend/public/assets/sky/*` at runtime (web-friendly JPG/PNG).
- Source `.exr` / `.tif` files can stay in `./Starmap` for future reconversion.

6. Start a local static server:

```bash
python -m http.server 8080 --directory frontend/public
```

7. Open:

```text
http://127.0.0.1:8080
```

## Optional Backend (FastAPI API Mode)

The frontend defaults to local metadata mode. You can also run the API scaffold now.

1. Start API server:

```bash
uvicorn backend.app.main:app --reload
```

2. Switch frontend summary mode in `frontend/public/js/config.js`:

```js
regionSummaryMode: "api"
```

3. Keep the static frontend server running (`http://127.0.0.1:8080`).

Available endpoints:

- `GET /api/health`
- `GET /api/regions/{region_id}`
- `GET /api/regions?q=amazon&biomeNum=1&limit=20`
- `GET /api/biomes`

## Performance Notes

- Startup loads only the land, marine, and lake overview TopoJSON layers (7,105 polygon entities).
- Global LOD0 boundaries load after the camera settles below 6,200 km. The overview stays selectable while loading; downloaded layers are reused. Above 7,000 km the overview becomes active again.
- Camera visibility checks scan only the active detail level. Cached inactive layers are hidden at the data-source level.
- Hover uses one pick at most every 100 ms and pauses during dragging/camera movement. Click selection retains the complete region lookup.
- While the camera moves, resolution drops to 75% of the idle scale and returns to full sharpness when movement ends.
- Region outlines remain enabled. Optional per-realm LOD1 detail is currently disabled in configuration.

## Notes

- Cesium is loaded from CDN (no frontend build step required).
- Area values are derived (equal-area projection) and intended for UI summaries, not legal/technical measurements.
- `mapshaper` may print intersection repair warnings during simplification; the main win here is topology-aware shared-edge simplification for display geometry.

## Architecture

See `ARCHITECTURE.md` for data flow, the GeoJSON vs TopoJSON decision, and how to extend the sidebar with real biodiversity summary data later.

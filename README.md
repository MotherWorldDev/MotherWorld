# BiomeSummary MVP (Cesium + WWF/RESOLVE Ecoregions2017)

> **Known regional data issue:** the seven-region boundary correction is included in species, PHYLACINE, and geology releases. Affected climate and pollution data rebuilds remain pending. See [the issue, verified impact, and remaining fix](docs/REGION_BOUNDARY_CORRECTIONS.md).

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

## Region content

Climate, Species, and Threats tabs use `frontend/public/data/region-content.json`: 13 regional profiles, fallbacks for all 14 land biomes and three marine zones, and a general lake overview. Content loads on selection and is cached. The generated geometry and metadata indexes remain separate.

The sidebar labels broad overviews and links regional references. See [CONTENT_SCHEMA.md](CONTENT_SCHEMA.md) to add or revise profiles. Run `npm test` to validate coverage and merging behavior.

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

## Recorded species

The Species tab loads GBIF land/lake and OBIS marine inventories on demand, with kingdom tabs, local search, class/family/record filters and sorting. An explicit common-name lookup resolves names against the region inventory. Opening a species loads a rich GBIF/WoRMS profile with sourced descriptions, traits, names, distribution reports, references and licensed photos where available. Profiles are fetched only on opening and cached for reuse. Recorded occurrences do not imply complete range coverage or population abundance. See [SPECIES_PIPELINE.md](SPECIES_PIPELINE.md) for the resumable builders and public taxonomy download, and [DATA_SOURCES.md](DATA_SOURCES.md) for provenance.

## Temperature distributions

The Climate tab loads compact empirical temperature histograms when opened for a region. It offers regional daily means and area-weighted space-time views, with statistics, source attribution, actual coverage dates and keyboard-accessible bin inspection. Land/lakes use ERA5-Land 2 m air temperature; marine regions use NOAA OISST sea-surface temperature. Short releases are marked as previews, and regions awaiting data show an availability message.

See [CLIMATE_PIPELINE.md](CLIMATE_PIPELINE.md) for credential setup, resumable builders and validation, and [CLIMATE_DATA_SOURCES.md](CLIMATE_DATA_SOURCES.md) for dataset references. The planned baseline is 1991–2020. Raw rasters and credentials stay local; only the manifest and per-region summaries are published.

## Unified environmental stack v8

The installed environmental modules add fixed nine-family historical Earth Health, lazy precipitation/humidity/wind normals, descriptive geology and seafloor context, and a Moon-only Selenology tab. Earth Health and all diagnostic panels remain source-gated: scaffold indexes do not claim values until their real provider builds pass validation. See [docs/README.md](docs/README.md) for the scoring, provenance, licensing, and Moon-texture contracts.

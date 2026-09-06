# Data and imagery in this repository

This repository contains the application source, preprocessing scripts, processed
TopoJSON geometry and metadata under `frontend/public/data`, and browser-ready
imagery under `frontend/public/assets`, including the day and night tile pyramids.
The application still uses the BiomeSummary name in its UI and package metadata.

## Run a checkout

The processed runtime data is committed, so preprocessing is not required just to
open the application:

```sh
python -m http.server 8080 --directory frontend/public
```

Open http://127.0.0.1:8080. Cesium and the web fonts are loaded from external CDNs.
See `README.md` for the optional API and preprocessing commands.

## Source assets kept outside Git

The original workspace also contains several gigabytes of source data. These
files remain local and are excluded by `.gitignore`:

- `Ecoregions2017/`: terrestrial ecoregion shapefiles.
- `Marine Ecoregions of the World/`: marine source GIS data.
- `HydroLAKES_polys_v10_shp/`: lake source GIS data.
- `Bluemarble/` and `Blackmarble/`: source day and night imagery.
- `NasaMoonMap/` and `Starmap/`: original Moon and sky textures.
- `BlackMarbleTiles/`: local auxiliary source data.
- Original TIFF/EXR rasters and the root `PIA26681.jpg` source image.

To regenerate an asset, obtain its original dataset and restore the input paths
expected by the relevant script in `scripts/`. Run terrestrial preprocessing
before marine preprocessing, which uses the generated terrestrial coverage.
The current lake script selects 21 logical lake regions from HydroLAKES.

Dependencies (`node_modules` and virtual environments), Python caches, local
configuration, and temporary preprocessing directories are also excluded.
The original data providers' licenses and attribution requirements continue to
apply to the derived assets.

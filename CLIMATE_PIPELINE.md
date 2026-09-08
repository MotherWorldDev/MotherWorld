# MotherWorld temperature-distribution pipeline

> **Regional geometry:** seven land regions need the maintained analysis-boundary corrections before temperature/climate generation. See [the issue and required propagation](docs/REGION_BOUNDARY_CORRECTIONS.md).

## What gets computed

The Climate tab gets two empirical distributions, not a fitted Gaussian.

### 1. Regional mean over time

For each day `t`:

`T_region(t) = sum_i(T_i(t) * A_i * F_i) / sum_i(A_i * F_i)`

where:

- `T_i(t)` is the grid-cell daily temperature,
- `A_i` is the grid-cell surface area,
- `F_i` is the fraction of that cell inside the region.

A complete 1991–2020 baseline contains 10,958 days. Available daily means are histogrammed and normalized to 100%; missing cells are omitted from both sums. Every payload records its actual requested years and valid-day count. Releases shorter than 30 years are explicitly labelled as previews in the interface. This answers:

> What percentage of days had a region-wide mean temperature in this range?

### 2. Space × time

Every valid grid-cell/day temperature is histogrammed with weight `A_i * F_i`.

This answers:

> Across the whole region and the whole baseline, what fraction of weighted space-time experienced this temperature range?

This second view preserves altitude/latitude/coastal heterogeneity that gets hidden by a single region-wide daily mean.

## Boundary weighting

The implementation uses sub-cell rasterization (4×4 by default) to estimate each climate grid cell's fractional intersection with the ecoregion polygon. Grid cells are additionally weighted by physical surface area, which matters strongly toward the poles on latitude/longitude grids.

Increase to `--supersample 8` if you want better treatment of small islands or narrow coastal regions at the cost of preprocessing CPU.

## Land/lake builder

`scripts/build_land_temperature.py` retrieves ERA5-Land daily mean 2 m temperature from CDS one region/year at a time and processes it immediately. Raw downloads are deleted by default after processing each component. Compressed per-year checkpoints retain the histogram, daily means and weighted moments, so completed years resume without downloading again. A failed or interrupted component may need retrieval again; a checkpoint is committed only after the full region/year is processed.

Widely separated multipart regions are requested component-by-component to avoid downloading enormous empty bounding boxes.

Example smoke test:

```bash
python scripts/build_land_temperature.py \
  --region eco_689 \
  --years 2019:2020 \
  --keep-raw
```

Full baseline:

```bash
python scripts/build_land_temperature.py --years 1991:2020
```

For a lighter 0.25° implementation:

```bash
python scripts/build_land_temperature.py --source era5 --years 1991:2020
```

## Marine builder

`scripts/build_marine_temperature.py` downloads each OISST yearly NetCDF file once, builds fractional ecoregion weights on the OISST grid, then streams the data in chunks. Each time chunk (7 days by default) is read once and reused across all marine regions. Raw annual files are roughly 450 MiB each and are removed after their processed checkpoint is saved, unless `--keep-raw` is set. Download retries use HTTP byte ranges when the server supports them. Grid weights and annual aggregates are cached; resuming a build reuses completed years.

Example smoke test:

```bash
python scripts/build_marine_temperature.py \
  --region marine_meow_20192 \
  --years 2019:2020
```

Full baseline:

```bash
python scripts/build_marine_temperature.py --years 1991:2020
```

When processing the complete marine set, or selecting `--region open_ocean`, the builder also creates `open_ocean` as the residual OISST grid-cell fraction outside all mapped marine ecoregions. Selecting open ocean still uses the complete region boundary set to calculate this residual. Invalid/land OISST cells contribute nothing.

## Setup and resume

Install `requirements.txt` in a Python environment with NumPy, xarray, netCDF4, GeoPandas, rasterio and cdsapi. The current local climate environment is `.cache/climate-venv`; it does not change the Python environment used by the species builder.

For CDS, save the signed-in configuration from <https://cds.climate.copernicus.eu/how-to-api> in your user `.cdsapirc` and accept the ERA5-Land dataset terms in the browser. Alternatively, set `CDSAPI_RC` to the local configuration file. Never commit or publish that file. NOAA needs no account. The local `CpernicusToken` folder is explicitly ignored by Git.

Raw files, weight tables, locks and processed checkpoints live under `.cache/motherworld/climate/`. Fingerprints include the source, bins, boundary geometry and sampling settings; marine yearly checkpoints can be reused when only the baseline dates change. `--force` rebuilds the processed results. Run one builder per provider/cache at a time. Land and marine builders may run together: atomic JSON replacement and a manifest lock protect their shared index. No builder pushes or deploys its output.

Validation:

```bash
python -m unittest discover -s tests -p test_temperature_pipeline.py -v
npm test
```

The pipeline checks coordinate grids, time stamps, explicit temperature units and histogram mass. Values outside the configured bins fail the build rather than silently dropping samples. Every NetCDF member of a CDS archive is combined, including archives split by month.

## Output schema

Each region output is deliberately frontend-small:

```text
frontend/public/data/climate/
  climate.index.json
  land/eco_1.temperature.json
  lakes/lake_superior.temperature.json
  marine/marine_meow_20192.temperature.json
  marine/open_ocean.temperature.json
```

Raw daily rasters are never shipped to the browser.

## Statistical caveats

- These are empirical reanalysis/analysis distributions, not direct station-only observations.
- `regionalDailyMean` weights every valid day equally.
- `spaceTime` weights every valid grid-cell/day by covered physical area.
- Space-time percentiles are reconstructed from the histogram bins and are therefore approximate at the selected bin width.
- ERA5-Land is atmospheric 2 m air temperature. OISST is sea-surface water temperature. The UI source label makes this difference explicit.
- Region geometry comes from MotherWorld's committed detailed LOD0 display geometry; pieces sharing a logical region ID are merged. If you restore the original source GIS polygons locally, you can adapt `temperature_common.py` to load those for even better boundary fidelity.

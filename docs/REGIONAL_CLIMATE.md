# Regional Climate normals — precipitation, humidity and wind

This module extends MotherWorld's existing temperature-distribution Climate tab without changing the temperature pipeline or the fixed Earth Health methodology.

## Baseline and scopes

- Baseline: **1991–2020**.
- Land and currently selected large lakes: ECMWF ERA5-Land Daily Aggregated.
- Marine ecoregions: ECMWF ERA5 Daily.
- `open_ocean`: visually remains the residual selection, but these non-species metrics are calculated as a **whole-ocean aggregate** using the global ocean mask, consistent with the MotherWorld ocean-scope rule.

## Precipitation

Daily total precipitation is converted from metres to millimetres and tiny negative values are clamped to zero. The public payload contains:

- area-weighted average annual precipitation (mm/year),
- 12-month precipitation climatology (mm/month),
- area × day distribution of daily precipitation,
- wet area-days/year (>=1 mm/day),
- heavy-rain area-days/year (>=10 mm/day),
- approximate P95/P99 daily precipitation reconstructed from histogram bins.

"Area-days" is deliberate: it avoids pretending a large heterogeneous ecoregion has one single rain gauge.

## Relative humidity

Relative humidity is derived from daily-mean 2 m air temperature and daily-mean 2 m dew-point temperature with a Magnus saturation-vapour-pressure approximation. Because RH is nonlinear, this is labelled as a **daily-mean-state RH estimate**, not the exact average of 24 hourly RH values.

The payload contains monthly RH climatology, an area × day distribution, mean RH and approximate P10/median/P90.

## Wind

Daily-mean 10 m u/v wind components are converted to horizontal speed and meteorological direction-from. The payload contains:

- monthly mean wind speed,
- area × day wind-speed distribution,
- approximate median/P90/P99 speed,
- calm share (<1 m/s),
- strong-wind area-days/year (>=10 m/s),
- 16-sector area × day wind rose and prevailing sector.

This is a climatology of **daily mean wind**, not gusts. Gust extremes can be added later as a separate diagnostic.

## Efficient Earth Engine reduction

The builder first collapses the 30-year daily collection into summary raster bands (histogram-bin frequencies, monthly normals and threshold counts). It then performs area-weighted regional reductions on those summary images. This avoids a separate region reduction for every day and makes the 847+ land / lakes / 232 marine workload tractable.

For marine metrics, summary bands are multiplied by a fractional ocean mask derived from ERA5-Land static `land_sea_mask` and `lake_cover`. The global `open_ocean` payload uses a world geometry with that ocean fraction, so it includes the complete ocean system.

## Build

```bash
python scripts/build_all_climate_extras.py \
  --project YOUR_GOOGLE_CLOUD_PROJECT \
  --years 1991:2020
```

Useful controls:

- `--region ID` (repeatable) — smoke tests / partial rebuilds;
- `--kinds land,lakes,marine` — select groups;
- `--chunk-size 12` — regions per EE reduction request;
- `--tile-scale 4` — Earth Engine memory tradeoff;
- `--simplify-deg 0.015` — topology-preserving upload simplification;
- `--force` — rebuild existing payloads.

The builder is resumable at the region JSON level and updates `climate-extras.index.json` after every chunk.

## Direct CDS regional acquisition

The offline [`download_regional_climate_cds.py`](../scripts/download_regional_climate_cds.py) acquires ERA5-Land daily temperature, dew point, and 10 m wind components for canonical land/lake regions, or ERA5 daily data for marine regions. Month and variable selections bound request cost. It uses the maintained seven-region land footprints, records the actual request and analysis geometry, validates daily date coverage and NetCDF dimensions/units, and validates staged downloads before replacing existing files. Matching partial downloads can resume. Source commit: `83b8d26b`.

The January 1991 `eco_689` pilot passed validation for all four variables and 31 daily records. This confirms acquisition only: it is not a complete 1991–2020 regional climatology, does not provide precipitation, and is not included as a scored historical series. Downstream reductions still require the full requested temporal coverage and their own geometry/provenance checks. Google Earth Engine is not required for this acquisition route.

Validation: 26 combined downloader/geometry tests, including six focused regional tests and six invalid-month subtests. The real pilot was checked read-only. Raw inputs, request sidecars, and credentials remain local outside the published data tree.

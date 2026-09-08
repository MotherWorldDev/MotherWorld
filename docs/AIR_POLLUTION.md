# Pollution pipeline

## Annual aggregation

For each metric and year:

1. Build an annual-mean raster.
2. Reduce that raster over each MotherWorld region polygon.
3. Store the regional mean and the spatial 90th percentile.
4. Repeat across years.
5. Fit a simple least-squares trend to annual regional means and express it per decade.

## PM2.5

CAMS includes multiple forecast lead times per initialization. The script filters to:

```text
model_initialization_hour = 0
model_forecast_hour = 0
```

before calculating each annual mean. This avoids treating all forecast lead times as separate equally weighted observations.

## Sentinel-5P gases

The OFFL products are used rather than NRTI because the goal is a historical summary layer, not a live alert system.

The relevant metric is spatially averaged over the region after temporal annual averaging. This keeps the computational workload tractable for roughly a thousand regions.

## Spatial hotspot metric

`spatialP90` is the 90th percentile of pixel values inside the annual-mean raster. It means:

> 90% of the region's mapped annual-mean pixel values are at or below this value.

It is **not** the 90th percentile of daily exposure through time.

## Regional percentile

After every selected region is processed, the latest regional means are ranked within each metric. The resulting `regionalPercentile` helps users compare regions while keeping incompatible pollutant units separate.

## Resumability

Every metric/year/batch result is cached under:

```text
pollution_raw/earthengine_START_END/METRIC/YEAR/batch_XXXX.json
```

Reruns reuse completed batches unless `--no-resume` is supplied.

## Future water-pollution extension

This schema can accept non-atmospheric sections later without changing the Pollution tab itself. Candidate layers include:

- chlorophyll-a / eutrophication proxies
- turbidity / suspended matter
- harmful algal bloom products
- coastal plastics where a defensible spatial dataset is available
- river nutrient loads or wastewater pressure

Those should remain separate from atmospheric pollution metrics rather than being blended into one arbitrary number.

## Earth Health historical backbone (v3)

The detailed CAMS and Sentinel-5P/TROPOMI products remain present-day diagnostics. They do **not** determine the Earth Health headline. Earth Health uses one consistent annual global MERRA-2 PM2.5 reanalysis backbone for every historical/current comparison; TROPOMI NO2, SO2, CO and aerosol diagnostics stay visible when the current Pollution family is expanded.

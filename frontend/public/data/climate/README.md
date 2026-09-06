# Generated climate data

`climate.index.json` maps MotherWorld region IDs to compact precomputed temperature-distribution JSON files.

The builders create:

- `land/<region>.temperature.json` — ERA5-Land/ERA5 2 m air temperature
- `lakes/<region>.temperature.json` — ERA5-Land/ERA5 2 m air temperature
- `marine/<region>.temperature.json` — NOAA OISST sea-surface temperature
- `marine/open_ocean.temperature.json` — residual ocean outside mapped marine ecoregions

Each region contains two normalized empirical distributions:

1. `regionalDailyMean`: one area-weighted regional mean per day, histogrammed over time.
2. `spaceTime`: all grid-cell/day temperatures, weighted by region fraction and grid-cell surface area.

Raw climate downloads belong outside `frontend/public` and are not required by the browser after preprocessing.

The planned baseline is 1991–2020. Read each payload's `baseline` and `sampleCountDays` for actual coverage; shorter releases are previews. `queryFingerprint` ties results to the source, dates, bins, boundary geometry and weighting settings. Mean and standard deviation use weighted temperature moments; space-time percentiles are approximated from histogram bins.

The initial marine preview covers 2019 for all 232 mapped marine regions plus open ocean. Land/lake entries are added as CDS processing completes. The index is the authoritative list of currently included outputs.

# Climate data sources

## Land and lakes — ERA5-Land daily statistics

Primary implementation target:

- Dataset: **ERA5-Land post-processed daily statistics from 1950 to present**
- CDS dataset ID: `derived-era5-land-daily-statistics`
- Variable: `2m_temperature`
- Aggregation: `daily_mean`
- Native/regridded spatial resolution: 0.1° × 0.1° (about 9 km native land resolution)
- Temporal coverage: 1950–present
- DOI: `10.24381/cds.e9c9c792`
- Catalogue: https://cds.climate.copernicus.eu/datasets/derived-era5-land-daily-statistics

The pack also supports lower-resolution standard ERA5 by passing `--source era5` to `build_land_temperature.py`.

CDS requires an account/API token and may require accepting dataset terms in the browser before API retrieval works.

## Marine — NOAA OISST v2.1

- Dataset: **NOAA Optimum Interpolation Sea Surface Temperature v2.1**
- Variable: daily `sst`
- Spatial resolution: 0.25°
- Temporal coverage: 1 September 1981–present
- Source product page: https://www.ncei.noaa.gov/products/optimum-interpolation-sst
- Yearly NetCDF mirror used by the implementation: https://downloads.psl.noaa.gov/Datasets/noaa.oisst.v2.highres/

The yearly files are named `sst.day.mean.YYYY.nc`.

## Baseline

The default package baseline is **1991–2020**, a conventional 30-year climate-normal period. Both distributions use the same daily source values and baseline so the two views are directly comparable.

## MotherWorld processing and release coverage

The public Climate tab links to `frontend/public/climate-sources.html` for methods and attribution. It uses committed LOD0 land, lake and marine boundaries, merging all features with each logical region ID. Cells are weighted by spherical area and estimated fractional region coverage (4 × 4 rasterization by default).

Every inventory identifies its requested years and valid-day count. Shorter than 30 years is displayed as a preview; the availability of a preview does not imply that the planned 1991–2020 baseline has finished. Raw rasters and local CDS credentials are excluded from publication. CDS terms must be accepted manually before retrieval.

# Additional global-system sources

## Greenhouse forcing

NOAA Global Monitoring Laboratory:

- Annual Greenhouse Gas Index: `https://gml.noaa.gov/aggi/AGGI_Table.csv`
- Global annual CO₂: `https://gml.noaa.gov/webdata/ccgg/trends/co2/co2_annmean_gl.csv`
- Global annual CH₄: `https://gml.noaa.gov/webdata/ccgg/trends/ch4/ch4_annmean_gl.csv`
- Global annual N₂O: `https://gml.noaa.gov/webdata/ccgg/trends/n2o/n2o_annmean_gl.csv`

AGGI is the scored headline because it already combines effective radiative forcing from long-lived greenhouse gases. Individual gases remain visible context.

## Cryosphere

NOAA/NSIDC Sea Ice Index v4 daily extent:

- North: `https://noaadata.apps.nsidc.org/NOAA/G02135/north/daily/data/N_seaice_extent_daily_v4.0.csv`
- South: `https://noaadata.apps.nsidc.org/NOAA/G02135/south/daily/data/S_seaice_extent_daily_v4.0.csv`

The unified builder compares annual minimum extent to the 1981–2010 mean annual minimum. Optional staged Greenland, Antarctic and glacier time series are retained as context until calibrated component normalizations are chosen.

## Global Ocean

Recommended inputs:

- NOAA 0–2000 m World Ocean Heat Content time series
- Copernicus Marine global mean sea-water pH indicator, dataset `global_omi_health_carbon_ph_area_averaged`
- satellite global mean sea level (NASA/NOAA/University of Colorado source, normalized to a consistent baseline before scoring)

The ocean builder accepts standardized CSV/text/NetCDF inputs and always records the full-ocean metric scope.

## Global Freshwater Stability

### JPL GRACE / GRACE-FO Mascon terrestrial water storage

Recommended collection:

- `TELLUS_GRAC-GRFO_MASCON_CRI_GRID_RL06.3_V4`
- DOI: `10.5067/TEMSC-3JC634`
- monthly, global, 0.5° delivered grid
- variable: `lwe_thickness` in cm equivalent water thickness
- 2002-present

MotherWorld excludes Antarctica and Greenland from the scored global terrestrial-water-storage reduction to avoid double-counting ice-sheet loss already represented in Cryosphere.

### ERA5-Land monthly soil moisture

Earth Engine collection:

- `ECMWF/ERA5_LAND/MONTHLY_AGGR`
- monthly, ~11.1 km source grid
- 1950-near real time
- bands: `volumetric_soil_water_layer_1`, `_2`, `_3`

MotherWorld calculates a depth-weighted 0–100 cm root-zone approximation and compares recent pixel/month values to their own 1991–2020 monthly P10–P90 envelope.

### JRC Global Surface Water

Earth Engine collection:

- `JRC/GSW1_4/YearlyHistory`
- annual, 30 m
- 1984–2021
- band: `waterClass` (not water / seasonal / permanent)

The source is provided under the Copernicus Programme with acknowledgement requirements.


## Historical Earth Health backbone (v3+, unchanged in v4)

Earth Health scoring backbones are isolated under `data/indices/families/`; rich current summaries are under `data/indices/diagnostics/`. The historical slider begins at 1993 and only exposes the continuous intersection of all enabled backbone annual series. See `docs/HISTORICAL_BACKBONES.md`.


## Regional climate diagnostics (v4)

These do not contribute to Earth Health. They populate the regional Climate tab for the fixed 1991–2020 climatological baseline.

- **ERA5-Land Daily Aggregated** (`ECMWF/ERA5_LAND/DAILY_AGGR`) — land/lake precipitation, daily-mean temperature/dew point for derived RH, and daily-mean 10 m wind at ~11.1 km.
- **ERA5 Daily** (`ECMWF/ERA5/DAILY`) — marine precipitation, derived RH and daily-mean 10 m wind at ~27.8 km.
- **ERA5-Land Static** (`ECMWF/ERA5_LAND/STATIC`) — fractional land/lake mask used to area-weight marine and whole-ocean summaries.

Only compact regional climatology aggregates are published; source rasters remain in the provider platforms/caches.

## Regional geology + seafloor (v6)

Geology is not an Earth Health family. Raw sources are staged under `geology_raw/` or provider caches and reduced to compact regional JSON. See `docs/GEOLOGY_SOURCES.md`.



## Seafloor geology

- GEBCO_2026 Grid — bathymetry/elevation, 15 arc-second native grid, public domain with attribution requested.
- Seton et al. (2020) / EarthByte — present-day oceanic crust age and spreading parameters, CC BY 4.0.
- GlobSed — global 5 arc-minute total sediment thickness, PANGAEA archive CC BY 4.0.
- EarthByte / GPlates — spreading-ridge / seafloor tectonic features, CC BY 3.0 for EarthByte GeoData.
- InterRidge Vents v3.4 — hydrothermal vent fields, CC BY-NC-SA 4.0; raw stays private.
- CRUST1.0 / EarthScope EMC — optional crystalline crust thickness, provider/model attribution terms.

## Moon Selenology

The Moon-only Selenology surface mode uses the USGS Astrogeology Science Center **Unified Geologic Map of the Moon, 1:5,000,000, version 2 (2020)** by Fortezzo, Spudis and Harrel. The source is globally consistent and CC0. MotherWorld's builder converts the official global colored raster into 4K/8K WebP textures while preserving the existing live Moon illumination.

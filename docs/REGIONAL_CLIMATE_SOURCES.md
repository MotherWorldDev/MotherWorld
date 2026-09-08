# Regional Climate sources

## ERA5-Land Daily Aggregated

Earth Engine asset: `ECMWF/ERA5_LAND/DAILY_AGGR`.

Used for land and lake precipitation, relative humidity and wind. Required bands:

- `temperature_2m` — daily mean 2 m air temperature, K
- `dewpoint_temperature_2m` — daily mean 2 m dew point, K
- `u_component_of_wind_10m`, `v_component_of_wind_10m` — daily mean wind, m/s
- `total_precipitation_sum` — daily accumulated precipitation, m

ERA5-Land extends from 1950 to near real time at roughly 11.1 km, but MotherWorld fixes the regional climatology to 1991–2020 so it aligns with the temperature baseline.

## ERA5 Daily

Earth Engine asset: `ECMWF/ERA5/DAILY`.

Used for marine ecoregions and whole-ocean precipitation/humidity/wind normals. Required bands:

- `mean_2m_air_temperature`
- `dewpoint_2m_temperature`
- `u_component_of_wind_10m`, `v_component_of_wind_10m`
- `total_precipitation`

The precomputed Earth Engine daily asset covers complete calendar years through 2019 but currently ends in July 2020. The MotherWorld builder therefore reconstructs **calendar year 2020 from `ECMWF/ERA5/HOURLY`** (daily means for T/Td/u/v and a daily sum for hourly `total_precipitation`) before calculating the 1991–2020 climatology. This avoids a partial final year.

## Ocean mask

Earth Engine asset: `ECMWF/ERA5_LAND/STATIC`, bands `land_sea_mask` and `lake_cover`. MotherWorld derives a fractional open-ocean weight as `clamp(1 - land_sea_mask - lake_cover, 0, 1)` for marine area weighting.

## Reanalysis caveat

ERA5 and ERA5-Land are physically consistent reanalyses combining model physics and observations. They are not station-only measurements. Regional output is therefore described as **climate reanalysis climatology**.

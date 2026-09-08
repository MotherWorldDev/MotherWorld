# Pollution data sources

## CAMS Global Near-Real-Time

Earth Engine collection:

`ECMWF/CAMS/NRT`

Metric used:

`particulate_matter_d_less_than_25_um_surface`

Native catalog units: kg/m³. MotherWorld converts this to µg/m³ by multiplying by `1e9`.

The Earth Engine catalog describes a pixel size of roughly 44.5 km and availability beginning in June 2016. To avoid double-counting overlapping forecast lead times, the implementation uses model initialization hour `0` and forecast hour `0` before annual averaging.

## Sentinel-5P TROPOMI OFFL L3

Earth Engine collections:

- `COPERNICUS/S5P/OFFL/L3_NO2`
- `COPERNICUS/S5P/OFFL/L3_SO2`
- `COPERNICUS/S5P/OFFL/L3_CO`
- `COPERNICUS/S5P/OFFL/L3_AER_AI`

Metrics:

- tropospheric NO₂ column number density — mol/m²
- SO₂ column number density — mol/m²
- CO column number density — mol/m²
- absorbing aerosol index — dimensionless index

Sentinel-5P coverage begins in 2018; the default MotherWorld trend window starts in 2019 so years are complete.

## Why no single combined score

These products do not share the same physical unit or sampling model. A single weighted average of PM2.5, NO₂ columns, SO₂ columns, CO columns, and aerosol index would look scientific while being arbitrary. The UI therefore reports each metric separately and adds a relative regional percentile only within each metric.

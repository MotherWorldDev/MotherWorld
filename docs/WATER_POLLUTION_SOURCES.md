# Water-pollution / water-quality data sources

## Copernicus Satellite Ocean Color V6

Earth Engine collection:

`COPERNICUS/MARINE/SATELLITE_OCEAN_COLOR/V6`

- global ocean;
- daily;
- 4 km;
- 1997 onward;
- `chlor_a` in mg/m³;
- CC-BY;
- DOI `10.24381/cds.f85b319d`.

Used for the long-term marine chlorophyll-a/eutrophication indicator.

## Copernicus Marine GlobColour

Product: `OCEANCOLOUR_GLO_BGC_L4_MY_009_104`

Monthly 4 km multi-year datasets used by the full-history builder:

- `cmems_obs-oc_glo_bgc-plankton_my_l4-multi-4km_P1M` — CHL;
- `cmems_obs-oc_glo_bgc-transp_my_l4-multi-4km_P1M` — SPM, KD490, ZSD;
- `cmems_obs-oc_glo_bgc-optics_my_l4-multi-4km_P1M` — CDM.

Product DOI: `10.48670/moi-00279`.

The service covers the global ocean from 1997 onward. SPM is supplied in g/m³; KD490/CDM in m⁻¹; ZSD in metres.

## Copernicus Land Monitoring Service — Lake Water Quality

Recommended global lake source: 300 m, 10-daily.

Version 2 (2024-present) includes:

- turbidity (NTU);
- total suspended matter (g/m³);
- Trophic State Index;
- chlorophyll-a;
- floating cyanobacteria risk;
- quality information.

Dataset DOI: `10.2909/801137b8-9575-43ef-a073-140b663cc61c`.

Earlier 300 m v1 provides global lake turbidity/TSI/surface-reflectance history for 2016-2024, with a separate 2002-2012 archive.

CDSE storage path for current 300 m v2:

`/eodata/CLMS/bio-geophysical/lake_water_quality/lwq-nrt_global_300m_10daily_v2`

## Sentinel-2/JRC proxy fallback

Earth Engine:

- `COPERNICUS/S2_SR_HARMONIZED`
- `JRC/GSW1_4/GlobalSurfaceWater`

Used only when official CLMS lake products have not been staged. The generated NDTI/NDCI/reflectance values are optical indices, not calibrated pollutant or water-quality concentrations.

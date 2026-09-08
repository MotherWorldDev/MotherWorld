# Water-pollution pipeline architecture

## Data model

Each region file contains independent metrics:

```json
{
  "regionId": "marine_example",
  "regionKind": "marine",
  "metrics": {
    "chlorophyll_a": {
      "unit": "mg/m³",
      "stressDirection": "higher",
      "latest": {"year": 2025, "mean": 0.42, "stressPercentile": 1.2},
      "annual": [],
      "trendPerDecade": 0.03,
      "regionalStressPercentile": 68
    }
  }
}
```

`stressDirection` matters. High turbidity/CHL/KD490/SPM are generally the stressward direction, while **low** Secchi depth is the stressward direction.

No unit-normalized universal score is calculated.

## Marine Earth Engine backend

`scripts/build_marine_water_quality_ee.py`

- historical chlorophyll-a: `COPERNICUS/MARINE/SATELLITE_OCEAN_COLOR/V6`, 4 km, daily, 1997+;
- current GlobColour transparency: KD490 + ZSD;
- current GlobColour optics: CDM + BBP.

For each requested year the source imagery is averaged to an annual raster, then MotherWorld polygons receive:

- spatial mean;
- p90 spatial stress value, or p10 when lower values are worse;
- first→last change;
- linear trend per decade;
- cross-region stress percentile for generated peer regions.

## Marine Copernicus Marine Toolbox backend

`scripts/build_marine_water_quality_cmems.py`

Uses monthly 4 km multi-year products:

```text
cmems_obs-oc_glo_bgc-plankton_my_l4-multi-4km_P1M
  CHL

cmems_obs-oc_glo_bgc-transp_my_l4-multi-4km_P1M
  SPM, KD490, ZSD

cmems_obs-oc_glo_bgc-optics_my_l4-multi-4km_P1M
  CDM
```

The service is lazily subset to each marine polygon's bounding box rather than downloading the world ocean archive.

## Lake official backend

`scripts/build_lake_water_quality_clms.py`

The importer expects staged Copernicus Land Monitoring Service Lake Water Quality GeoTIFF/COG files. It recognizes metric tokens in filenames:

- `TUR` → turbidity;
- `TSM` → total suspended matter;
- `TSI` → Trophic State Index;
- `CHL`/`CHLA` → chlorophyll-a;
- `CYAN` → cyanobacteria risk.

Dates are detected from `YYYYMMDD`, `YYYY-MM-DD`, or `YYYYMM` patterns in the file path.

The importer applies raster scale/offset metadata, nodata masks, lake-polygon clipping, and annualizes the 10-day observations.

## Lake fallback

`scripts/build_lake_water_quality_s2_proxy_ee.py`

Uses cloud-screened Sentinel-2 Surface Reflectance and the JRC Global Surface Water mask to derive:

- NDTI = `(red - green) / (red + green)` as a relative turbidity proxy;
- NDCI = `(red-edge - red) / (red-edge + red)` as a relative algal/chlorophyll proxy;
- red reflectance as an additional particulate/turbidity optical proxy.

These metrics are deliberately labelled `proxy` and do not use NTU, chlorophyll concentration, or TSM concentration units.

## Quality tiers

Recommended UI interpretation:

- `official-calibrated`: official CLMS/CMEMS derived physical products;
- normal satellite indicator: standardized Copernicus ocean-colour fields;
- `proxy`: spectral index without a globally calibrated concentration retrieval.

## Future extensions

Water pollution not observable from ordinary satellite colour should remain separate. Potential future datasets include:

- river nutrient loads and wastewater discharge;
- in-situ nitrate/phosphate/dissolved oxygen;
- heavy metals;
- PFAS/pesticides;
- plastic/microplastic observations;
- oil-spill detection;
- beach/bathing-water microbiology.

Those should be attached as their own metrics with source coverage/confidence rather than inferred from chlorophyll or turbidity.

# Staging geology data

The geology builders intentionally do not bundle upstream raw datasets. Put downloads under `geology_raw/`; that directory and the processed provider cache are gitignored.

Suggested source pages and filename patterns:

| Provider | Official source | Suggested staged filename |
|---|---|---|
| GLiM | PANGAEA DOI `10.1594/PANGAEA.788537` | `glim.csv`, `glim.gpkg`, `glim.shp` or `glim.geojson` |
| Macrostrat | `https://macrostrat.org` / API | no staged file; use `--macrostrat` |
| USGS MRDS | `https://mrdata.usgs.gov/mrds/` | `mrds.csv` or `mrds.xlsx` |
| GEM Global Active Faults | `https://github.com/GEMScienceTools/gem-global-active-faults` | `gem_active_faults.geojson`, `.gpkg` or `.shp` |
| Smithsonian GVP | `https://volcano.si.edu/database/` | `gvp_volcanoes.xlsx` and optionally `gvp_eruptions.xlsx` |
| IHFC Global Heat Flow | DOI `10.5880/fidgeo.2024.014` | `global_heat_flow.xlsx` / `.csv` |
| Confirmed impact structures | manually reviewed provider | `confirmed_impacts.csv` / `.xlsx` |
| USGS ComCat | `https://earthquake.usgs.gov/earthquakes/search/` | `comcat_earthquakes.csv` |

The orchestrator auto-detects those broad patterns:

```bash
python scripts/build_all_geology.py
```

Macrostrat is deliberately opt-in because it performs live API requests per region:

```bash
python scripts/build_all_geology.py --macrostrat
```

## Provider-specific caveats

- GVP raw files stay outside the deployed/public tree; the publication policy exposes only regional derived output.
- The impact importer is a replaceable staging adapter. Until the Meteoritical Society Impact Cratering Committee publishes an authoritative machine-readable confirmed list with clear reuse terms, review the chosen provider before public deployment.
- MRDS occurrence counts are not reserve estimates.
- Heat-flow observations do not by themselves establish geothermal power potential.

## Seafloor staging

| Provider | Official source / identifier | Suggested staged filename |
|---|---|---|
| GEBCO_2026 | GEBCO global grid, DOI `10.5285/4f68d5c7-45eb-f999-e063-7086abc036fa` | `GEBCO_2026.nc` |
| Seton/EarthByte age grid | EarthByte / Zenodo `10.5281/zenodo.6782543` | `seton2020_age.nc` |
| Seton/EarthByte spreading rate | same archive | `seton2020_rate.nc` |
| GlobSed | PANGAEA `10.1594/PANGAEA.982339` / rescued GlobSed grid | `GlobSed.grd` |
| EarthByte spreading ridges | GPlates GeoData / SpreadingRidges feature collection | `earthbyte_spreading_ridges.shp` (or `.gpkg` / `.geojson`) |
| Trench/subduction lines | reviewed EarthByte/GPlates or other open global line layer | `earthbyte_trenches.shp` (or `.gpkg` / `.geojson`) |
| InterRidge vents | PANGAEA `10.1594/PANGAEA.917894` | `interridge_vents.tsv` / `.tab` / `.csv` |
| CRUST1.0 | EarthScope EMC CRUST1.0 NetCDF | `CRUST1.0-rho.nc` or another EMC file containing the layer-thickness variables |

The orchestrator auto-detects these broad patterns after archives are extracted:

```bash
python scripts/build_all_geology.py
```

The GEBCO global NetCDF is several gigabytes. It is **not** copied into the web app. `build_gebco_bathymetry.py` reads it and downsamples to a configurable statistics grid (`--target-step-deg`, default `0.10`) before polygon aggregation.

For a single marine-region development run:

```bash
python scripts/build_gebco_bathymetry.py \
  --input geology_raw/GEBCO_2026.nc \
  --region marine_meow_20192
```


### Optional impact-structure detail columns

`build_impact_structures.py` accepts the existing name/latitude/longitude/diameter/age fields and also recognizes optional columns for `age_min_ma`, `age_max_ma`, `age_uncertainty_ma`, `impactor_name`, `impactor_type`, `impactor_diameter_km`, impactor diameter min/max/uncertainty, and a citation/reference. Simple source age strings such as `35.7 ± 0.4` or `35.3-36.1` are preserved as uncertainty/ranges rather than collapsed to fake precision.

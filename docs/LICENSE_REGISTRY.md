# MotherWorld unified source / publication registry

Internal engineering note — not legal advice. Provider ingestion is intentionally separated from public index publication.

| Provider | Role | Internal class | Default public mode | Removable |
|---|---|---|---|---|
| NASA MOD44B.061 | vegetation cover | open/public | aggregate metrics | yes |
| Google Dynamic World | habitat / land use | CC BY 4.0 | aggregate metrics | yes |
| Hansen Global Forest Change | forest loss | attribution/open-use dataset | aggregate metrics | yes |
| WRI SDPT | plantations/tree crops | provider release terms | aggregate metrics | yes |
| CAMS | air pollution | Copernicus terms | aggregate metrics | yes |
| Sentinel-5P | air pollution | Copernicus terms | aggregate metrics | yes |
| Copernicus Marine / CLMS | marine/lake water quality, ocean pH | Copernicus licence | aggregate metrics | yes |
| GEMStat | freshwater contaminants | open subset / CC BY 4.0-equivalent | measurements/aggregate | yes |
| NOAA microplastics / IncidentNews | contaminants/incidents | U.S. federal/public datasets; source-specific caveats | aggregate observations | yes |
| EPA UCMR5 / Superfund / TRI | PFAS / contaminated sites / land releases | U.S. federal public data | aggregate observations | yes |
| OpenStreetMap | waste sites | ODbL 1.0 | aggregate/index preferred | yes |
| Climate TRACE | waste facilities/emissions | CC BY 4.0 outputs; review upstream | aggregate | yes |
| Global mining polygons | mining footprint | CC BY-SA 4.0 | aggregate | yes |
| WAPHA tailings | tailings | mixed upstream provenance | aggregate/index preferred | yes |
| NHM BII | biodiversity intactness | restricted non-commercial | regional component/index only; raw stays private | **yes** |
| IUCN spatial/range data | rarity/extinction risk | restricted non-commercial | regional component/index only; raw stays private | **yes** |
| PHYLACINE | historical mammal loss | CC0 | aggregate/full derived metrics | no practical need |
| GBIF / OBIS | recorded richness context | mixed contributor open licences | recorded aggregates + provenance | yes |
| NOAA AGGI / GHG trends | global climate forcing | NOAA public scientific data | global metrics/index | yes |
| NOAA/NSIDC Sea Ice Index | cryosphere | public scientific data with citation | global metrics/index | yes |
| NOAA Ocean Heat Content | Global Ocean | public scientific data | global metric/index | yes |
| Copernicus global pH | Global Ocean | Copernicus licence | global metric/index | yes |

## Publication firewall

Provider manifests can classify output as `full`, `aggregate`, `component_only`, or `index_only`. Restricted raw files belong under ignored working directories and must never be copied into `frontend/public/`.

If NHM/IUCN licensing ever breaks our hearts, rebuild biodiversity without them:

```bash
python scripts/rebuild_environment_indices.py \
  --merge-biodiversity \
  --exclude-biodiversity-provider nhm_bii \
  --exclude-biodiversity-provider iucn_rasters \
  --exclude-biodiversity-provider iucn_ranges
```
| JPL GRACE/GRACE-FO Mascon | global freshwater storage | NASA/JPL public scientific data; cite dataset | aggregate/global component | yes |
| ERA5-Land | global soil-moisture stability | Copernicus Climate Change Service terms | aggregate/global component | yes |
| JRC Global Surface Water | global surface-water stability | Copernicus Programme; free use with acknowledgement | aggregate/global component | yes |

## Regional climate normals (v4)

| Provider | MotherWorld provider id | Reuse note | Public output |
|---|---|---|---|
| ECMWF / Copernicus ERA5-Land Daily Aggregated | `era5_land_climate_normals` | Copernicus Climate Change Service / ECMWF terms; attribution required | regional aggregate climatology only |
| ECMWF / Copernicus ERA5 Daily | `era5_climate_normals` | Copernicus Climate Change Service / ECMWF terms; attribution required | regional / whole-ocean aggregate climatology only |

Raw raster/reanalysis data is never committed by this module; only compact regional aggregate JSON is published.

## Geology + seafloor providers (v6)

| Provider | MotherWorld provider id | Licence / reuse note | Public output |
|---|---|---|---|
| GLiM | `glim` | CC BY 3.0 | regional lithology shares |
| Macrostrat | `macrostrat` | CC BY 4.0; preserve original source attribution | regional mapped-unit/age context |
| USGS MRDS | `usgs_mrds` | U.S. federal public data | occurrence / commodity aggregates |
| GEM Global Active Faults | `gem_active_faults` | CC BY-SA 4.0 | derived regional fault summaries |
| Smithsonian GVP | `smithsonian_gvp` | Smithsonian site terms; conservative non-commercial handling | aggregate / selected regional context; raw private |
| IHFC Global Heat Flow | `ihfc_heat_flow` | CC BY 4.0 | regional heat-flow aggregates |
| Confirmed impact staging | `impact_confirmed_staged` | provider-specific; review before redistribution | aggregate only |
| USGS ComCat | `usgs_comcat` | U.S. federal public data | regional event-history aggregates |

Geology is diagnostic only and carries zero Earth Health weight.



## Seafloor geology providers

| Provider | Internal class | Publication | Removable |
|---|---|---|---|
| GEBCO_2026 | public domain / attribution | aggregate | yes |
| Seton/EarthByte seafloor age | CC BY 4.0 | aggregate | yes |
| GlobSed | CC BY 4.0 | aggregate | yes |
| EarthByte/GPlates tectonic features | CC BY 3.0 | aggregate | yes |
| InterRidge Vents | restricted non-commercial / share-alike | aggregate / selected records | yes |
| CRUST1.0 / EarthScope EMC | provider/model terms | aggregate | yes |

All are descriptive diagnostics and carry zero Earth Health weight. InterRidge raw files remain outside the public tree.

## Moon Selenology

| Provider | MotherWorld provider id | Licence / reuse note | Publication mode |
|---|---|---|---|
| USGS Unified Geologic Map of the Moon, 1:5M v2 | `usgs_moon_geology` | CC0; cite Fortezzo, Spudis & Harrel (2020) | derived web texture + aggregate legend/statistics |

The original USGS raster/GIS archives are not shipped in the MotherWorld implementation pack. The repository-side builder creates browser textures locally.

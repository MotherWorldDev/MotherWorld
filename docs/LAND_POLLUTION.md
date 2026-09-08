# Land pollution methodology

## Why there is no one score

A landfill polygon, a Superfund site, tonnes of E-PRTR waste transfer, toxic chemical mass disposed to land and mine tailings are different phenomena. MotherWorld keeps them as independent provider metrics.

Percentile bars are calculated **within the same provider and metric** only. This makes it possible to say an ecoregion has unusually high mapped landfill density among ecoregions with OSM data without pretending that value is directly comparable with TRI kilograms.

## OpenStreetMap

Recognized tags:

- `landuse=landfill`
- `amenity=waste_disposal`
- `amenity=waste_transfer_station`

Site counts use representative points to avoid double-counting one polygon in adjacent regions. Landfill **area**, however, is apportioned by polygon intersection with ecoregion boundaries using EPSG:6933 equal-area calculations.

Outputs include mapped site counts, landfill area, site density per 1,000 km², and percent of region mapped as landfill.

## Climate TRACE

The builder consumes the Waste-sector download's `solid-waste-disposal_*emissions_sources.csv` file. It spatially assigns each source by latitude/longitude and reports:

- facility count
- latest year represented
- latest CH4 emissions when available
- latest 100-year CO2e when available

Those emission values indicate **waste-disposal activity / methane burden**, not soil toxicity or leachate contamination.

## Global mining polygons

Global mining polygons v2 represent land directly used by mining. The polygon class includes open cuts, tailings dams, waste-rock dumps, water ponds, processing infrastructure and other mining-associated land.

MotherWorld intersects them with each ecoregion and reports mining footprint km² and percent of the ecoregion. Because the source polygons do not separate every waste feature from every extraction feature, this metric is labelled mining-related industrial/waste pressure rather than contamination.

## WAPHA tailings

The WAPHA Dryad dataset supplies georeferenced global tailings storage facilities and known tailings dam failures. MotherWorld reports site/failure counts by ecoregion.

Coverage is incomplete; the source itself combines multiple public compilations and does not capture every facility worldwide.

## EPA Superfund

MotherWorld spatially assigns EPA's current Superfund/NPL geospatial locations to ecoregions and reports site counts. These are direct regulated hazardous-site observations, but U.S.-only.

## EPA TRI

TRI reports toxic chemical releases/disposal by facility and chemical. The builder sums the land-disposal family of Form R fields:

- RCRA Subtitle C landfills
- other landfills
- land treatment/application farming
- surface impoundments
- other on-site disposal

The resulting mass is converted to kilograms and aggregated by the ecoregion containing the reporting facility. Mass is **not** a toxicity or health-risk score.

## EEA Industrial Emissions / E-PRTR

The EEA facility-level waste-transfer export reports regulated waste transfers in tonnes/year. MotherWorld reports facility count, total waste transfer, hazardous waste transfer and non-hazardous waste transfer for the latest available year in the staged file.

This records waste handled/transferred by regulated facilities; it does not assert that all transferred material contaminated soil at the facility.

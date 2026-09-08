# Earth Health historical backbones

Earth Health is deliberately **not** an average of every dataset MotherWorld has. It is a fixed-composition annual index designed for honest comparison through time.

The requested historical floor is **1993**. The right edge is discovered at build time and is the latest calendar year for which every enabled backbone family has a valid annual score. The frontend never silently swaps ingredients.

| Family | Scoring backbone | Native history used by MotherWorld | Detailed/current diagnostics (zero Earth Health weight) |
|---|---|---|---|
| Biodiversity | BirdLife/IUCN Red List Index | 1993 → available | NHM BII, IUCN spatial rarity/ranges, PHYLACINE, GBIF/OBIS context |
| Habitat | Copernicus C3S annual land cover direct-conversion retention | 1993 → available | Hansen loss, Dynamic World, plantations, mining/waste |
| Vegetation | NOAA AVHRR + VIIRS NDVI Climate Data Record | 1993 → available (record begins 1981) | MODIS VCF tree/non-tree/bare cover |
| Pollution | NASA MERRA-2 reconstructed surface PM2.5 | 1993 → available (record begins 1980) | CAMS, TROPOMI, land/water pollution, contaminants |
| Climate forcing | NOAA AGGI | 1993 → available (record begins 1979) | CO2/CH4/N2O concentrations |
| Cryosphere | NOAA/NSIDC Arctic + Antarctic annual-minimum sea ice | 1993 → available | Greenland/Antarctic land ice, glaciers |
| Global Ocean | NOAA global 0–700 m Ocean Heat Content | 1993 → available (record begins 1955) | pH, sea level, water quality, marine heatwaves/oxygen later |
| Freshwater stability | ERA5-Land root-zone soil-moisture regime stability | 1993–2025 (35 source years; fixed baseline 1991–2020) | GRACE TWS, JRC surface-water retention |
| Ozone layer | 100 − NOAA ODGI-ML | 1993 → available (ODGI begins 1992) | Antarctic ODGI-A |

Freshwater's published series has one continuous annual row for every year from 1993 through 2025. The 35 source years from 1991 through 2025 support that series; only 1991–2020 define the fixed per-grid-cell monthly P10–P90 envelope. Its annual coverage is the fraction of the 12 calendar months with finite eligible land-area observations after the ice exclusion mask, so 1.0 means all twelve months had eligible observations rather than 100% of global land or complete source sampling. The mask excludes land south of 60°S and the broad rectangle from 75°W to 10°W and 58°N to 85°N; it intentionally also removes Iceland, Baffin, and nearby Canadian land and is not a pixel-level glacier mask.

The combined Earth Health score remains withheld while habitat, vegetation, and pollution are pending; publishing freshwater does not publish a nine-family score.

## Present-day diagnostics

`frontend/public/data/indices/families/*.index.json` contains the **scoring backbones**.

`frontend/public/data/indices/diagnostics/*.diagnostics.json` contains richer present-day evidence. Diagnostic datasets can be newer, shorter, region-specific, or more restrictive. They are displayed when a family is expanded at the latest common Earth Health year, but they never modify the headline score or its historical series.

This separation is intentional. If MotherWorld says Earth Health changed from one year to another, the same family definitions, weights and normalization rules were used on both sides of that comparison.

## Timeline rule

The Earth Health builder intersects usable annual years across all enabled families. It requires the configured start year (1993) and then emits a continuous year-by-year series until the first year that is not shared by all backbones. There is no mixed-year `LATEST` score.

## Normalization caveats

Several raw scientific indicators are converted to 0–100 with explicit MotherWorld governance reference points. Those endpoints are versioned/configurable and must not be presented as universal ecological thresholds. Raw indicator values are retained alongside scores.

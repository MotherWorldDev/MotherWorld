# Biodiversity data sources

## Natural History Museum — Biodiversity Intactness Index v2.1.1

Public limited release:

- temporal extent: 2000–2020;
- public release size: ~44 MB;
- license: CC BY-NC-SA 4.0 / non-commercial limited release;
- DOI: `10.5519/k33reyb6`.

MotherWorld stores regional aggregates, not a copy of the source raster archive.

## IUCN Red List spatial data

IUCN provides:

- species range polygons;
- occurrence points;
- freshwater HydroBASIN mappings;
- Species Richness and Rarity-Weighted Richness spatial downloads.

The spatial data download page states that these files are freely available for non-commercial use. MotherWorld keeps raw IUCN downloads outside Git and publishes only regional aggregates.

Current IUCN spatial data are filtered using their Presence, Origin and Seasonality codes. The IUCN mapping standards define presence code 5 as `Extinct (post 1500)`.

## IUCN STAR methodology

The STAR methodology is spatially explicit and links a location's species distributions and extinction risk to potential conservation contributions.

MotherWorld's `extinctionRiskResponsibility` is **not STAR**. It borrows only the transparent idea of weighting a threatened species by extinction-risk category and by the share of its global mapped range occurring inside the region.

## PHYLACINE 1.2.1

PHYLACINE includes current and counterfactual present-natural range rasters for extant and recently extinct mammals, together with integrated taxonomy, traits, phylogenies and threat status.

Latest stable repository:

`https://github.com/MegaPast2Future/PHYLACINE_1.2`

PHYLACINE is CC0. Cite the Ecology publication and database release when using its derived metrics.

## GBIF / OBIS recorded species

The merger reuses MotherWorld's existing generated Species index. It does not recalculate occurrence inventories.

Recorded richness is an observation/sampling metric and must not be presented as a complete ecological species count.

# Biodiversity pipeline

## Core design principle

MotherWorld does not collapse biodiversity into one opaque score.

The merged schema has five independent dimensions:

| Dimension | Primary provider | Interpretation |
|---|---|---|
| Condition | NHM BII | How much originally present ecological community remains |
| Uniqueness | IUCN rarity-weighted richness | How geographically range-restricted / globally important the species assemblage is |
| Extinction-risk responsibility | IUCN range maps | How much of threatened species' global mapped ranges occur inside the region |
| Historical mammal loss | PHYLACINE | Current mammal fauna vs counterfactual present-natural fauna |
| Observed richness | GBIF / OBIS | Number of species represented by qualifying occurrence records |

## NHM BII aggregation

For each BII raster and ecoregion polygon, the importer calculates an area-weighted mean using fractional boundary-cell rasterization.

The result is converted to percent if the input uses the 0–1 scale.

If multiple annual rasters are present, the provider output retains the series and first-to-last change in percentage points.

## IUCN richness and rarity-weighted richness

The precomputed IUCN rasters are aggregated to ecoregions with:

- area-weighted mean;
- spatial 90th percentile;
- fraction-weighted cell sum.

The merged output computes peer-region percentiles separately for land, marine, and lake regions.

The area-weighted mean is the default UI metric. The cell sum is preserved for later analyses of integrated responsibility.

## MotherWorld extinction-risk responsibility

This optional deeper metric uses IUCN range polygons.

For each mapped species:

```text
range_share(region, species) = intersection_area / global_filtered_range_area
```

Then:

```text
responsibility(region) = Σ range_share × category_weight
```

Weights:

```text
NT  1
VU  2
EN  3
CR  4
```

This follows the conceptual structure of STAR's use of extinction-risk category and spatial responsibility, but MotherWorld does not use STAR's threat-action layers. The frontend must label it **STAR-inspired / not official STAR**.

IUCN spatial filtering defaults to:

```text
Presence:     1 Extant, 4 Possibly Extinct
Origin:       1 Native, 2 Reintroduced, 6 Assisted Colonisation
Seasonality:  1 Resident, 2 Breeding, 3 Non-breeding, 5 Uncertain season
```

IUCN presence code 5 (Extinct post-1500) is separately intersected with MotherWorld regions to expose mapped modern extinction evidence.

## PHYLACINE faunal retention

PHYLACINE provides one current and one present-natural raster per mammal species.

MotherWorld rasterizes its regions onto the PHYLACINE grid and asks, for every species:

- does its present-natural range intersect this region?
- does its current range intersect this region?

Derived values:

```text
faunal_retention = current_species_count / present_natural_species_count × 100
```

and

```text
range_occupancy_retention = summed_current_occupied_area / summed_present_natural_occupied_area × 100
```

A species whose present-natural range intersects a region but whose current range does not is counted as locally lost in the PHYLACINE counterfactual comparison.

This is **not** a fossil extinction count. Present-natural ranges estimate where species would be expected to occur today without strong anthropogenic impacts.

## Percentile ranks

Percentiles are descriptive ranks among MotherWorld regions with that metric. They are calculated separately by region type.

Examples:

- 98th percentile rarity-weighted richness = more geographically unique than ~98% of comparable regions.
- 98th percentile extinction-risk responsibility = unusually important for currently threatened species.
- 98th percentile intactness = unusually intact, not unusually threatened.

## Provider removal

Intermediate provider outputs live under:

```text
.biodiversity-work/providers/<provider>/<kind>/<region>.json
```

They are ignored by Git.

To remove a provider from all published outputs, rerun the merger with `--exclude`. No frontend change is required.

## Earth Health historical backbone (v3)

The global Earth Health biodiversity connector is the BirdLife/IUCN Red List Index (RLI), rescaled from 0–1 to 0–100. NHM BII, IUCN spatial products, PHYLACINE and occurrence-derived metrics remain richer present-day diagnostics and regional metrics; none changes the fixed historical Earth Health formula.

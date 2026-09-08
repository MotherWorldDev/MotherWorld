# Unified environment stack architecture

The deployed UI is organized by **metric family**, not by dataset.

```text
providers -> raw regional/global metrics -> 0–100 components -> family indices -> Earth Health
```

Every score uses the same direction: **100 = best condition / lowest pressure**. Data coverage is always separate from score.

## Regional families

- Biodiversity
- Habitat
- Vegetation condition
- Pollution

Pollution expands into the appropriate medium: Air + Land for terrestrial regions; Air + Water for marine/lake regions. Contaminant observations appear as context unless/until an analyte-specific defensible normalization exists.

## Direct global families

- Climate forcing
- Cryosphere
- Global Ocean
- Freshwater Stability

These are not fabricated by averaging ecoregions when authoritative whole-Earth products exist. Freshwater Stability is likewise global-only: terrestrial water storage, root-zone soil moisture and surface-water extent are evaluated at the planetary hydrological scale, not assigned to terrestrial ecoregions.

## Global Ocean scope

`open_ocean` remains the visual/selectable residual. Species stay scoped to that residual and can be organized into Pacific / Atlantic / Indian / Southern / Arctic tabs.

Every non-species Global Ocean metric uses **the full ocean system**: visual residual + all mapped coastal/marine ecoregions. Native global provider values are preferred whenever available.

## Aggregation

Within a family, the regional global score is an area-weighted arithmetic condition mean. Across different families, Earth Health uses a weighted geometric mean so catastrophic weakness is difficult to cancel with excellence elsewhere.

Restricted providers are modular and publication-filtered before they reach the deployed data tree.


## Historical Earth Health backbone (v3+, unchanged in v4)

Earth Health scoring backbones are isolated under `data/indices/families/`; rich current summaries are under `data/indices/diagnostics/`. The historical slider begins at 1993 and only exposes the continuous intersection of all enabled backbone annual series. See `docs/HISTORICAL_BACKBONES.md`.

## Regional Climate diagnostics (v4)

The Climate tab now has two independent lazy-loaded data streams:

```text
existing temperature distributions
  frontend/public/data/climate/*

precipitation / humidity / wind normals
  frontend/public/data/climate-extras/*
```

They share the 1991–2020 climatological baseline but remain separate payloads so the proven temperature/OISST pipeline does not need to be rewritten. Regional climate normals are diagnostics and never feed the fixed 1993+ Earth Health score.

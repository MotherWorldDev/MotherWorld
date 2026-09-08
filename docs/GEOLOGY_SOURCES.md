# Geology sources

## Surface rocks — GLiM

Hartmann & Moosdorf (2012), Global Lithological Map (GLiM), DOI `10.1594/PANGAEA.788537`, CC BY 3.0. The public PANGAEA release includes a 0.5° gridded representation; the original compilation contains 16 first-level lithological classes. MotherWorld supports either vector inputs or a gridded lat/lon table. The 16 first-level codes are built into `geology_common.py`.

## Geologic units / ages — Macrostrat

Macrostrat geologic maps and API, CC BY 4.0. MotherWorld uses the cartographic small-scale GeoJSON endpoint as an optional enrichment layer and clips returned units to the selected ecoregion. Original `source_id` provenance is retained in the intermediate data.

## Minerals and resources — USGS MRDS

USGS Mineral Resources Data System. The importer expects a staged CSV/XLSX containing coordinates, site/deposit name and commodity fields. MotherWorld publishes occurrence counts and commodity summaries; it does not claim reserve tonnage or economic recoverability.

## Tectonics — GEM Global Active Faults

GEM Global Active Faults Database, CC BY-SA 4.0. The importer aggregates intersecting fault traces, mapped intersection length, available kinematics and slip-rate metadata.

## Seismicity — USGS ComCat

Optional staged USGS earthquake-catalog CSV, default threshold M5+. This is contextual event history, not a score or hazard forecast.

## Volcanism — Smithsonian Global Volcanism Program

Volcanoes of the World v5.4.0 (7 Aug 2026) and confirmed Holocene eruptions. GVP site terms require careful attribution and constrain commercial use; MotherWorld therefore keeps raw downloads outside the public repository and publishes regional aggregates / selected records only.

## Geothermal — IHFC Global Heat Flow Database

IHFC Global Heat Flow Database Release 2024, DOI `10.5880/fidgeo.2024.014`, CC BY 4.0. The current release contains 91,182 measurements and quality metadata. MotherWorld publishes regional counts, percentiles and binned heat-flow distributions.

## Impact structures

Until the Meteoritical Society Impact Cratering Committee publishes its authoritative online confirmed-structure catalog, MotherWorld treats impact data as a manually staged, replaceable provider. The default importer only accepts rows marked confirmed when a status field exists. Provider redistribution terms must be reviewed before publishing anything more granular than regional derived output.

The importer preserves optional source-supplied age ranges/uncertainties and impactor metadata (`impactor_name`, type/class, estimated diameter and diameter range/uncertainty). MotherWorld does not infer an impactor name or projectile diameter from crater diameter unless a future explicit physical-model provider is added.

## Moon impact craters — Robbins global lunar crater database

The Moon overview uses Stuart Robbins' global lunar impact-crater database (Robbins 2019; USGS Astrogeology / NASA PDS archive). The study identifies 2,033,574 craters in total; 1,296,879 have diameter ≥1 km and 6,972 have diameter ≥20 km. The database is estimated to be a complete census for craters larger than roughly 1–2 km, with location-dependent completeness at smaller sizes. MotherWorld therefore labels the Moon number as **catalogued craters ≥1 km**, never as the literal total number of lunar craters.

## Seafloor bathymetry — GEBCO_2026

GEBCO Bathymetric Compilation Group 2026, **GEBCO_2026 Grid**, DOI `10.5285/4f68d5c7-45eb-f999-e063-7086abc036fa`. The current grid is a 15 arc-second global terrain/bathymetry model and is accompanied by source-type metadata. GEBCO places the grid in the public domain while requesting attribution. MotherWorld publishes only regional/whole-ocean summary statistics; the source grid remains outside the deployed tree.

## Oceanic crust age / spreading — Seton et al. (2020), EarthByte

Seton et al. (2020), `10.1029/2020GC009214`; dataset archive `10.5281/zenodo.6782543`. The release contains present-day oceanic crust age plus spreading rate, direction, asymmetry, obliquity and confidence grids. The associated EarthByte work is CC BY 4.0. MotherWorld v7 scores nothing from these data; it uses them only for descriptive age/spreading statistics.

## Ocean sediment thickness — GlobSed

Straume et al. (2019), `10.1029/2018GC008115`; rescued/versioned dataset archive `10.1594/PANGAEA.982339`. GlobSed is a global 5 arc-minute total sediment-thickness grid for the oceans and marginal seas. The PANGAEA archive is CC BY 4.0.

## Seafloor tectonic fabric / spreading ridges

EarthByte / GPlates GeoData provides present-day spreading-ridge files and global seafloor-fabric features. EarthByte GeoData is published under CC BY 3.0; cite the original feature source used by the staged file. The generic MotherWorld importer can also accept a separately staged trench/subduction line layer.

## Hydrothermal vents — InterRidge

InterRidge Global Database of Active Submarine Hydrothermal Vent Fields v3.4, DOI `10.1594/PANGAEA.917894`. Version 3.4 contains 721 vent fields, including confirmed/inferred active and inactive records. The PANGAEA release is CC BY-NC-SA 4.0, so raw data stay private and MotherWorld publishes regional/whole-ocean aggregates plus a limited selected-record view under the provider firewall.

## Crustal thickness — CRUST1.0

CRUST1.0 (Laske, Ma, Masters & Pasyanos) via the EarthScope Earth Model Collaboration is a 1° global crustal model with water, ice, sediment and crystalline-crust layer thicknesses. MotherWorld optionally aggregates upper + middle + lower crystalline crust thickness. Preserve model/EarthScope attribution and review the applicable archive/model terms before changing publication granularity.

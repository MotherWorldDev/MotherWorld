# MotherWorld unified environment stack v8

> **Boundary-correction status:** seven land-region summaries require the shared analysis-footprint correction. Species, PHYLACINE coverage, and geology now include the corrected footprints; affected climate and pollution data releases remain pending. See [the tracked issue and acceptance checklist](REGION_BOUNDARY_CORRECTIONS.md).

This checkout contains the v8 environmental integration: fixed-composition Earth Health, lazy regional climate extras, descriptive geology/seafloor diagnostics, and Moon-only Selenology. The runtime keeps raw/provider downloads outside `frontend/public/data`; each index stays unavailable until its source-backed builder produces verified output.

Earth Health is unavailable until all nine enabled historical backbones share a continuous timeline from 1993 and meet both the per-family and weighted effective-coverage thresholds. Present-day diagnostics, climate extras, geology, and Selenology never contribute Earth Health points.

The browser modules preserve lazy loading and the existing species and temperature services. Selenology uses the natural LROC texture by default and switches to an available official mapped texture only while the Moon-only tab is active, restoring the natural texture when the tab closes or the selection changes.

Key references:

- [Earth Health method](EARTH_HEALTH_METHOD.md), [historical backbones](HISTORICAL_BACKBONES.md), [direct-source runbook](DIRECT_HISTORICAL_BACKBONES.md), and [scoring contract](SCORING_CONTRACT.md)
- [Regional climate](REGIONAL_CLIMATE.md) and [provider sources](REGIONAL_CLIMATE_SOURCES.md)
- [Geology](GEOLOGY.md), [geology sources](GEOLOGY_SOURCES.md), and [geology licensing](GEOLOGY_LICENSES.md)
- [Selenology](SELENOLOGY.md) and [Moon texture method](MOON_GEOLOGY_TEXTURE.md)
- [global source registry](DATA_SOURCES_GLOBAL.md), [license registry](LICENSE_REGISTRY.md), and [module manifest](MODULE_MANIFEST.json)

Run the repository tests with `npm test` and the focused Earth Health contract tests with `python -m unittest tests.test_earth_health_contract`.

## Direct-provider build status — 9 September 2026

The active acquisition workflow does not use Google Earth Engine. The pack's Earth Engine scripts and source notes are retained as optional reference implementations; they are not required to use the website. Direct CDS, NOAA, NASA, and geological-provider adapters are being validated against the same source products and scoring methods.

The official USGS mapped Moon textures (4K and 8K) and 49-rule legend are ready. Seven historical backbone families are built: biodiversity through 2025, climate forcing through 2024, cryosphere through 2025, ocean heat through 2025, freshwater stability through 2025 (35 source years, 1991–2025; fixed baseline 1991–2020), ozone through 2024, and NASA MERRA-2 pollution through 2025. Every built family has a continuous annual series from 1993. Sea-ice annual minima exclude the current UTC calendar year and retain explicit observed-date coverage for historical gaps.

Habitat and vegetation historical backbones remain pending. C3S land-cover acquisition has resumed in a durable background worker; the complete history is still pending. The full NOAA NDVI streaming run (1982–2025, including the 1982–1992 baseline) started on September 9 and remains in progress. NASA MERRA-2 pollution completed all 396 source months for its 1993–2025 series. Both workflows retain checkpoints and enforce a 40 GiB free-space reserve; no incomplete historical family is published. See the [direct-source runbook](DIRECT_HISTORICAL_BACKBONES.md) for methods, source-grid corrections, and resume checks. Freshwater annual coverage is the fraction of the 12 calendar months with finite eligible land-area observations after the fixed ice exclusion mask; 1.0 means all 12 months had eligible observations, not that all global land or every source sample was covered. The combined Earth Health score remains unavailable until all nine families satisfy the fixed composition and coverage contract. Regional geology and other diagnostic builds are integrated only after their canonical region IDs and source coverage are checked.

Initial integration validation: 47 JavaScript tests and 34 Python tests passed. The delivered Moon texture was visually checked for global coverage and orientation. Interactive browser/WebGL validation is still pending because browser automation is unavailable in this session.

### Regional geology release

The September 9 geology release includes the seven corrected GLiM summaries identified by the [boundary audit](REGION_BOUNDARY_CORRECTIONS.md). It contains summaries for 1,097 of the 1,100 inventory regions, plus a separate whole-ocean scope. GEBCO bathymetry covers 233 marine scopes, and earthquake-only USGS ComCat summaries cover 601 regions. It includes GLiM surface lithology, USGS MRDS mineral sites, GEM active faults, IHFC heat-flow measurements, InterRidge hydrothermal vents, and EarthByte ridge intersections, ocean-crust age, and spreading rates where records are available. Marine map fragments are unioned under canonical region IDs before aggregation. The whole-ocean scope excludes both land and freshwater lakes. Missing measurements remain null and are excluded from numeric summaries; every generated file passes strict JSON parsing.

Three inventory regions currently have no matching geology records from these providers: lake_aral_sea, lake_ladoga, and lake_vanern. This is separate from species coverage. Sediment and other unbuilt geological providers remain unavailable while their inputs are acquired. The [release receipt](GEOLOGY_RELEASE_20260909.json) records source hashes, provider counts, and excluded ComCat event types. The M5+ earthquake catalog spans 1 January 1993 through 8 September 2026; its 57,468 earthquake rows exclude 73 landslides, eruptions, explosions, and mine-collapse events.

The direct CDS ERA5-Land freshwater adapter passed checks for decoded monthly dates, variable aliases, request provenance, and 0–360° longitude masking. The published freshwater backbone covers 1993–2025 from 35 source years (1991–2025) and uses a fixed 1991–2020 monthly P10–P90 envelope. Its southern-latitude and broad Greenland-box exclusions are a package-level ice-screening approximation, not a pixel-level glacier mask.

### Regional biodiversity and contaminants release

The lazy Biodiversity tab includes PHYLACINE records for all 847 land regions: 707 have native raster coverage and 140 explicitly lack a native cell center. The latter show unavailable counts rather than zero mammals. The seven maintained boundary corrections are included with verified producer geometry and cache identities; all seven remain below the native raster resolution. Present-natural ranges are counterfactual, not a fossil map, and these diagnostics do not contribute Earth Health points.

The lazy Contaminants tab includes 190 marine regions and 499 microplastics measurement series across 185 regions. NOAA incident categories cover 49 regions for oil incidents and 15 for chemical incidents, with overlapping regional coverage. Measurement series stay separate by unit and sampling protocol. Quantified counts include finite zero values; positive counts and concentration summaries use positive reported values because the source provides no explicit nondetect flags. Incident maximum potential releases are not actual release quantities.

Both tabs fetch data only when opened, retain a bounded region cache, and offer retries after request failures. The release validation checked canonical IDs, strict finite JSON, actual index paths and payload shapes, and the seven geometry identities. See the [boundary issue record](REGION_BOUNDARY_CORRECTIONS.md) for the remaining climate and pollution work. OSM and incomplete global histories are not part of this regional release.

Regional diagnostics release validation: all 53 JavaScript tests pass, including actual generated-payload loading, source-map compatibility, missing-value handling, and protocol/concentration display. The separate regional CDS acquisition change passed 26 combined Python downloader/geometry tests. Interactive browser/WebGL validation remains unavailable because the browser automation kernel failed to start.

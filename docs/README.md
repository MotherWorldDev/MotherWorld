# MotherWorld unified environment stack v8

> **Boundary-correction status:** seven land-region summaries require the shared analysis-footprint correction. Species is fixed; other regional rebuilds remain pending. See [the tracked issue and acceptance checklist](REGION_BOUNDARY_CORRECTIONS.md).

This checkout contains the v8 environmental integration: fixed-composition Earth Health, lazy regional climate extras, descriptive geology/seafloor diagnostics, and Moon-only Selenology. The runtime keeps raw/provider downloads outside `frontend/public/data`; each index stays unavailable until its source-backed builder produces verified output.

Earth Health is unavailable until all nine enabled historical backbones share a continuous timeline from 1993 and meet both the per-family and weighted effective-coverage thresholds. Present-day diagnostics, climate extras, geology, and Selenology never contribute Earth Health points.

The browser modules preserve lazy loading and the existing species and temperature services. Selenology uses the natural LROC texture by default and switches to an available official mapped texture only while the Moon-only tab is active, restoring the natural texture when the tab closes or the selection changes.

Key references:

- [Earth Health method](EARTH_HEALTH_METHOD.md), [historical backbones](HISTORICAL_BACKBONES.md), and [scoring contract](SCORING_CONTRACT.md)
- [Regional climate](REGIONAL_CLIMATE.md) and [provider sources](REGIONAL_CLIMATE_SOURCES.md)
- [Geology](GEOLOGY.md), [geology sources](GEOLOGY_SOURCES.md), and [geology licensing](GEOLOGY_LICENSES.md)
- [Selenology](SELENOLOGY.md) and [Moon texture method](MOON_GEOLOGY_TEXTURE.md)
- [global source registry](DATA_SOURCES_GLOBAL.md), [license registry](LICENSE_REGISTRY.md), and [module manifest](MODULE_MANIFEST.json)

Run the repository tests with `npm test` and the focused Earth Health contract tests with `python -m unittest tests.test_earth_health_contract`.

## Direct-provider build status — 8 September 2026

The active acquisition workflow does not use Google Earth Engine. The pack's Earth Engine scripts and source notes are retained as optional reference implementations; they are not required to use the website. Direct CDS, NOAA, NASA, and geological-provider adapters are being validated against the same source products and scoring methods.

The official USGS mapped Moon textures (4K and 8K) and 49-rule legend are ready. Six historical backbone families are built: biodiversity through 2025, climate forcing through 2024, cryosphere through 2025, ocean heat through 2025, freshwater stability through 2025 (35 source years, 1991–2025; fixed baseline 1991–2020), and ozone through 2024. Every built family has a continuous annual series from 1993. Sea-ice annual minima exclude the current UTC calendar year and retain explicit observed-date coverage for historical gaps.

Habitat, vegetation, and pollution historical backbones remain pending. The C3S 1993 land-cover pilot has been downloaded and verified through CDS; the complete history is still pending. NOAA NDVI can be accessed directly; the historical transfer strategy is still being assessed. The direct MERRA-2 route requires NASA Earthdata access. Freshwater annual coverage is the fraction of the 12 calendar months with finite eligible land-area observations after the fixed ice exclusion mask; 1.0 means all 12 months had eligible observations, not that all global land or every source sample was covered. The combined Earth Health score remains unavailable until all nine families satisfy the fixed composition and coverage contract. Regional geology and other diagnostic builds are integrated only after their canonical region IDs and source coverage are checked.

Integration validation: 47 JavaScript tests and 34 Python tests passed. The delivered Moon texture was visually checked for global coverage and orientation. Interactive browser/WebGL validation is still pending because browser automation is unavailable in this session.

### Regional geology release

The geology snapshot passed schema, canonical-ID, and finite-value checks; the later [boundary audit](REGION_BOUNDARY_CORRECTIONS.md) identified seven land records that need rebuilding. The snapshot contains summaries for 1,095 of the 1,100 inventory regions, plus a separate whole-ocean scope. It includes GLiM surface lithology, USGS MRDS mineral sites, GEM active faults, IHFC heat-flow measurements, InterRidge hydrothermal vents, and EarthByte ridge intersections, ocean-crust age, and spreading rates where records are available. Marine map fragments are unioned under canonical region IDs before aggregation. The whole-ocean scope excludes both land and freshwater lakes. Missing measurements remain null and are excluded from numeric summaries; every generated file passes strict JSON parsing.

Five inventory regions currently have no matching geology records from these providers: lake_aral_sea, lake_ladoga, lake_vanern, marine_meow_20019, and marine_meow_25184. This is separate from species coverage. Bathymetry, sediment, and other unbuilt geological providers remain unavailable while their inputs are acquired.

The direct CDS ERA5-Land freshwater adapter passed checks for decoded monthly dates, variable aliases, request provenance, and 0–360° longitude masking. The published freshwater backbone covers 1993–2025 from 35 source years (1991–2025) and uses a fixed 1991–2020 monthly P10–P90 envelope. Its southern-latitude and broad Greenland-box exclusions are a package-level ice-screening approximation, not a pixel-level glacier mask.

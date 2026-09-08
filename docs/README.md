# MotherWorld unified environment stack v8

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

The official USGS mapped Moon textures (4K and 8K) and 49-rule legend are ready. Five historical backbone families are built: biodiversity through 2025, climate forcing through 2024, cryosphere through 2025, ocean heat through 2025, and ozone through 2024. Every one has a continuous annual series from 1993. Sea-ice annual minima exclude the current UTC calendar year and retain explicit observed-date coverage for historical gaps.

Habitat, vegetation, pollution, and freshwater historical backbones are still being acquired or validated. The accepted C3S land-cover pilot is downloading through CDS. NOAA NDVI can be accessed directly; the historical transfer strategy is still being assessed. The direct MERRA-2 route requires NASA Earthdata access. The combined Earth Health score remains unavailable until all nine families satisfy the fixed composition and coverage contract. Regional geology and other diagnostic builds are integrated only after their canonical region IDs and source coverage are checked.

Integration validation: 47 JavaScript tests and 31 Python tests passed. The delivered Moon texture was visually checked for global coverage and orientation. Interactive browser/WebGL validation is still pending because browser automation is unavailable in this session.

# MotherWorld unified environment stack v8

This checkout contains the v8 environmental integration: fixed-composition Earth Health, lazy regional climate extras, descriptive geology/seafloor diagnostics, and Moon-only Selenology. The runtime keeps raw/provider downloads outside `frontend/public/data`; the committed indexes are intentionally empty until their source-backed builders produce verified outputs.

Earth Health is unavailable until all nine enabled historical backbones share a continuous timeline from 1993 and meet both the per-family and weighted effective-coverage thresholds. Present-day diagnostics, climate extras, geology, and Selenology never contribute Earth Health points.

The browser modules preserve lazy loading and the existing species and temperature services. Selenology uses the natural LROC texture by default and switches to an available official mapped texture only while the Moon-only tab is active, restoring the natural texture when the tab closes or the selection changes.

Key references:

- [Earth Health method](EARTH_HEALTH_METHOD.md), [historical backbones](HISTORICAL_BACKBONES.md), and [scoring contract](SCORING_CONTRACT.md)
- [Regional climate](REGIONAL_CLIMATE.md) and [provider sources](REGIONAL_CLIMATE_SOURCES.md)
- [Geology](GEOLOGY.md), [geology sources](GEOLOGY_SOURCES.md), and [geology licensing](GEOLOGY_LICENSES.md)
- [Selenology](SELENOLOGY.md) and [Moon texture method](MOON_GEOLOGY_TEXTURE.md)
- [global source registry](DATA_SOURCES_GLOBAL.md), [license registry](LICENSE_REGISTRY.md), and [module manifest](MODULE_MANIFEST.json)

Run the repository tests with `npm test` and the focused Earth Health contract tests with `python -m unittest tests.test_earth_health_contract`.

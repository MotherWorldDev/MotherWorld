# Validation report — v6 historical backbones + climate suite + geology + seafloor

Validation was performed on the packaged implementation without fabricating scientific output values.

## Passed

- all 84 packaged Python scripts compile;
- all packaged frontend JavaScript files pass `node --check`;
- Earth Health remains nine fixed backbone families, weights sum to 1.00, and the requested historical start remains 1993;
- Geology/seafloor remains descriptive with Earth Health weight exactly 0;
- all 14 geology + seafloor provider policies are diagnostic-only;
- restricted-source publication firewall checks include Smithsonian GVP, staged impacts, and InterRidge vents;
- Global Ocean scope invariant still passes (`open_ocean` visual residual; non-species whole-ocean aggregation);
- existing geology point/line builders now use whole-ocean aggregation for the Global Ocean selection;
- regional climate providers remain diagnostic-only with the 1991–2020 baseline;
- GLiM first-level lithology mapping and geologic-era helpers pass;
- seafloor area-weighted raster statistics and category-mass helpers pass synthetic tests;
- synthetic provider fragments from multiple geology/seafloor providers merge into one public per-region payload and index;
- a synthetic two-region ocean was processed end-to-end through GEBCO bathymetry, Seton-style age/spreading, GlobSed sediment thickness, CRUST1.0 thickness, ridge/trench lines, and InterRidge-style vents;
- that synthetic test verified the `open_ocean` selection aggregates the entire ocean rather than only the residual polygon;
- installer main/index/UI/config patch functions are idempotent and refresh module query versions;
- full synthetic installer upgrade path passes: v5 → v6 → v6, with the second v6 install byte-identical across installed text/data files (excluding Python bytecode);
- package validation passes after the v5→v6 module changes.

## Not executed here

Live multi-gigabyte provider downloads were not executed. The package does not bundle GEBCO, EarthByte, GlobSed, CRUST1.0, InterRidge, GVP, Macrostrat, or other raw source data. Earth Engine jobs also require the project owner's authenticated Earth Engine / Google Cloud project.

The package therefore ships an empty `geology.index.json` and **no invented geology/seafloor values**. Stage the real provider files under `geology_raw/` and run `python scripts/build_all_geology.py` (optionally `--macrostrat`).

## v8 Selenology validation

- All Python files compile.
- All frontend JavaScript parses with `node --check`.
- Unified-stack validator passes with nine fixed Earth Health backbone families unchanged.
- Selenology installer HTML/UI/config patches are idempotent.
- Moon `globe.js` surface-mode patch is idempotent and exposes `natural` / `geology` modes through a document event bridge.
- Synthetic 360×180 RGB GeoTIFF successfully converted to two WebP Moon textures and changed the Selenology index from `texture.available=false` to `true`.
- Selenology remains Moon-only and has Earth Health weight 0.

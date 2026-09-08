# Species pipeline

## 1. GBIF for terrestrial ecoregions and lakes

The land/lake builder uses the GBIF Occurrence Search API with MotherWorld region geometry, requests a `speciesKey` facet, and pages through the facet values. It applies these defaults:

- coordinates required;
- GBIF geospatial issues excluded;
- occurrence status `PRESENT`;
- modern observation/specimen/material record types;
- no date cutoff unless you provide one.

The facet gives a stable species-level GBIF key and the number of occurrence records inside the polygon.

### Why there is a taxonomy-cache preparation step

Calling the GBIF species endpoint once for every species on Earth would be abusive and extremely slow. Instead the pack uses one GBIF **SPECIES_LIST occurrence download** as a local key-to-name/taxonomy lookup. GBIF's species-list format includes `speciesKey`, species/scientific names, taxonomy, occurrence totals and an IUCN Red List category field when GBIF has one.

A GBIF account is required to create a download, although the underlying occurrence data are publicly available.

Set credentials in the environment:

```bash
export GBIF_USERNAME="your_username"
export GBIF_PASSWORD="your_password"
export GBIF_EMAIL="you@example.com"
```

Then request and build the cache:

```bash
python scripts/gbif_prepare_taxonomy.py --request-download
```

The script waits for the asynchronous GBIF job, downloads the resulting species-list ZIP, and creates:

```text
.cache/motherworld/gbif-species-taxonomy.sqlite
```

If you already have a GBIF `SPECIES_LIST` ZIP:

```bash
python scripts/gbif_prepare_taxonomy.py \
  --species-list-zip /path/to/gbif-species-list.zip
```


### Public taxonomy option (no account required)

Run `python scripts/gbif_prepare_backbone.py --download` to download the public 466 MiB GBIF Backbone archive and build the same SQLite cache. The importer pins the **2023-08-28** archive used for the legacy `speciesKey` facet, records its edition and source URL, and streams the import. This archive supplies names and taxonomy only; it does **not** supply current IUCN assessments or global occurrence counts. A GBIF Species List download remains an option when those fields are needed.

The `.cache/` downloads and SQLite files stay local and are ignored by Git. Only generated regional JSON is deployed. No account credentials are required for the public archive or the GBIF/OBIS query builders.

### Build all land + lake regions

```bash
python scripts/gbif_build_region_species.py --kind both
```

Useful development runs:

```bash
python scripts/gbif_build_region_species.py --kind land --region eco_1
python scripts/gbif_build_region_species.py --kind lakes --region lake_superior
```

Optional time window:

```bash
python scripts/gbif_build_region_species.py --kind both --year 1950,2026
```

The build is resumable. Existing region files are reused when their geometry, query filters and taxonomy source fingerprint match unless `--force` is supplied.

## 2. OBIS for marine ecoregions

OBIS is the better primary source for MotherWorld's MEOW marine layer. Its public checklist API accepts WKT geometry, returns taxa observed inside the area, uses WoRMS-backed marine taxonomy, and exposes occurrence record counts.

Build all marine regions:

```bash
python scripts/obis_build_region_species.py
```

Development run:

```bash
python scripts/obis_build_region_species.py --region marine_meow_20192
```

The builder groups subspecies/lower taxon checklist rows back to the parent species so the site does not double-count species and subspecies as separate species.

## 3. Build everything

After the GBIF taxonomy cache exists:

```bash
python scripts/build_all_species.py
```

## Output model

Example shape:

```json
{
  "regionId": "eco_1",
  "source": "GBIF",
  "inventoryType": "recorded_species",
  "stats": {
    "recordedSpeciesCount": 4200,
    "occurrenceCount": 128000
  },
  "species": [
    {
      "speciesKey": 123456,
      "scientificName": "Example species",
      "kingdom": "Animalia",
      "family": "Exampleidae",
      "iucnRedListCategory": "VU",
      "occurrenceCount": 187
    }
  ]
}
```

Counts are occurrence records, not population estimates.

## Geometry accuracy

For terrestrial ecoregions the script prefers the original local source shapefile:

```text
Ecoregions2017/Ecoregions2017.shp
```

If that source is unavailable it falls back to the committed LOD0 TopoJSON. Marine and lake queries use their committed runtime polygons. Query polygons are topology-preservingly simplified by at most 0.01 degrees (about 1 km at the equator), then partitioned into disjoint polygons to fit API URL limits. All polygon parts and holes are retained. Counts near simplified boundaries may differ from a join against the full source geometry; records exactly on partition boundaries can be counted by both neighboring queries.

## Scaling note

GBIF explicitly warns that large numbers of search-API calls can be rate-limited and recommends bulk downloads when a workflow runs for a long time. This pack therefore:

- retries `429` and transient server errors;
- runs serially by default;
- writes after each region so interrupted builds resume;
- uses a single bulk Species List download for taxonomy rather than per-species lookups.

For a production-scale refresh you can also use GBIF's public monthly Parquet snapshots on AWS and perform the polygon join in a cloud/data-processing environment. Those snapshots are roughly hundreds of gigabytes, so the API-facet workflow is more practical for a normal development machine even though it takes longer.

## Licensing and attribution

### GBIF

GBIF aggregates records from datasets under CC0, CC BY and CC BY-NC licenses. Keep the GBIF download metadata/DOI generated by the taxonomy-cache step and provide a data-sources page in MotherWorld. If you move to raw occurrence downloads or the AWS snapshot, preserve dataset-level citation information as well.

### OBIS

OBIS provides open access to marine occurrence data; individual contributing datasets retain their own licenses. The region files identify OBIS as the source and should be accompanied by an OBIS/data-sources attribution page.

### DOPA / modeled ranges

DOPA is useful for ecoregion biodiversity statistics and range-based indicators, but some underlying range datasets (notably third-party species range sources) carry separate reuse conditions. Do not blindly redistribute full modeled species lists from those services without checking the original dataset terms. DOPA is better treated as an optional validation/aggregate-statistics layer unless the relevant source licenses permit redistribution.

## What this intentionally does not do

It does not generate synthetic descriptions and call them biodiversity data. The Species tab is backed by actual occurrence-derived inventories.

It also does not claim that occurrence coverage equals the complete biological range of every species. The UI should say `Recorded species`, and the source line explains the method.

## Frontend and precedence

The newer species pack owns the Species tab. Earlier editorial `tertiarySummary` content remains in the legacy data schema but is not rendered there. Climate and Threats keep their earlier content and source references. Moon timers and plots remain separate.

The species manifest and selected region inventory load only when the Species tab is opened. Requests are deduplicated, failed requests can be retried, stale responses cannot replace the current selection, and the in-memory cache keeps six regions. Search is debounced; lists start at 60 rows and expand on request.

`npm test` checks summary and species loading. `python -m unittest discover -s tests -p "test_species*.py"` checks geometry mapping, partitioning, API pagination, grouping and taxonomy imports. Refreshes should use `--force` when new observations are wanted with unchanged filters.

Run only one builder against a given output directory at a time. To generate GBIF and OBIS concurrently, give them separate `--output-dir` directories and merge the completed manifests afterward.

## Release packaging and current build

The complete September 2026 inventory release covers all 1,100 mapped regions: 847 land ecoregions, 21 lakes and 232 marine ecoregions. This is complete region coverage for the chosen occurrence queries, not a claim that every species present has been observed or recorded. The seven initially empty land inventories were rebuilt with corrected query footprints; all 1,100 inventories now contain at least one recorded species. An empty result from any future query still means no matching records, not biological absence.

The initial release uses the committed LOD0 polygons for land queries (`--runtime-geometry`). This matches the site's detailed map and avoids processing the much larger original shapefile. GBIF query WKT defaults to 2,500 characters to leave room for encoded URL parameters; exterior rings are oriented counterclockwise.

The `both` GBIF build handles lakes first, then land regions with authored editorial content, then the remaining land regions. Current background outputs are `.cache/motherworld/gbif-output` and `.cache/motherworld/obis-output`; logs and process information are in `.cache/motherworld/`.

Package completed results for publication:

```bash
python scripts/package_species.py --input-dir .cache/motherworld/gbif-output --input-dir .cache/motherworld/obis-output
```

The packager validates inventory IDs and totals, compresses individual files as `.json.gz`, and replaces the public manifest only after writing the files. The browser decodes only the selected inventory. Modern browsers supporting `DecompressionStream` are required for compressed inventories. Plain JSON inventories remain supported too. Packaging does not commit or push; deploy the validated `frontend/public` changes through the existing GitHub Pages workflow.

## Kingdom browsing and live species profiles

The Species tab uses `speciesCatalog.js` to index local inventories once per selected region. Kingdom tabs show inventory totals; class/family filters, record thresholds and name/record/taxonomy sorting operate locally. Sixty rows render initially, with additional rows on request. Common-name lookup is explicit: it searches up to 100 GBIF Backbone species records and intersects the returned identifiers or exact marine scientific names with the existing regional inventory. It never creates new regional occurrence records.

`speciesProfileService.js` fetches details only when a row is opened. GBIF supplies taxonomy, common names, descriptions, species profiles, distribution/conservation reports, synonyms and references. WoRMS adds authoritative marine taxonomy, vernaculars, attributes with nested qualifiers, distributions, synonyms and literature. Marine cross-provider matching requires an exact species-level scientific name, matching kingdom and high match confidence; fuzzy and higher-rank matches are rejected. Provider output remains attributed and is never presented as proof of current local presence or current conservation status.

Requests have a 12-second timeout and share a four-request concurrency cap. Twenty-four complete profiles are cached for six hours in memory; no profile is prefetched for unopened rows. Partial source failures show available sections and allow retry without caching the incomplete profile. Closing a profile or changing region cancels its work; selection versions also guard against responses from cancelled requests. Failed photos keep their source captions and do not block other content.

Photographs use their individual media licences, independently of the occurrence dataset's licence. Only CC BY, CC BY-SA, CC0/public-domain media from the exact species are accepted. The GBIF image cache resizes photos to at most 640 pixels wide; the public URL's MD5 is used only as the documented image cache identifier. Images load sequentially and lazily, with credit, licence and observation-source links. Source HTML is reduced to text and escaped; external URLs must be HTTPS without user credentials.

The profile UI keeps regional record counts separate from species-wide descriptions, traits, distribution reports and photos. Existing source omissions are not filled with generated biological facts. Full source links remain available when the profile shows a bounded selection of records.

Run `npm test` for catalog, lookup, profile service, media, rendering and selection-race coverage. Direct browser QA should cover a GBIF land profile, a WoRMS marine profile, common-name lookup, filters, keyboard kingdom navigation, Back/Escape, a missing region and the Moon state.

## Remote-region query geometry corrections

The seven formerly empty regions have maintained query footprints in `scripts/data/species-query-geometries.geojson`. `species_query_geometry.py` validates the geometry and attribution; the GBIF builder applies these before WKT partitioning, even with `--runtime-geometry`. Five use original RESOLVE multipart geometry. Trindade–Martin Vaz and St. Peter and St. Paul use official Brazilian island-group baseline envelopes, including small inter-islet waters. Query-only corrections preserve the globe rendering assets; affected inventories expose this distinction in `query.geometryNotes`.

Geometry fingerprints and source metadata participate in resume fingerprints. Unrelated regions retain their existing fingerprints. `--query-geometry-file` permits an explicitly supplied, validated replacement FeatureCollection. Records still require the same species facets, coordinates, PRESENT status, accepted record types and no geospatial issues; filter relaxation is not used.

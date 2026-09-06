# Public data sources used by this pack

## GBIF

- Global Biodiversity Information Facility occurrence data
- Public occurrence search API for region-level species facets
- `SPECIES_LIST` occurrence-download format for a local taxonomy cache
- Optional public monthly Parquet snapshots are available through the AWS Open Data Registry

References:

- https://www.gbif.org/
- https://techdocs.gbif.org/en/openapi/v1/occurrence
- https://techdocs.gbif.org/en/data-use/download-formats
- https://registry.opendata.aws/gbif/

## OBIS

- Ocean Biodiversity Information System
- Public checklist API with WKT geometry filtering
- WoRMS-backed taxonomic quality control
- Full GeoParquet exports are also available through AWS Open Data for larger workflows

References:

- https://obis.org/
- https://api.obis.org/
- https://manual.obis.org/access.html
- https://manual.obis.org/data_qc.html

## DOPA

European Commission JRC DOPA can be used later for aggregate/range-derived biodiversity and conservation indicators. Its services combine several source datasets, some of which have separate third-party reuse terms.

- https://dopa.jrc.ec.europa.eu/
- https://data.jrc.ec.europa.eu/dataset/jrc-dopa-download-services

## MotherWorld integration

The current taxonomy lookup uses the public GBIF Backbone Taxonomy **2023-08-28** edition, downloaded from https://hosted-datasets.gbif.org/datasets/backbone/2023-08-28/simple.txt.gz. It resolves the legacy species keys returned by occurrence facets and contains no current IUCN assessments. Source URL and edition are recorded in each GBIF inventory.

The public-facing source and methods page is `frontend/public/data-sources.html`. Regional inventories include query filters, generation dates and source provenance. Raw archives, credentials and SQLite lookup tables stay out of Git and the deployment.

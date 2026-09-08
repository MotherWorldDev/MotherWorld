# Internal provider / licence notes

This file is an internal implementation note, not legal advice. Keep provider outputs modular so a source can be removed without changing the frontend schema.

| Provider | MotherWorld provider id | Current licence / reuse note | Replaceable? |
|---|---|---|---|
| OpenStreetMap | `osm-waste` | ODbL 1.0; attribution and database share-alike obligations may apply to derived databases | yes |
| Climate TRACE solid-waste disposal | `climate-trace-solid-waste` | Climate TRACE publishes its outputs under CC BY 4.0; their terms identify upstream sources whose terms users should review | yes |
| Global-scale mining polygons v2 | `global-mining-polygons-v2` | CC BY-SA 4.0 | yes |
| WAPHA tailings | `wapha-tailings` | Dryad-hosted academic compilation with mixed upstream provenance; cite dataset and review source terms before redistribution changes | yes |
| US EPA Superfund | `epa-superfund` | U.S. federal public data | yes |
| US EPA TRI land disposal | `epa-tri-land` | U.S. federal public data | yes |
| EEA Industrial Emissions / E-PRTR | `eea-industrial-waste` | EEA/EU public-data reuse terms; cite exact dataset release | yes |

Generated public files preserve provider separation so any provider can be dropped by deleting its key and rebuilding the index/percentiles.

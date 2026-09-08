# Internal biodiversity license registry

This file is an **internal engineering note**, not legal advice.

The same registry is machine-readable in `frontend/public/data/biodiversity/provider-manifest.json`.

| Provider | License / terms | Internal class | Removable? | MotherWorld policy |
|---|---|---|---|---|
| NHM BII v2.1.1 limited release | CC BY-NC-SA 4.0; non-commercial | `restricted_noncommercial` | Yes | Aggregate to regions. Keep raw data out of Git. Attribute NHM. If funding/use is rejected by NHM, exclude provider and remerge. |
| IUCN richness / RWR | IUCN Red List spatial-data terms; non-commercial | `restricted_noncommercial` | Yes | Keep raw downloads out of Git. Publish only regional derived metrics. Preserve IUCN attribution/version. |
| IUCN range polygons | IUCN Red List spatial-data terms; non-commercial | `restricted_noncommercial` | Yes | Same as above. MotherWorld responsibility metric must never be labelled official STAR. |
| PHYLACINE 1.2.1 | CC0 | `open` | No practical need | Safe core historical-loss provider. Cite dataset/publication. |
| GBIF / OBIS regional occurrence inventory | Mixed contributor licenses / source terms | `mixed_open` | No practical need | Preserve dataset/source provenance; call it `recorded species`, not complete richness. |

## Funding assumption

Current project intent: free website, open-source code, voluntary donations/crowdfunding to cover hosting, compute and development; no paywalled biodiversity data or donor-only environmental features.

We are proceeding with the non-commercial providers under that intended use while keeping them technically removable.

## Kill switch

If NHM or IUCN later objects to MotherWorld's funding/use model:

```bash
python scripts/merge_biodiversity_metrics.py \
  --exclude nhm_bii \
  --exclude iucn_rasters \
  --exclude iucn_ranges
```

Then redeploy the regenerated `frontend/public/data/biodiversity/` outputs.

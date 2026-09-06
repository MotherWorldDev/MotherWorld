# Region content schema

`region-content.json` has four layers:

```json
{
  "biomeFallbacks": {
    "1": {
      "climateSummary": "...",
      "tertiarySummary": "...",
      "plotsSummary": "..."
    }
  },
  "marineFallbacks": {
    "Tropical": { "climateSummary": "..." },
    "Temperate": { "climateSummary": "..." },
    "Polar": { "climateSummary": "..." }
  },
  "lakeFallback": {
    "climateSummary": "...",
    "tertiarySummary": "...",
    "plotsSummary": "..."
  },
  "regions": {
    "eco_1": {
      "climateSummary": "...",
      "tertiarySummary": "...",
      "plotsSummary": "...",
      "flagshipSpecies": ["Mountain gorilla (Gorilla beringei beringei)"],
      "threats": ["Agricultural expansion"],
      "contentSources": [
        {
          "name": "One Earth ecoregion snapshot",
          "publisher": "One Earth / RESOLVE",
          "url": "https://...",
          "use": "Paraphrased editorial reference..."
        }
      ]
    }
  }
}
```

Merge precedence is:

`biome/marine/lake fallback` → generated region metadata → explicit region override.

That means you can gradually enrich the site one region at a time without ever showing empty placeholder panels.

## Integration notes

- Content loads on the first region selection and is cached. Missing content does not prevent the globe or metadata from working; subsequent selections can retry a failed request.
- Empty summary strings and the original generated lake implementation notes do not override useful fallback content. Geographic IDs, coordinates, and metadata are preserved.
- The service records the source level of each summary in `contentLevels`; the sidebar distinguishes regional summaries from biome, marine-zone, and general-lake overviews. Regional source links are displayed below the text.
- Moon and open-ocean summaries retain their existing specialized behavior. Local and API-backed named regions use the same enrichment rules.
- Alashan's flagship entry is corrected to black stork and the Aleutian entry to red-legged kittiwake, matching their linked One Earth references. Other supplied editorial copy is retained.
- Run `node --test tests/region-content.test.mjs` to check coverage, merging, request caching, failure recovery, and special selections.

## Species pack precedence

The newer species-data pack replaces the visible Species tab with occurrence-backed recorded-species inventories. Legacy `tertiarySummary` and flagship entries are retained for compatibility but are not presented as recorded-species data. Climate and Threats continue to use the merge rules above. See [SPECIES_PIPELINE.md](SPECIES_PIPELINE.md).

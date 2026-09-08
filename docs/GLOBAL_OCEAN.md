# Global Ocean scope and family

MotherWorld deliberately separates **selection geometry** from **metric scope**.

- Visual/selectable region: `open_ocean` = the residual ocean outside mapped marine/coastal ecoregions.
- Species scope: that residual only, optionally organized into All / Pacific / Atlantic / Indian / Southern / Arctic tabs.
- Every non-species Global Ocean metric: the **entire ocean**, conceptually `open_ocean + all marine/coastal ecoregions`.

Native whole-ocean provider values are preferred when available because they are mathematically equivalent to aggregating the complete union and avoid boundary/resampling artifacts.

Current whole-ocean components:

- 0–2000 m ocean heat content
- global mean seawater pH / acidification
- satellite global mean sea level

The open-ocean regional Health card uses these full-ocean components. A family for which no defensible full-ocean aggregate exists is withheld rather than displaying a coastal-only proxy as global.

## Earth Health historical backbone (v3)

The fixed Earth Health ocean connector is NOAA World Ocean 0–700 m ocean heat content. The richer whole-ocean panel above remains the present-day diagnostic layer: pH, sea level and other ocean metrics can be shown without changing the historical/current headline formula. The selectable open-ocean geometry remains the residual; non-species Global Ocean metrics represent the whole ocean.

# Habitat family

The unified habitat provider keeps **direct conversion**, **forest loss**, and **supplemental conversion evidence** distinct.

Primary global layers:

- Dynamic World — crops/built probabilities and recent expansion trend
- Hansen/UMD Global Forest Change — tree-cover loss relative to year-2000 baseline forest
- optional WRI SDPT — planted forest / perennial tree-crop footprint
- mining and waste footprints — imported from the Land Pollution providers

The default family score uses crop/built retention, baseline-forest retention, cropland expansion pressure, and urban expansion pressure. Plantations, mining, and waste are currently retained as supplemental raw metrics instead of naively summing overlapping footprints. A future exact conversion-union builder can promote those to a scored direct-conversion component once overlaps are spatially resolved.

## Earth Health historical backbone (v3)

The global Earth Health habitat connector is a fixed C3S annual land-cover direct-conversion-retention series. Dynamic World, Hansen forest loss, planted-tree layers, mining, waste and industrial-footprint products remain detailed present-day diagnostics. They do not add hidden weight to the headline Earth Health score.

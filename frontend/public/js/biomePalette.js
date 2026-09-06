const FALLBACK_BIOME_COLORS = {
  1: "#2f9e44",
  2: "#3dbb6f",
  3: "#76c893",
  4: "#7fc97f",
  5: "#4d908e",
  6: "#577590",
  7: "#4ea8de",
  8: "#adb5bd",
  9: "#6c757d",
  10: "#1d7874",
  11: "#8ecae6",
  12: "#c77dff",
  13: "#e76f51",
  14: "#f4a261",
};

export function getBiomeColor(biomeNum, customPalette = {}) {
  if (biomeNum == null || Number.isNaN(Number(biomeNum))) {
    return customPalette.default || "#8b949e";
  }
  return customPalette[Number(biomeNum)] || FALLBACK_BIOME_COLORS[Number(biomeNum)] || customPalette.default || "#8b949e";
}

export function paletteMapFromIndex(indexPayload) {
  const palette = { default: "#8b949e" };
  if (!indexPayload?.biomes) {
    return palette;
  }
  for (const biome of indexPayload.biomes) {
    if (biome?.biomeNum != null && biome?.color) {
      palette[Number(biome.biomeNum)] = biome.color;
    }
  }
  return palette;
}

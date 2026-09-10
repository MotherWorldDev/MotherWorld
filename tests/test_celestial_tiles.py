import importlib.util
import json
from pathlib import Path
import unittest
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("celestial_tiles", ROOT / "scripts/build_celestial_tiles.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

class CelestialTileTests(unittest.TestCase):
    def test_gutters_wrap_dateline_and_clamp_poles(self):
        source = Image.new("RGB", (1024, 512))
        source.putdata([(x % 251, y % 251, (x + y) % 251) for y in range(512) for x in range(1024)])
        for left in [0, 512]:
            tile = module.gutter_tile(source, left, 0)
            self.assertEqual(tile.size, (516, 516))
            for tx in [0, 1, 2, 257, 513, 514, 515]:
                for ty in [0, 1, 2, 257, 513, 514, 515]:
                    expected = source.getpixel(((left + tx - 2) % 1024, min(511, max(0, ty - 2))))
                    self.assertEqual(tile.getpixel((tx, ty)), expected)

    def test_sky_overview_is_half_resolution_and_lossless(self):
        name, size = module.overview_spec("sky")
        self.assertEqual((name, size), ("overview-4k.webp", (4096, 2048)))
        for moon_mode in ("natural", "eclipse", "geology"):
            self.assertEqual(module.overview_spec(moon_mode), ("overview.webp", (1024, 512)))
        root = ROOT / module.SETS["sky"][2]
        manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["overview"], {"path": name, "width": size[0], "height": size[1]})
        with Image.open(ROOT / module.SETS["sky"][0]) as source, Image.open(root / name) as overview:
            self.assertEqual(overview.size, (source.width // 2, source.height // 2))
            with source.resize(size, Image.Resampling.LANCZOS) as expected:
                self.assertEqual(overview.tobytes(), expected.tobytes())

    def test_highest_level_tiles_preserve_source_pixels(self):
        for name, (source_path, max_level, tile_path, *_) in module.SETS.items():
            with Image.open(ROOT / source_path) as source:
                cols, rows = 2 ** (max_level + 1), 2 ** max_level
                for x, y in [(0, 0), (cols - 1, rows - 1), (cols // 2, rows // 2)]:
                    with Image.open(ROOT / tile_path / str(max_level) / str(x) / f"{y}.webp") as tile:
                        for tx, ty in [(0, 0), (2, 2), (255, 256), (513, 513), (515, 515)]:
                            sx = (x * 512 + tx - 2) % source.width
                            sy = min(source.height - 1, max(0, y * 512 + ty - 2))
                            self.assertEqual(tile.getpixel((tx, ty)), source.getpixel((sx, sy)), (name, x, y, tx, ty))

if __name__ == "__main__":
    unittest.main()

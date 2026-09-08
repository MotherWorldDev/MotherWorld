# Moon Selenology

MotherWorld treats Selenology as a Moon-only descriptive module. It has zero Earth Health weight.

Opening **Selenology** can switch the existing lit Moon sphere from the normal LROC texture to a web texture derived from the USGS **Unified Geologic Map of the Moon, 1:5M (2020, v2)**. Leaving the tab restores the natural surface. Solar illumination stays live because the same Moon material is used.

The source product is globally consistent and CC0. The package does not redistribute the source archive or a pre-rendered derivative.

Build the texture with:

```bash
python scripts/build_moon_selenology_texture.py --download
```

Or use `--source PATH`. The builder writes 4096×2048 and 8192×4096 WebP textures and flips `frontend/public/data/moon/selenology.json` to `texture.available=true`.

Optional `--sld path/to/file.sld` extracts a compact legend.

The public schema reserves sections for surface geology, geologic age, LOLA/Kaguya topography, GRAIL crust/gravity, M3/Diviner/Lunar Prospector mineralogy, volcanism, tectonics/moonquakes, polar volatiles, impact history and returned samples. Unbuilt providers remain visibly unbuilt rather than fabricated.

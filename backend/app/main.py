from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware


ROOT = Path(__file__).resolve().parents[2]
INDEX_PATH = ROOT / "frontend" / "public" / "data" / "regions.index.json"

app = FastAPI(title="BiomeSummary API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:8080", "http://localhost:8080"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@lru_cache(maxsize=1)
def load_index() -> dict:
    if not INDEX_PATH.exists():
        raise FileNotFoundError(
            f"Processed metadata not found at {INDEX_PATH}. Run scripts/preprocess_ecoregions.py first."
        )
    return json.loads(INDEX_PATH.read_text(encoding="utf-8"))


@app.get("/api/health")
def health() -> dict:
    return {"ok": True}


@app.get("/api/regions")
def list_regions(
    q: str | None = Query(default=None, description="Optional name contains filter"),
    biome_num: int | None = Query(default=None, alias="biomeNum"),
    limit: int = Query(default=50, ge=1, le=500),
) -> dict:
    data = load_index()
    regions = list(data.get("regions", {}).values())

    if q:
        q_norm = q.strip().lower()
        regions = [r for r in regions if q_norm in str(r.get("name", "")).lower()]
    if biome_num is not None:
        regions = [r for r in regions if r.get("biomeNum") == biome_num]

    items = regions[:limit]
    return {"count": len(regions), "items": items}


@app.get("/api/regions/{region_id}")
def get_region(region_id: str) -> dict:
    data = load_index()
    region = data.get("regions", {}).get(region_id)
    if not region:
        raise HTTPException(status_code=404, detail=f"Region not found: {region_id}")
    return region


@app.get("/api/biomes")
def list_biomes() -> dict:
    data = load_index()
    return {"items": data.get("biomes", [])}

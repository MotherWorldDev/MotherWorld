from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_json(path: Path, default=None):
    if not path.exists():
        return {} if default is None else default
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload, *, compact: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, separators=(",", ":")) if compact else json.dumps(payload, ensure_ascii=False, indent=2)
    path.write_text(text + ("" if compact else "\n"), encoding="utf-8")


def finite_number(value):
    try:
        n = float(value)
    except (TypeError, ValueError):
        return None
    return n if math.isfinite(n) else None


def clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def score_level(score: float | None) -> int | None:
    if score is None:
        return None
    return int(clamp(round(score), 1, 100))


def geometric_mean(scores: Iterable[tuple[float, float]]) -> float | None:
    """Weighted geometric mean for 0..100 scores.

    Input is (score, weight). A true zero makes the result zero. Invalid/nonpositive
    weights are skipped. Weights do not need to sum to one.
    """
    pairs = [(finite_number(s), finite_number(w)) for s, w in scores]
    pairs = [(s, w) for s, w in pairs if s is not None and w is not None and w > 0]
    if not pairs:
        return None
    if any(s <= 0 for s, _ in pairs):
        return 0.0
    total_w = sum(w for _, w in pairs)
    return math.exp(sum(w * math.log(clamp(s, 1e-9, 100.0)) for s, w in pairs) / total_w)


def weighted_mean(values: Iterable[tuple[float, float]]) -> float | None:
    pairs = [(finite_number(v), finite_number(w)) for v, w in values]
    pairs = [(v, w) for v, w in pairs if v is not None and w is not None and w > 0]
    if not pairs:
        return None
    total = sum(w for _, w in pairs)
    return sum(v * w for v, w in pairs) / total


def linear_trend_per_decade(series: list[dict]) -> float | None:
    pairs = []
    for row in series:
        year = finite_number(row.get("year"))
        score = finite_number(row.get("score"))
        if year is not None and score is not None:
            pairs.append((year, score))
    if len(pairs) < 3:
        return None
    xmean = sum(x for x, _ in pairs) / len(pairs)
    ymean = sum(y for _, y in pairs) / len(pairs)
    denom = sum((x - xmean) ** 2 for x, _ in pairs)
    if denom <= 0:
        return None
    slope = sum((x - xmean) * (y - ymean) for x, y in pairs) / denom
    return slope * 10.0


def status_label(score: float | None, thresholds: list[dict] | None = None) -> str:
    if score is None:
        return "Insufficient data"
    rules = thresholds or [
        {"min": 85, "label": "Thriving"},
        {"min": 70, "label": "Stable"},
        {"min": 50, "label": "Strained"},
        {"min": 30, "label": "Degraded"},
        {"min": 0, "label": "Critical"},
    ]
    for rule in sorted(rules, key=lambda r: float(r.get("min", 0)), reverse=True):
        if score >= float(rule.get("min", 0)):
            return str(rule.get("label") or "")
    return "Critical"


def aggregate_family_regions(rows: list[dict], *, score_key: str = "score", area_key: str = "areaKm2", coverage_key: str = "coverage") -> dict:
    """Area-weight regional family scores into a global family score.

    Spatial aggregation is arithmetic because this is estimating the mean condition
    over represented area. Cross-family aggregation is geometric (elsewhere).
    """
    score_pairs = []
    coverage_pairs = []
    represented_area = 0.0
    total_area = 0.0
    region_count = 0
    for row in rows:
        area = finite_number(row.get(area_key))
        if area is None or area <= 0:
            continue
        total_area += area
        score = finite_number(row.get(score_key))
        coverage = finite_number(row.get(coverage_key))
        coverage = clamp(coverage if coverage is not None else 1.0, 0.0, 1.0)
        coverage_pairs.append((coverage, area))
        if score is None:
            continue
        represented_area += area * coverage
        score_pairs.append((clamp(score, 0, 100), area * coverage))
        region_count += 1
    return {
        "score": weighted_mean(score_pairs),
        "coverage": weighted_mean(coverage_pairs) if coverage_pairs else 0.0,
        "representedAreaKm2": represented_area,
        "totalAreaKm2": total_area,
        "regionCount": region_count,
        "aggregation": "area_weighted_arithmetic_mean_of_regional_family_scores",
    }

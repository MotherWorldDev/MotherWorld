from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path


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


def finite(value):
    try:
        n = float(value)
    except (TypeError, ValueError):
        return None
    return n if math.isfinite(n) else None


def clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def geometric_mean(pairs: list[tuple[float, float]]) -> float | None:
    valid = [(float(s), float(w)) for s, w in pairs if finite(s) is not None and finite(w) is not None and w > 0]
    if not valid:
        return None
    if any(s <= 0 for s, _ in valid):
        return 0.0
    total = sum(w for _, w in valid)
    return math.exp(sum(w * math.log(clamp(s, 1e-9, 100.0)) for s, w in valid) / total)


def linear_trend_per_decade(series: list[dict], value_key: str = "value") -> float | None:
    pairs = []
    for row in series:
        year = finite(row.get("year"))
        value = finite(row.get(value_key))
        if year is not None and value is not None:
            pairs.append((year, value))
    if len(pairs) < 3:
        return None
    xm = sum(x for x, _ in pairs) / len(pairs)
    ym = sum(y for _, y in pairs) / len(pairs)
    denom = sum((x - xm) ** 2 for x, _ in pairs)
    if denom <= 0:
        return None
    slope = sum((x - xm) * (y - ym) for x, y in pairs) / denom
    return slope * 10.0


def stability_score_from_abs_z(abs_z: float | None, *, full_score_z: float = 0.5, zero_score_z: float = 3.0) -> float | None:
    """Transparent stability score from a standardized departure.

    <=0.5σ -> 100; >=3σ -> 0; linear in between. The score deliberately
    penalizes unusually wet and unusually dry departures because this family is
    measuring stability relative to the historical hydrological regime.
    """
    z = finite(abs_z)
    if z is None:
        return None
    z = abs(z)
    if z <= full_score_z:
        return 100.0
    if z >= zero_score_z:
        return 0.0
    return 100.0 * (zero_score_z - z) / (zero_score_z - full_score_z)


def score_from_expected_in_envelope(observed_fraction: float | None, expected_fraction: float = 0.80) -> float | None:
    value = finite(observed_fraction)
    if value is None or expected_fraction <= 0:
        return None
    return clamp(100.0 * value / expected_fraction, 0.0, 100.0)

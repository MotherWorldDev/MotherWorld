#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("path", type=Path)
    args = p.parse_args()
    data = json.loads(args.path.read_text(encoding="utf-8"))
    print(data.get("regionId"), data.get("regionName"))
    print(data.get("source", {}).get("label"))
    for key, dist in data.get("distributions", {}).items():
        total = sum(dist.get("percent", []))
        print(key, f"bins={len(dist.get('percent', []))}", f"percent_sum={total:.6f}", dist.get("stats", {}))


if __name__ == "__main__":
    main()

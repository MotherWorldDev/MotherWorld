#!/usr/bin/env python
from __future__ import annotations
import argparse, json
from pathlib import Path

p = argparse.ArgumentParser()
p.add_argument("path", type=Path)
args = p.parse_args()
data = json.loads(args.path.read_text(encoding="utf-8"))
print(json.dumps({
    "regionId": data.get("regionId"),
    "regionName": data.get("regionName"),
    "providerOrder": data.get("providerOrder"),
    "summary": data.get("summary"),
}, indent=2))

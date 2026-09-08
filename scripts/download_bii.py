#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
from pathlib import Path

import requests

PACKAGE_ID = "bii-developed-by-nhm-v2-1-1-limited-release"
API = f"https://data.nhm.ac.uk/api/3/action/package_show?id={PACKAGE_ID}"


def main():
    p = argparse.ArgumentParser(description="Download NHM BII v2.1.1 limited release after explicit license acknowledgement.")
    p.add_argument("--output", type=Path, default=Path("biodiversity_raw/nhm_bii"))
    p.add_argument("--accept-noncommercial-license", action="store_true")
    args = p.parse_args()
    if not args.accept_noncommercial_license:
        raise SystemExit(
            "Refusing automatic download without --accept-noncommercial-license. The public BII release is CC BY-NC-SA 4.0 and non-commercial."
        )
    args.output.mkdir(parents=True, exist_ok=True)
    meta = requests.get(API, timeout=60).json()
    if not meta.get("success"):
        raise RuntimeError(meta)
    resources = meta["result"].get("resources", [])
    resource = next((r for r in resources if str(r.get("format", "")).upper() == "ZIP"), None)
    if not resource or not resource.get("url"):
        raise RuntimeError("Could not find BII ZIP resource URL in NHM CKAN response")
    target = args.output / (resource.get("name") or "nhm-bii-v2.1.1.zip")
    if target.suffix.lower() != ".zip":
        target = target.with_suffix(".zip")
    print(f"Downloading {resource['url']} -> {target}")
    with requests.get(resource["url"], stream=True, timeout=180) as res:
        res.raise_for_status()
        with target.open("wb") as fh:
            for chunk in res.iter_content(1024 * 1024):
                if chunk:
                    fh.write(chunk)
    (args.output / "LICENSE-NOTE.json").write_text(
        json.dumps(
            {
                "dataset": "NHM Biodiversity Intactness Index v2.1.1 Open Access Limited Release",
                "license": "CC BY-NC-SA 4.0 / non-commercial limited release",
                "acceptedByCommandFlag": True,
                "datasetUrl": "https://data.nhm.ac.uk/dataset/bii-developed-by-nhm-v2-1-1-limited-release",
                "doi": "10.5519/k33reyb6",
            },
            indent=2,
        ) + "\n",
        encoding="utf-8",
    )
    print(target)


if __name__ == "__main__":
    main()

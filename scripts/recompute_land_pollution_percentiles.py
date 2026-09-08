#!/usr/bin/env python
from pathlib import Path
from land_pollution_common import recompute_peer_percentiles
recompute_peer_percentiles(Path(__file__).resolve().parents[1])
print('Land-pollution peer percentiles recomputed.')

#!/usr/bin/env bash
set -euo pipefail
TARGET="${1:-biodiversity_raw/phylacine}"
if [[ -d "$TARGET/.git" ]]; then
  git -C "$TARGET" pull --ff-only
else
  mkdir -p "$(dirname "$TARGET")"
  git clone --depth 1 https://github.com/MegaPast2Future/PHYLACINE_1.2.git "$TARGET"
fi
printf 'PHYLACINE ready at %s\n' "$TARGET"

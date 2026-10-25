#!/bin/sh
# Run on Batocera, not Windows. No global package installation.
set -eu
HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
export PYTHONDONTWRITEBYTECODE=1
exec python3 -B "$HERE/ccc.py" "$@"

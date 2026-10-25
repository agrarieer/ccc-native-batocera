#!/bin/sh
set -eu
APP=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
: "${CCC_INSTALL_ROOT:=$(dirname "$APP")}"
export CCC_INSTALL_ROOT PYTHONDONTWRITEBYTECODE=1
# The system Python3 supervises bundled Python2; do not inherit its home/libs.
unset PYTHONHOME PYTHONPATH LD_LIBRARY_PATH
exec python3 "$APP/launch.py" "$@"

#!/bin/sh
set -eu
cd "$(dirname "$0")/prototype"
exec python3 server.py --port 0 --open

#!/usr/bin/env bash
# Linux / macOS: ./run.sh   (same options as run.py, e.g. ./run.sh --reset)
cd "$(dirname "$0")"
exec python3 run.py "$@"

#!/bin/sh
cd "$(dirname "$0")" || exit 1
unset PYTHONHOME
exec python3 -I -S start.py

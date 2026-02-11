#!/usr/bin/env bash
# v2 deploy fix
set -e

echo "=== Installing dependencies ==="
pip install -r requirements.txt

echo "=== Running scrapers ==="
python run_scrapers.py

echo "=== Build complete ==="

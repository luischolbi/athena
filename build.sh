#!/usr/bin/env bash
set -e

echo "=== Installing dependencies ==="
pip install -r requirements.txt

echo "=== Running scrapers ==="
python run_scrapers.py

echo "=== Build complete ==="

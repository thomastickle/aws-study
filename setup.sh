#!/usr/bin/env sh
set -eu
python3 -m venv .venv
printf '\nReady. No package downloads are required. Try:\n  .venv/bin/python aws-study.py stats --cert CLF-C02\n  .venv/bin/python aws-study.py quiz --cert CLF-C02 -n 20 --year 2026\n'

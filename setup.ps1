$ErrorActionPreference = "Stop"
py -m venv .venv
Write-Host ""
Write-Host "Ready. No package downloads are required. Try:"
Write-Host "  .\.venv\Scripts\python.exe .\aws-study.py stats --cert CLF-C02"
Write-Host "  .\.venv\Scripts\python.exe .\aws-study.py quiz --cert CLF-C02 -n 20 --year 2026"

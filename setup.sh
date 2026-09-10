#!/usr/bin/env bash
# Script de preparacao do ambiente de nuvem do Claude Code.
set -euo pipefail
python -m pip install --upgrade pip
pip install -r requirements.txt
echo "Dependencias instaladas."

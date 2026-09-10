#!/usr/bin/env bash
# Preparacao do ambiente Python do CRM.
#
# O campo de setup do ambiente de nuvem fica VAZIO: quem prepara a sessao e o
# hook SessionStart (.claude/hooks/session-start.sh), registrado em
# .claude/settings.json. Este script existe so para rodar o mesmo passo a mao.
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

CLAUDE_CODE_REMOTE=true \
CLAUDE_PROJECT_DIR="$PROJECT_DIR" \
  "$PROJECT_DIR/.claude/hooks/session-start.sh"

echo "Dependencias instaladas em $PROJECT_DIR/.venv"

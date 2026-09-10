#!/usr/bin/env bash
# SessionStart hook -- prepara o ambiente Python do CRM.
#
# Roda SOMENTE no ambiente de nuvem do Claude Code (claude.ai/code).
# Em maquina local sai imediatamente, sem tocar em nada.
#
# O que faz:
#   1. cria .venv isolado (sem --system-site-packages) se ainda nao existir;
#   2. instala requirements.txt dentro do .venv;
#   3. roda `pip check` -- se falhar, aborta com status != 0;
#   4. poe o .venv no PATH da sessao via CLAUDE_ENV_FILE.
#
# Nao le credenciais, nao acessa a Google Sheets, nao dispara prospeccao.
set -euo pipefail

# Cloud-only: sem isto, sai sem efeito colateral.
if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

# CLAUDE_PROJECT_DIR e a raiz do repositorio. Fallback: pasta deste script.
PROJECT_DIR="${CLAUDE_PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
VENV="$PROJECT_DIR/.venv"
REQS="$PROJECT_DIR/requirements.txt"

if [ ! -f "$REQS" ]; then
  echo "session-start: requirements.txt nao encontrado em $PROJECT_DIR -- nada a instalar." >&2
  exit 0
fi

# O python do sistema traz um cryptography quebrado (sem _cffi_backend).
# O venv isolado nao enxerga dist-packages, entao a versao do PyPI prevalece.
if [ ! -x "$VENV/bin/python" ]; then
  echo "session-start: criando venv isolado em $VENV"
  python3 -m venv "$VENV"
fi

PY="$VENV/bin/python"

# Idempotente: pip resolve o que ja esta satisfeito e nao reinstala.
"$PY" -m pip install --quiet --upgrade pip
"$PY" -m pip install --quiet --requirement "$REQS"

# pip check quebrado = ambiente inconsistente. Falha alto: nao adianta a sessao
# comecar e so descobrir na primeira chamada da Sheets API.
if ! "$PY" -m pip check; then
  echo "session-start: FALHOU -- pip check reportou inconsistencias (veja acima)." >&2
  echo "session-start: ambiente NAO esta pronto." >&2
  exit 1
fi

# Deixa o interpretador do venv como padrao para o resto da sessao.
if [ -n "${CLAUDE_ENV_FILE:-}" ]; then
  {
    echo "export VIRTUAL_ENV=\"$VENV\""
    echo "export PATH=\"$VENV/bin:\$PATH\""
  } >> "$CLAUDE_ENV_FILE"
fi

echo "session-start: ambiente pronto -- $("$PY" --version), deps de requirements.txt instaladas em .venv"

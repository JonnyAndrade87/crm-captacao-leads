#!/usr/bin/env python3
"""Acrescenta UMA linha a aba 'Execucoes' e, em seguida, verifica a escrita.

- Escrita apenas via values().append (INSERT_ROWS / RAW). Nunca batchUpdate.
- Aborta sem escrever se o ID informado ja existir. O gateway repete essa
  checagem no servidor e devolve 409 se o ID chegar duplicado mesmo assim.
- Apos escrever, roda verify_write.verify(): intervalo gravado, releitura,
  cobertura de tabela nativa e dropdowns na linha nova.

Status permitido (unica lista aceita): Em andamento | Concluída | Parcial | Falhou.
Para o teste de conexao: Status = "Concluída"; o texto "Teste de conexão" vai
no --id e no --resumo, nunca no Status.

Exemplos:
  # teste de conexao
  python append_execucao.py \
      --id "TESTE-CONEXAO-20260910-1200" \
      --status "Concluída" \
      --resumo "Teste de conexão da rotina na nuvem; nenhuma prospecção executada."

  # execucao real (mais tarde)
  python append_execucao.py --status "Concluída" --candidatos 12 --adicionados 3 \
      --resumo "3 leads aderentes adicionados."
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

from sheets_client import GatewayError, append_row, read_range
from verify_write import verify

TAB = "Execuções"
TZ = ZoneInfo("America/Sao_Paulo")

ALLOWED_STATUS = ["Em andamento", "Concluída", "Parcial", "Falhou"]

# Ordem exata das 10 colunas da aba Execucoes.
COLUMNS = [
    "ID da execução",
    "Início",
    "Fim",
    "Status",
    "Candidatos analisados",
    "Leads adicionados",
    "Leads atualizados",
    "Descartados",
    "Erro / limitação",
    "Resumo",
]


def _now() -> str:
    return datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S")


def _existing_ids() -> list[str]:
    return [str(r[0]) for r in read_range(f"'{TAB}'!A2:A") if r and str(r[0]).strip() != ""]


def _args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Adiciona uma linha em Execucoes e verifica.")
    p.add_argument("--id", default=None, help="ID da execucao (padrao: EXEC-<timestamp>)")
    p.add_argument("--status", required=True, choices=ALLOWED_STATUS)
    p.add_argument("--resumo", required=True)
    p.add_argument("--inicio", default=None)
    p.add_argument("--fim", default=None)
    p.add_argument("--candidatos", type=int, default=0)
    p.add_argument("--adicionados", type=int, default=0)
    p.add_argument("--atualizados", type=int, default=0)
    p.add_argument("--descartados", type=int, default=0)
    p.add_argument("--erro", default="")
    return p.parse_args()


def main() -> None:
    args = _args()
    stamp = datetime.now(TZ).strftime("%Y%m%d-%H%M%S")
    exec_id = args.id or f"EXEC-{stamp}"

    ids = _existing_ids()
    if exec_id in ids:
        raise SystemExit(f"ID {exec_id!r} ja existe em {TAB}. Abortado (nada gravado).")

    row = [
        exec_id,
        args.inicio or _now(),
        args.fim or _now(),
        args.status,
        args.candidatos,
        args.adicionados,
        args.atualizados,
        args.descartados,
        args.erro,
        args.resumo,
    ]

    print(f"{TAB}: {len(ids)} execucao(oes) ja registrada(s).")
    print("Linha a acrescentar:")
    for col, val in zip(COLUMNS, row):
        print(f"  {col}: {val}")

    try:
        resp = append_row(TAB, row)
    except GatewayError as exc:
        if exc.status == 409:
            raise SystemExit(f"Gateway recusou por duplicidade: {exc.message}")
        raise SystemExit(f"Gateway recusou a escrita (HTTP {exc.status}): {exc.message}")
    upd = resp.get("updates", {})
    updated_range = upd.get("updatedRange", "")
    print("\nEscrita concluida:")
    print(f"  intervalo atualizado: {updated_range}")
    print(f"  linhas gravadas:      {upd.get('updatedRows')}")

    print("\n--- verificacao pos-escrita ---")
    ok = verify(updated_range, row)
    sys.exit(0 if ok else 2)


if __name__ == "__main__":
    main()

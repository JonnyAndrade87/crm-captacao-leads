#!/usr/bin/env python3
"""Leitura de volta (readback) da aba 'Execucoes'.

  python read_execucoes.py                       # ultimas 10 linhas
  python read_execucoes.py --id TESTE-CONEXAO-... # confirma um registro
"""

from __future__ import annotations

import argparse

from sheets_client import read_range

TAB = "Execuções"


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--id", default=None, help="Filtra por ID da execucao")
    p.add_argument("--last", type=int, default=10, help="Mostra as N ultimas linhas")
    args = p.parse_args()

    values = read_range(f"'{TAB}'!A1:J")
    if not values:
        raise SystemExit("Aba vazia.")

    header, rows = values[0], values[1:]
    if args.id:
        rows = [r for r in rows if r and str(r[0]) == args.id]
        if not rows:
            raise SystemExit(f"ID {args.id!r} nao encontrado em {TAB}.")
    else:
        rows = rows[-args.last :]

    widths = [len(h) for h in header]
    for r in rows:
        for i, c in enumerate(r):
            if i < len(widths):
                widths[i] = max(widths[i], len(str(c)))

    def fmt(cells: list) -> str:
        padded = list(cells) + [""] * (len(header) - len(cells))
        return " | ".join(str(c).ljust(widths[i]) for i, c in enumerate(padded[: len(header)]))

    print(fmt(header))
    print("-+-".join("-" * w for w in widths))
    for r in rows:
        print(fmt(r))


if __name__ == "__main__":
    main()

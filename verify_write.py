#!/usr/bin/env python3
"""Verificacao pos-escrita. NAO corrige nada -- apenas confere e relata.

Checagens:
  1. Intervalo realmente gravado (updatedRange da API) e uma unica linha.
  2. Releitura desse intervalo: os valores conferem com o enviado.
  3. Cobertura de tabela nativa: a linha gravada caiu dentro do range da
     tabela nativa da aba (ou seja, a tabela se estendeu)?
  4. Validacoes / dropdowns presentes nas celulas da linha gravada.

Uso direto:
  python verify_write.py "Execuções!A25:J25" val1 val2 ... val10
"""

from __future__ import annotations

import re
import sys

from sheets_client import get_grid, get_tables, read_range

_A1_RE = re.compile(
    r"^'?(?P<tab>[^'!]+)'?!(?P<c1>[A-Z]+)(?P<r1>\d+):(?P<c2>[A-Z]+)(?P<r2>\d+)$"
)


def _col_to_idx(col: str) -> int:
    n = 0
    for ch in col:
        n = n * 26 + (ord(ch) - 64)
    return n - 1  # base 0


def verify(updated_range: str, expected_row: list) -> bool:
    m = _A1_RE.match(updated_range)
    if not m:
        print(f"[ATENCAO] intervalo nao reconhecido: {updated_range!r}")
        return False

    tab = m.group("tab")
    r1, r2 = int(m.group("r1")), int(m.group("r2"))
    c1 = _col_to_idx(m.group("c1"))
    c2 = _col_to_idx(m.group("c2"))
    written0 = r1 - 1  # indice base 0 da linha gravada
    ok = True

    print(f"Intervalo gravado pela API: {updated_range} "
          f"(linha {r1}{'' if r1 == r2 else f'-{r2}'}, colunas {c1 + 1}-{c2 + 1})")
    if r1 != r2:
        print("[ATENCAO] a escrita cobriu mais de uma linha.")
        ok = False

    # 1/2. Releitura
    got = read_range(updated_range)
    got_row = [str(x) for x in (got[0] if got else [])]
    exp_row = [str(x) for x in expected_row]
    if got_row == exp_row:
        print("[PASS] releitura confere com o enviado.")
    else:
        print("[ATENCAO] releitura difere do enviado:")
        print(f"  enviado: {exp_row}")
        print(f"  lido:    {got_row}")
        ok = False

    # 3. Tabela nativa
    tables = [t for t in get_tables() if t["title"] == tab]
    if not tables:
        print(f"[INFO] aba {tab!r} sem tabela nativa exposta pela API -- nada a estender.")
    for t in tables:
        rng = t.get("range") or {}
        start = rng.get("startRowIndex", 0)
        end = rng.get("endRowIndex")  # exclusivo, base 0
        inside = end is not None and start <= written0 < end
        print(f"[{'PASS' if inside else 'ATENCAO'}] tabela nativa {t['name']!r}: "
              f"linha {'DENTRO' if inside else 'FORA'} do range "
              f"(linhas base-0 {start}..{end}).")
        if not inside:
            print("  -> a tabela nativa NAO se estendeu sozinha. "
                  "Nenhuma acao automatica foi tomada; ajuste manual pode ser preciso.")
            ok = False

    # 4. Validacoes / dropdowns na linha gravada
    # Os campos lidos (dataValidation, userEnteredValue) sao fixados no gateway.
    grid = get_grid(updated_range)
    try:
        cells = grid["sheets"][0]["data"][0]["rowData"][0].get("values", [])
    except (KeyError, IndexError):
        cells = []
    with_dv = []
    for i, cell in enumerate(cells):
        dv = cell.get("dataValidation")
        if dv:
            cond = (dv.get("condition") or {}).get("type", "?")
            with_dv.append(f"col {c1 + i + 1} ({cond})")
    if with_dv:
        print("[INFO] validacao aplicada na linha gravada: " + ", ".join(with_dv))
    else:
        print("[INFO] nenhuma validacao/dropdown detectada na linha gravada. "
              "Se a aba usa dropdown por coluna, confirme se o append herdou.")

    print("\nRESULTADO:", "PASS" if ok else "ATENCAO -- revisar itens acima")
    return ok


if __name__ == "__main__":
    if len(sys.argv) < 3:
        raise SystemExit('uso: python verify_write.py "<intervalo A1>" v1 v2 ...')
    sys.exit(0 if verify(sys.argv[1], sys.argv[2:]) else 2)

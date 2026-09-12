#!/usr/bin/env python3
"""Inspeciona a base ANTES de qualquer escrita.

Mostra, para cada aba: cabecalho, limites da grade, linhas com conteudo e
tabelas nativas. Lista os IDs ja existentes (Leads, Nao contatar, Execucoes)
para deducao / verificacao de duplicados.
"""

from __future__ import annotations

from sheets_client import describe_auth, get_structure, read_range, spreadsheet_id

TABS = ["Leads", "Acompanhamento", "Não contatar", "Execuções", "Configuração"]


def _ids(a1_range: str) -> list[str]:
    return [str(r[0]) for r in read_range(a1_range) if r and str(r[0]).strip() != ""]


def main() -> None:
    print(describe_auth())
    print(f"Planilha: {spreadsheet_id()}\n")

    struct = get_structure()
    by_title = {s["properties"]["title"]: s for s in struct.get("sheets", [])}

    for tab in TABS:
        sheet = by_title.get(tab)
        if not sheet:
            print(f"[AVISO] Aba nao encontrada: {tab}\n")
            continue
        grid = sheet["properties"].get("gridProperties", {})
        tables = sheet.get("tables", [])
        values = read_range(f"'{tab}'!A1:AZ")
        header = values[0] if values else []
        data_rows = max(len(values) - 1, 0)

        print(f"== {tab} ==")
        print(f"  grade: {grid.get('rowCount')} linhas x {grid.get('columnCount')} colunas")
        print(f"  conteudo: {len(values)} linhas (cabecalho + {data_rows} de dados)")
        print(f"  cabecalho ({len(header)} col): {header}")
        for t in tables:
            print(f"  tabela nativa: {t.get('name')!r} -> range {t.get('range')}")
        print()

    leads_ids = _ids("'Leads'!A2:A")
    nao_contatar_ids = _ids("'Não contatar'!A2:A")
    execucoes_ids = _ids("'Execuções'!A2:A")
    print(f"IDs em Leads ({len(leads_ids)}):        {leads_ids}")
    print(f"IDs em Nao contatar ({len(nao_contatar_ids)}): {nao_contatar_ids}")
    print(f"IDs em Execucoes ({len(execucoes_ids)}):    {execucoes_ids}")
    print()
    print("Status permitido em Execucoes: Em andamento | Concluída | Parcial | Falhou")
    print("(o rotulo 'Teste de conexão' vai no ID e no Resumo, nunca no Status)")


if __name__ == "__main__":
    main()

"""Cliente minimo da Google Sheets API para o CRM de captacao de leads.

Politica de escrita (nao e garantia de preservacao -- ver verify_write.py):
- NUNCA chamar spreadsheets().batchUpdate().
- Escrita SOMENTE via spreadsheets().values().append() com
  insertDataOption="INSERT_ROWS" e valueInputOption="RAW". E a operacao de
  escrita menos invasiva disponivel: acrescenta uma linha nova e nao reescreve
  celulas existentes. Ela NAO garante que uma tabela nativa do Sheets se
  estenda sozinha nem que a validacao/dropdown de uma coluna seja herdada pela
  linha nova. Por isso toda escrita e conferida depois por verify_write.py.
- Leitura via values().get() e spreadsheets().get().

Credenciais: conta de servico do Google Cloud, via variavel de ambiente
GOOGLE_SERVICE_ACCOUNT_JSON (conteudo integral do arquivo JSON). Num ambiente
de nuvem do Claude Code essa variavel e LEGIVEL por qualquer comando da sessao
(inclusive comandos do Claude) e por quem usar o ambiente -- ver README.
"""

from __future__ import annotations

import json
import os
from functools import lru_cache

from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

DEFAULT_SPREADSHEET_ID = "1hggTeE7kfhuodjM4FG3nO5Sg4N7_psJlBISodiA87p8"


def spreadsheet_id() -> str:
    return os.environ.get("SPREADSHEET_ID", DEFAULT_SPREADSHEET_ID)


def _load_credentials() -> Credentials:
    raw = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
    if not raw:
        raise SystemExit(
            "Variavel GOOGLE_SERVICE_ACCOUNT_JSON ausente. "
            "Defina-a nas variaveis de ambiente do ambiente de nuvem."
        )
    try:
        info = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"GOOGLE_SERVICE_ACCOUNT_JSON nao e JSON valido: {exc}")
    return Credentials.from_service_account_info(info, scopes=SCOPES)


@lru_cache(maxsize=1)
def get_service():
    return build("sheets", "v4", credentials=_load_credentials(), cache_discovery=False)


def get_structure() -> dict:
    """Propriedades das abas + tabelas nativas (sem dados de celula)."""
    svc = get_service()
    last_exc: Exception | None = None
    for fields in ("sheets(properties,tables)", "sheets(properties)"):
        try:
            return (
                svc.spreadsheets()
                .get(spreadsheetId=spreadsheet_id(), fields=fields)
                .execute()
            )
        except HttpError as exc:
            last_exc = exc
    raise last_exc  # type: ignore[misc]


def get_tables() -> list[dict]:
    """Tabelas nativas por aba: [{title, name, tableId, range}]. Vazio se a API
    nao expuser o recurso."""
    svc = get_service()
    try:
        resp = (
            svc.spreadsheets()
            .get(
                spreadsheetId=spreadsheet_id(),
                fields="sheets(properties(title,sheetId),tables(tableId,name,range))",
            )
            .execute()
        )
    except HttpError:
        return []
    out: list[dict] = []
    for sheet in resp.get("sheets", []):
        title = sheet["properties"]["title"]
        for tbl in sheet.get("tables", []):
            out.append(
                {
                    "title": title,
                    "name": tbl.get("name"),
                    "tableId": tbl.get("tableId"),
                    "range": tbl.get("range"),
                }
            )
    return out


def read_range(a1_range: str) -> list[list]:
    svc = get_service()
    resp = (
        svc.spreadsheets()
        .values()
        .get(
            spreadsheetId=spreadsheet_id(),
            range=a1_range,
            valueRenderOption="UNFORMATTED_VALUE",
            dateTimeRenderOption="FORMATTED_STRING",
        )
        .execute()
    )
    return resp.get("values", [])


def get_grid(a1_range: str, fields: str) -> dict:
    """spreadsheets().get com includeGridData para inspecionar celulas
    (ex.: dataValidation) de um intervalo pequeno."""
    svc = get_service()
    return (
        svc.spreadsheets()
        .get(
            spreadsheetId=spreadsheet_id(),
            ranges=[a1_range],
            includeGridData=True,
            fields=fields,
        )
        .execute()
    )


def append_row(tab: str, row: list) -> dict:
    """Acrescenta UMA linha ao fim da aba. Operacao de escrita menos invasiva
    disponivel -- confira o resultado depois com verify_write.verify()."""
    svc = get_service()
    return (
        svc.spreadsheets()
        .values()
        .append(
            spreadsheetId=spreadsheet_id(),
            range=f"'{tab}'!A1",
            valueInputOption="RAW",
            insertDataOption="INSERT_ROWS",
            body={"values": [row]},
        )
        .execute()
    )

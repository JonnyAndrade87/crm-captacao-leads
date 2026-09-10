#!/usr/bin/env python3
"""Gateway HTTP do CRM de captacao de leads (Cloud Run).

Motivo de existir
-----------------
A Google Sheets API so aceita `Authorization: Bearer <access_token>` OAuth2 de
vida curta, obtido assinando um JWT com a chave privada da conta de servico. O
recurso "API credentials" do ambiente de nuvem do Claude Code injeta um header
ESTATICO -- ele nao consegue autenticar `sheets.googleapis.com` diretamente.

Este servico e a ponte:
  sessao Claude --(header estatico X-CRM-Token, injetado pelo proxy)--> gateway
  gateway --(ADC da conta de servico anexada ao Cloud Run)--> Sheets API

Nenhuma chave JSON existe. No Cloud Run a conta de servico e a *identidade de
execucao* do servico e o token vem do metadata server (google.auth.default).

Politica imposta AQUI, no servidor -- o cliente nao escolhe
-----------------------------------------------------------
- O ID da planilha vem de SPREADSHEET_ID (env de deploy). O cliente nunca envia
  um ID; nao ha como apontar este gateway para outra planilha.
- Somente as abas de TABS sao acessiveis, e so as abas com append=True aceitam
  escrita. `Não contatar` e `Configuração` sao somente leitura.
- Escrita SOMENTE via values().append (INSERT_ROWS / RAW), uma unica linha por
  chamada. `spreadsheets().batchUpdate` nao e chamado em lugar nenhum.
- A linha precisa ter exatamente o mesmo numero de colunas do cabecalho da aba.
- Deduplicacao por coluna de ID e feita antes de gravar (409 se ja existir).
- Em `Execuções`, a coluna Status so aceita a lista fechada de ALLOWED_STATUS.

O token compartilhado vem de GATEWAY_TOKEN (Secret Manager -> --set-secrets).
Ele nunca e registrado em log nem devolvido em resposta.
"""

from __future__ import annotations

import hmac
import logging
import os
import re
from dataclasses import dataclass
from typing import Any, Callable

from flask import Flask, g, jsonify, request

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

# Header que carrega o token compartilhado. Nome proprio de proposito: o proxy
# do ambiente injeta este header com prefixo vazio. Usar `Authorization` faria
# o front-end do Google tentar interpretar o valor como token OAuth do Google.
AUTH_HEADER = "X-CRM-Token"

# Unico endpoint sem token. Caminhos terminados em "z" sao reservados pelo
# Cloud Run e nunca chegam ao servico -- por isso /health, nao /healthz.
HEALTH_PATH = "/health"

ALLOWED_STATUS = ("Em andamento", "Concluída", "Parcial", "Falhou")

STRUCTURE_FIELDS = "sheets(properties,tables)"
STRUCTURE_FIELDS_FALLBACK = "sheets(properties)"
TABLES_FIELDS = "sheets(properties(title,sheetId),tables(tableId,name,range))"
GRID_FIELDS = "sheets(data(rowData(values(dataValidation,userEnteredValue))))"

MAX_ROW_CELLS = 64
MAX_CELL_CHARS = 2000


@dataclass(frozen=True)
class TabPolicy:
    """O que e permitido numa aba. Default = somente leitura."""

    append: bool = False
    dedupe_column: str | None = None  # coluna do ID estavel, ex. "A"
    status_index: int | None = None  # indice base-0 da coluna Status, se houver


TABS: dict[str, TabPolicy] = {
    "Leads": TabPolicy(append=True, dedupe_column="A"),
    "Acompanhamento": TabPolicy(append=True),
    "Não contatar": TabPolicy(),
    "Execuções": TabPolicy(append=True, dedupe_column="A", status_index=3),
    "Configuração": TabPolicy(),
}

# 'Aba com espaco'!A2:B10  |  Aba!A1  |  'Aba'!A:A
_RANGE_RE = re.compile(
    r"^(?:'(?P<quoted>(?:[^']|'')+)'|(?P<plain>[^'!]+))"
    r"!(?P<ref>\$?[A-Za-z]{1,3}\$?\d*(?::\$?[A-Za-z]{1,3}\$?\d*)?)$"
)


class ApiError(Exception):
    """Erro de politica ou de entrada, devolvido como JSON com status proprio."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


@dataclass(frozen=True)
class Config:
    token: str
    spreadsheet_id: str


def config_from_env() -> Config:
    token = os.environ.get("GATEWAY_TOKEN", "")
    spreadsheet_id = os.environ.get("SPREADSHEET_ID", "")
    missing = [
        name
        for name, value in (("GATEWAY_TOKEN", token), ("SPREADSHEET_ID", spreadsheet_id))
        if not value
    ]
    if missing:
        raise RuntimeError(
            "Variaveis obrigatorias ausentes no servico: " + ", ".join(missing)
        )
    return Config(token=token, spreadsheet_id=spreadsheet_id)


def default_service_factory():
    """Servico da Sheets API autenticado por ADC (metadata server do Cloud Run).

    Import tardio para que os testes possam montar o app sem as bibliotecas
    do Google instaladas.
    """
    import google.auth
    from googleapiclient.discovery import build

    credentials, _ = google.auth.default(scopes=SCOPES)
    return build("sheets", "v4", credentials=credentials, cache_discovery=False)


def parse_range(a1_range: Any) -> str:
    """Valida um intervalo A1 e devolve o nome da aba.

    Recusa intervalo sem aba explicita: sem `!` a API resolveria para a primeira
    aba da planilha, o que escaparia da allowlist.
    """
    if not isinstance(a1_range, str) or not a1_range.strip():
        raise ApiError(400, "campo 'range' ausente ou vazio.")
    match = _RANGE_RE.match(a1_range.strip())
    if not match:
        raise ApiError(
            400,
            f"intervalo {a1_range!r} invalido: use a forma \"'Aba'!A1:B2\", "
            "sempre com a aba explicita.",
        )
    if match.group("quoted") is not None:
        tab = match.group("quoted").replace("''", "'")
    else:
        tab = match.group("plain")
    if tab not in TABS:
        raise ApiError(403, f"aba {tab!r} nao permitida.")
    return tab


def _policy(tab: str) -> TabPolicy:
    policy = TABS.get(tab)
    if policy is None:
        raise ApiError(403, f"aba {tab!r} nao permitida.")
    return policy


def _check_row(row: Any) -> list[Any]:
    if not isinstance(row, list):
        raise ApiError(400, "campo 'row' precisa ser uma lista de celulas.")
    if not row:
        raise ApiError(400, "campo 'row' vazio.")
    if len(row) > MAX_ROW_CELLS:
        raise ApiError(400, f"linha com {len(row)} celulas excede o limite de {MAX_ROW_CELLS}.")
    for i, cell in enumerate(row):
        if cell is None:
            row[i] = ""
            continue
        if isinstance(cell, bool) or isinstance(cell, (int, float)):
            continue
        if isinstance(cell, str):
            if len(cell) > MAX_CELL_CHARS:
                raise ApiError(
                    400, f"celula {i + 1} excede {MAX_CELL_CHARS} caracteres."
                )
            continue
        raise ApiError(
            400,
            f"celula {i + 1} tem tipo nao suportado ({type(cell).__name__}); "
            "use texto, numero ou booleano.",
        )
    return row


def create_app(
    config: Config | None = None,
    service_factory: Callable[[], Any] | None = None,
) -> Flask:
    app = Flask(__name__)
    app.config["CRM"] = config or config_from_env()
    factory = service_factory or default_service_factory
    cached: dict[str, Any] = {}

    def service() -> Any:
        if "svc" not in cached:
            cached["svc"] = factory()
        return cached["svc"]

    def spreadsheet_id() -> str:
        return app.config["CRM"].spreadsheet_id

    # ------------------------------------------------------------------ auth

    @app.before_request
    def _authenticate():
        # Unico caminho isento. Nao usar sufixo "z" (/healthz): o Cloud Run
        # reserva caminhos terminados em "z" e responde um 404 HTML proprio
        # antes de a requisicao chegar ao servico.
        # https://docs.cloud.google.com/run/docs/known-issues#reserved_url_paths
        if request.path == HEALTH_PATH:
            return None
        expected = app.config["CRM"].token
        got = request.headers.get(AUTH_HEADER, "")
        # compare_digest para nao vazar tamanho/prefixo por tempo de resposta.
        if not got or not hmac.compare_digest(got, expected):
            # O valor recebido NUNCA e registrado nem devolvido.
            app.logger.warning("401 em %s: token ausente ou invalido", request.path)
            return jsonify({"error": "token ausente ou invalido."}), 401
        return None

    @app.errorhandler(ApiError)
    def _api_error(exc: ApiError):
        return jsonify({"error": exc.message}), exc.status

    # --------------------------------------------------------------- helpers

    def _values_get(a1_range: str) -> list[list[Any]]:
        resp = (
            service()
            .spreadsheets()
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

    def _header(tab: str) -> list[Any]:
        rows = _values_get(f"'{tab}'!1:1")
        return rows[0] if rows else []

    def _body() -> dict:
        payload = request.get_json(silent=True)
        if payload is None:
            return {}
        if not isinstance(payload, dict):
            raise ApiError(400, "corpo da requisicao precisa ser um objeto JSON.")
        return payload

    # ---------------------------------------------------------------- rotas

    @app.get(HEALTH_PATH)
    def health():
        return jsonify({"status": "ok"})

    @app.post("/v1/config")
    def v1_config():
        """O cliente descobre daqui o ID da planilha e a politica vigente."""
        return jsonify(
            {
                "spreadsheet_id": spreadsheet_id(),
                "tabs": {
                    name: {
                        "append": policy.append,
                        "dedupe_column": policy.dedupe_column,
                    }
                    for name, policy in TABS.items()
                },
                "allowed_status": list(ALLOWED_STATUS),
            }
        )

    @app.post("/v1/structure")
    def v1_structure():
        """Propriedades das abas + tabelas nativas. Sem dados de celula."""
        svc = service().spreadsheets()
        last_exc: Exception | None = None
        for fields in (STRUCTURE_FIELDS, STRUCTURE_FIELDS_FALLBACK):
            try:
                resp = svc.get(spreadsheetId=spreadsheet_id(), fields=fields).execute()
                return jsonify(resp)
            except Exception as exc:  # noqa: BLE001 - fallback para API sem `tables`
                last_exc = exc
        raise ApiError(502, f"falha ao ler a estrutura da planilha: {last_exc}")

    @app.post("/v1/tables")
    def v1_tables():
        """Tabelas nativas por aba. Lista vazia se a API nao expuser o recurso."""
        try:
            resp = (
                service()
                .spreadsheets()
                .get(spreadsheetId=spreadsheet_id(), fields=TABLES_FIELDS)
                .execute()
            )
        except Exception:  # noqa: BLE001
            return jsonify({"tables": []})
        out = []
        for sheet in resp.get("sheets", []):
            title = sheet.get("properties", {}).get("title")
            for table in sheet.get("tables", []):
                out.append(
                    {
                        "title": title,
                        "name": table.get("name"),
                        "tableId": table.get("tableId"),
                        "range": table.get("range"),
                    }
                )
        return jsonify({"tables": out})

    @app.post("/v1/values.get")
    def v1_values_get():
        a1_range = _body().get("range")
        parse_range(a1_range)  # valida a aba contra a allowlist
        return jsonify({"values": _values_get(a1_range.strip())})

    @app.post("/v1/values.grid")
    def v1_values_grid():
        """Grid de um intervalo pequeno, so com dataValidation/valor.

        O conjunto de `fields` e fixo aqui: o cliente nao escolhe o que ler.
        """
        a1_range = _body().get("range")
        parse_range(a1_range)
        resp = (
            service()
            .spreadsheets()
            .get(
                spreadsheetId=spreadsheet_id(),
                ranges=[a1_range.strip()],
                includeGridData=True,
                fields=GRID_FIELDS,
            )
            .execute()
        )
        return jsonify(resp)

    @app.post("/v1/values.append")
    def v1_values_append():
        payload = _body()
        tab = payload.get("tab")
        if not isinstance(tab, str) or not tab:
            raise ApiError(400, "campo 'tab' ausente.")
        policy = _policy(tab)
        if not policy.append:
            raise ApiError(403, f"aba {tab!r} e somente leitura neste gateway.")

        row = _check_row(payload.get("row"))

        header = _header(tab)
        if header and len(row) != len(header):
            raise ApiError(
                400,
                f"a linha tem {len(row)} colunas e o cabecalho de {tab!r} tem "
                f"{len(header)}. Alinhe a linha a ordem exata dos cabecalhos.",
            )

        if policy.status_index is not None and len(row) > policy.status_index:
            status = str(row[policy.status_index])
            if status not in ALLOWED_STATUS:
                raise ApiError(
                    400,
                    f"Status {status!r} invalido em {tab!r}. "
                    f"Aceitos: {' | '.join(ALLOWED_STATUS)}.",
                )

        if policy.dedupe_column:
            new_id = str(row[0]).strip()
            if not new_id:
                raise ApiError(400, f"a coluna de ID de {tab!r} nao pode ficar vazia.")
            col = policy.dedupe_column
            existing = {
                str(r[0]).strip()
                for r in _values_get(f"'{tab}'!{col}2:{col}")
                if r and str(r[0]).strip()
            }
            if new_id in existing:
                raise ApiError(409, f"ID {new_id!r} ja existe em {tab!r}. Nada gravado.")

        resp = (
            service()
            .spreadsheets()
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
        return jsonify(resp)

    return app


if __name__ == "__main__":  # execucao local de conferencia
    logging.basicConfig(level=logging.INFO)
    create_app().run(host="127.0.0.1", port=int(os.environ.get("PORT", 8080)))

"""Cliente do CRM de captacao de leads -- fala com o gateway, nao com o Google.

Autenticacao (sem chave privada em lugar nenhum)
------------------------------------------------
Este processo NAO tem credencial do Google e nao consegue obter uma. Ele chama
o gateway em Cloud Run (gateway/main.py), que roda com a conta de servico como
identidade de execucao e busca o token do Google no metadata server (ADC).

O token compartilhado que autentica esta chamada no gateway viaja no header
`X-CRM-Token` e e injetado pelo **proxy do ambiente de nuvem** (recurso
"API credentials"), depois que a requisicao sai da VM da sessao. Ele nao esta
em variavel de ambiente, nao aparece em log e nao e legivel por nenhum comando
desta sessao -- inclusive por comandos do Claude.

Por isso este modulo NAO define o header em execucao normal: quem o adiciona e
o proxy. `CRM_GATEWAY_TOKEN` existe apenas como escape para testar contra um
gateway rodando em localhost; no ambiente de nuvem ela fica ausente.

Politica de escrita
-------------------
Continua valendo, e agora e imposta pelo servidor: escrita somente por
`values.append` (INSERT_ROWS / RAW), uma linha por vez, so nas abas permitidas,
com o ID da planilha fixado no gateway. `spreadsheets.batchUpdate` nao existe
como operacao alcancavel a partir daqui.

Toda escrita continua sendo conferida depois por `verify_write.py`.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from functools import lru_cache
from typing import Any

AUTH_HEADER = "X-CRM-Token"
TIMEOUT_SECONDS = 60


class GatewayError(RuntimeError):
    """Falha vinda do gateway, com status HTTP e mensagem ja legivel."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


def gateway_url() -> str:
    url = os.environ.get("CRM_GATEWAY_URL", "").strip().rstrip("/")
    if not url:
        raise SystemExit(
            "Variavel CRM_GATEWAY_URL ausente. Defina-a nas variaveis de ambiente "
            "do ambiente de nuvem com a URL exata do servico Cloud Run "
            "(ex.: https://crm-sheets-gateway-000000000000.southamerica-east1.run.app). "
            "Ela nao e segredo -- o segredo e o token, que o proxy injeta."
        )
    if not url.startswith("https://"):
        raise SystemExit(f"CRM_GATEWAY_URL precisa ser https://, recebido: {url!r}")
    return url


def _post(path: str, payload: dict | None = None) -> dict:
    body = json.dumps(payload or {}).encode("utf-8")
    headers = {"Content-Type": "application/json"}

    # Em producao NAO enviamos o token: o proxy do ambiente injeta o header.
    # Esta variavel existe so para rodar contra um gateway local durante testes.
    dev_token = os.environ.get("CRM_GATEWAY_TOKEN")
    if dev_token:
        headers[AUTH_HEADER] = dev_token

    req = urllib.request.Request(
        f"{gateway_url()}{path}", data=body, headers=headers, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
            return json.loads(resp.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            message = json.loads(raw).get("error", raw)
        except json.JSONDecodeError:
            message = raw.strip() or exc.reason
        if exc.code == 401:
            message = (
                "gateway recusou o token (401). Confira se a API credential do "
                "ambiente cobre exatamente o host do gateway e usa o header "
                f"{AUTH_HEADER} com prefixo vazio."
            )
        raise GatewayError(exc.code, message) from None
    except urllib.error.URLError as exc:
        raise GatewayError(0, f"gateway inacessivel: {exc.reason}") from None


@lru_cache(maxsize=1)
def get_config() -> dict:
    """ID da planilha e politica vigente, ditos pelo servidor."""
    return _post("/v1/config")


def spreadsheet_id() -> str:
    return get_config()["spreadsheet_id"]


def get_structure() -> dict:
    """Propriedades das abas + tabelas nativas (sem dados de celula)."""
    return _post("/v1/structure")


def get_tables() -> list[dict]:
    """Tabelas nativas por aba: [{title, name, tableId, range}]. Vazio se a API
    nao expuser o recurso."""
    return _post("/v1/tables").get("tables", [])


def read_range(a1_range: str) -> list[list]:
    return _post("/v1/values.get", {"range": a1_range}).get("values", [])


def get_grid(a1_range: str) -> dict:
    """Grid de um intervalo pequeno para inspecionar dataValidation.

    O conjunto de campos lidos e fixado no gateway -- o cliente nao escolhe.
    """
    return _post("/v1/values.grid", {"range": a1_range})


def append_row(tab: str, row: list) -> dict:
    """Acrescenta UMA linha ao fim da aba.

    O gateway confere antes de gravar: aba permitida para escrita, largura igual
    a do cabecalho, Status dentro da lista fechada e ID ainda inexistente. Um ID
    repetido volta como GatewayError com status 409 e nada e gravado.

    Operacao de escrita menos invasiva disponivel -- confira o resultado depois
    com verify_write.verify().
    """
    return _post("/v1/values.append", {"tab": tab, "row": row})


def describe_auth() -> str:
    """Uma linha para diagnostico. Nunca imprime token."""
    dev = "sim (CRM_GATEWAY_TOKEN definida -- modo de teste local)" if os.environ.get(
        "CRM_GATEWAY_TOKEN"
    ) else "nao (header injetado pelo proxy do ambiente)"
    return f"gateway: {gateway_url()} | token enviado pelo cliente: {dev}"

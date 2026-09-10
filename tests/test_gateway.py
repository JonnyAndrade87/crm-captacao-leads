"""Testes do gateway: politica imposta no servidor, sem rede e sem Google."""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fake_sheets import FakeService  # noqa: E402
from gateway.main import AUTH_HEADER, Config, create_app  # noqa: E402

SHEET_ID = "1hggTeE7kfhuodjM4FG3nO5Sg4N7_psJlBISodiA87p8"
TOKEN = "token-de-teste-nao-usado-em-producao"

EXEC_HEADER = [
    "ID da execução", "Início", "Fim", "Status", "Candidatos analisados",
    "Leads adicionados", "Leads atualizados", "Descartados",
    "Erro / limitação", "Resumo",
]


def exec_row(exec_id: str, status: str = "Concluída") -> list:
    return [exec_id, "2026-09-10 12:00:00", "2026-09-10 12:00:10", status,
            0, 0, 0, 0, "", "Teste de conexão."]


class GatewayTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.data = {
            "Leads": [["ID", "Empresa", "Domínio"], ["LEAD-1", "Alfa", "alfa.com.br"]],
            "Acompanhamento": [["ID", "Etapa"]],
            "Não contatar": [["ID", "Motivo"], ["LEAD-9", "pediu para não receber"]],
            "Execuções": [list(EXEC_HEADER), exec_row("EXEC-EXISTENTE")],
            "Configuração": [["Parâmetro", "Valor"], ["Máximo de leads", 5]],
        }
        self.service = FakeService(SHEET_ID, self.data)
        self.service.tables["Execuções"] = {
            "tableId": "t1", "name": "TabelaExecucoes",
            "range": {"startRowIndex": 0, "endRowIndex": 2,
                      "startColumnIndex": 0, "endColumnIndex": 10},
        }
        self.service.validated_columns["Execuções"] = (3,)
        app = create_app(
            config=Config(token=TOKEN, spreadsheet_id=SHEET_ID),
            service_factory=lambda: self.service,
        )
        app.testing = True
        self.client = app.test_client()

    def post(self, path: str, payload: dict | None = None, token: str | None = TOKEN):
        headers = {AUTH_HEADER: token} if token is not None else {}
        return self.client.post(path, json=payload or {}, headers=headers)

    # ------------------------------------------------------------ auth

    def test_sem_token_recusa(self):
        resp = self.post("/v1/config", token=None)
        self.assertEqual(resp.status_code, 401)

    def test_token_errado_recusa(self):
        resp = self.post("/v1/config", token="errado")
        self.assertEqual(resp.status_code, 401)

    def test_erro_401_nao_vaza_o_token(self):
        resp = self.post("/v1/config", token="errado")
        self.assertNotIn(TOKEN, resp.get_data(as_text=True))

    def test_healthz_dispensa_token(self):
        resp = self.client.get("/healthz")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()["status"], "ok")

    # --------------------------------------------- planilha fixada no servidor

    def test_config_devolve_a_planilha_do_servidor(self):
        body = self.post("/v1/config").get_json()
        self.assertEqual(body["spreadsheet_id"], SHEET_ID)
        self.assertFalse(body["tabs"]["Não contatar"]["append"])
        self.assertFalse(body["tabs"]["Configuração"]["append"])

    def test_cliente_nao_consegue_trocar_a_planilha(self):
        # spreadsheetId no corpo e simplesmente ignorado; o duble falha se o
        # gateway usar qualquer outro ID.
        resp = self.post("/v1/values.get",
                         {"range": "'Leads'!A1:C", "spreadsheetId": "OUTRA-PLANILHA"})
        self.assertEqual(resp.status_code, 200)

    # ------------------------------------------------------ allowlist de abas

    def test_aba_fora_da_allowlist_recusada(self):
        resp = self.post("/v1/values.get", {"range": "'Financeiro'!A1:C"})
        self.assertEqual(resp.status_code, 403)

    def test_intervalo_sem_aba_recusado(self):
        # Sem `!` a API resolveria para a primeira aba, escapando da allowlist.
        resp = self.post("/v1/values.get", {"range": "A1:C10"})
        self.assertEqual(resp.status_code, 400)

    def test_intervalo_malformado_recusado(self):
        for bad in ["", "   ", "'Leads'!", "Leads!A1:C10:D", None, 42]:
            with self.subTest(bad=bad):
                self.assertEqual(self.post("/v1/values.get", {"range": bad}).status_code, 400)

    def test_aba_com_acento_e_espaco_aceita(self):
        resp = self.post("/v1/values.get", {"range": "'Não contatar'!A2:B"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()["values"], [["LEAD-9", "pediu para não receber"]])

    # -------------------------------------------------------- escrita restrita

    def test_append_em_aba_somente_leitura_recusado(self):
        for tab in ("Não contatar", "Configuração"):
            with self.subTest(tab=tab):
                resp = self.post("/v1/values.append", {"tab": tab, "row": ["X", "Y"]})
                self.assertEqual(resp.status_code, 403)

    def test_append_em_aba_inexistente_recusado(self):
        resp = self.post("/v1/values.append", {"tab": "Financeiro", "row": ["X"]})
        self.assertEqual(resp.status_code, 403)

    def test_append_grava_uma_linha_e_devolve_intervalo(self):
        row = exec_row("EXEC-NOVA")
        resp = self.post("/v1/values.append", {"tab": "Execuções", "row": row})
        self.assertEqual(resp.status_code, 200)
        updates = resp.get_json()["updates"]
        self.assertEqual(updates["updatedRows"], 1)
        self.assertEqual(updates["updatedRange"], "Execuções!A3:J3")
        self.assertEqual(self.data["Execuções"][-1], row)

    def test_largura_diferente_do_cabecalho_recusada(self):
        for row in (exec_row("EXEC-CURTA")[:5], exec_row("EXEC-LONGA") + ["extra"]):
            with self.subTest(n=len(row)):
                resp = self.post("/v1/values.append", {"tab": "Execuções", "row": row})
                self.assertEqual(resp.status_code, 400)
                self.assertIn("cabecalho", resp.get_json()["error"])

    def test_status_fora_da_lista_recusado(self):
        resp = self.post("/v1/values.append",
                         {"tab": "Execuções", "row": exec_row("EXEC-X", "Teste de conexão")})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("Status", resp.get_json()["error"])

    def test_todos_os_status_validos_aceitos(self):
        for i, status in enumerate(["Em andamento", "Concluída", "Parcial", "Falhou"]):
            with self.subTest(status=status):
                resp = self.post("/v1/values.append",
                                 {"tab": "Execuções", "row": exec_row(f"EXEC-S{i}", status)})
                self.assertEqual(resp.status_code, 200)

    def test_id_duplicado_recusado_sem_gravar(self):
        antes = len(self.data["Execuções"])
        resp = self.post("/v1/values.append",
                         {"tab": "Execuções", "row": exec_row("EXEC-EXISTENTE")})
        self.assertEqual(resp.status_code, 409)
        self.assertEqual(len(self.data["Execuções"]), antes)

    def test_id_vazio_recusado(self):
        resp = self.post("/v1/values.append", {"tab": "Execuções", "row": exec_row("   ")})
        self.assertEqual(resp.status_code, 400)

    def test_linha_invalida_recusada(self):
        casos = [None, [], "linha", [{"a": 1}] + exec_row("X")[1:], list(range(100))]
        for row in casos:
            with self.subTest(row=type(row).__name__):
                resp = self.post("/v1/values.append", {"tab": "Execuções", "row": row})
                self.assertEqual(resp.status_code, 400)

    def test_batchupdate_nunca_e_chamado(self):
        self.post("/v1/values.append", {"tab": "Execuções", "row": exec_row("EXEC-B")})
        self.post("/v1/structure")
        self.post("/v1/tables")
        self.post("/v1/values.grid", {"range": "Execuções!A3:J3"})
        self.assertTrue(all(kind != "batchUpdate" for kind, _ in self.service.calls))

    # ------------------------------------------ tabelas nativas e dropdowns

    def test_tables_expoe_tabela_nativa(self):
        tables = self.post("/v1/tables").get_json()["tables"]
        self.assertEqual(len(tables), 1)
        self.assertEqual(tables[0]["title"], "Execuções")
        self.assertEqual(tables[0]["name"], "TabelaExecucoes")

    def test_tables_vazio_quando_api_nao_suporta(self):
        self.service.tables_unsupported = True
        self.assertEqual(self.post("/v1/tables").get_json()["tables"], [])

    def test_structure_cai_no_fallback_sem_tables(self):
        self.service.tables_unsupported = True
        resp = self.post("/v1/structure")
        self.assertEqual(resp.status_code, 200)
        titles = [s["properties"]["title"] for s in resp.get_json()["sheets"]]
        self.assertIn("Execuções", titles)

    def test_grid_devolve_dropdown_da_linha(self):
        self.post("/v1/values.append", {"tab": "Execuções", "row": exec_row("EXEC-G")})
        cells = (self.post("/v1/values.grid", {"range": "Execuções!A3:J3"})
                 .get_json()["sheets"][0]["data"][0]["rowData"][0]["values"])
        self.assertEqual(cells[3]["dataValidation"]["condition"]["type"], "ONE_OF_LIST")

    def test_grid_respeita_allowlist(self):
        self.assertEqual(
            self.post("/v1/values.grid", {"range": "'Financeiro'!A1:B2"}).status_code, 403
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)

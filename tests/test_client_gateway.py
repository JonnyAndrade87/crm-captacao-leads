"""Ponta a ponta: sheets_client + verify_write falando HTTP com o gateway real.

O unico duble e a Google Sheets API. O cliente, o servidor HTTP, a serializacao
e a verificacao pos-escrita sao os de verdade.
"""

from __future__ import annotations

import importlib
import os
import sys
import threading
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from werkzeug.serving import make_server  # noqa: E402

from fake_sheets import FakeService  # noqa: E402
from gateway.main import Config, create_app  # noqa: E402
from test_gateway import EXEC_HEADER, SHEET_ID, TOKEN, exec_row  # noqa: E402


class ServidorLocal:
    """Sobe o gateway num porta livre de localhost durante o teste."""

    def __init__(self, app):
        self._server = make_server("127.0.0.1", 0, app, threaded=True)
        self.url = f"http://127.0.0.1:{self._server.server_port}"
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._server.shutdown()
        self._thread.join(timeout=5)


class ClienteGatewayTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.data = {
            "Leads": [["ID", "Empresa", "Domínio"]],
            "Acompanhamento": [["ID", "Etapa"]],
            "Não contatar": [["ID", "Motivo"], ["LEAD-9", "pediu para não receber"]],
            "Execuções": [list(EXEC_HEADER), exec_row("EXEC-EXISTENTE")],
            "Configuração": [["Parâmetro", "Valor"], ["Máximo de leads", 5]],
        }
        self.service = FakeService(SHEET_ID, self.data)
        self.service.tables["Execuções"] = {
            "tableId": "t1", "name": "TabelaExecucoes",
            "range": {"startRowIndex": 0, "endRowIndex": 3,
                      "startColumnIndex": 0, "endColumnIndex": 10},
        }
        self.service.validated_columns["Execuções"] = (3,)

        app = create_app(
            config=Config(token=TOKEN, spreadsheet_id=SHEET_ID),
            service_factory=lambda: self.service,
        )
        self.server = ServidorLocal(app)
        self.server.__enter__()
        self.addCleanup(self.server.__exit__, None, None, None)

        # O cliente aponta para o servidor local. CRM_GATEWAY_TOKEN so existe
        # aqui: na nuvem o header e injetado pelo proxy do ambiente.
        os.environ["CRM_GATEWAY_URL"] = self.server.url
        os.environ["CRM_GATEWAY_TOKEN"] = TOKEN
        self.addCleanup(os.environ.pop, "CRM_GATEWAY_URL", None)
        self.addCleanup(os.environ.pop, "CRM_GATEWAY_TOKEN", None)

        import sheets_client
        importlib.reload(sheets_client)
        self.sc = sheets_client
        # https e exigido em producao; localhost e a excecao usada no teste.
        self.sc.gateway_url = lambda: self.server.url

    # -------------------------------------------------------------- leitura

    def test_spreadsheet_id_vem_do_servidor(self):
        self.assertEqual(self.sc.spreadsheet_id(), SHEET_ID)

    def test_read_range(self):
        self.assertEqual(
            self.sc.read_range("'Não contatar'!A2:B"),
            [["LEAD-9", "pediu para não receber"]],
        )

    def test_get_tables(self):
        tables = self.sc.get_tables()
        self.assertEqual([t["title"] for t in tables], ["Execuções"])

    def test_get_structure(self):
        titles = [s["properties"]["title"] for s in self.sc.get_structure()["sheets"]]
        self.assertIn("Configuração", titles)

    def test_leitura_de_aba_proibida_vira_erro_legivel(self):
        with self.assertRaises(self.sc.GatewayError) as ctx:
            self.sc.read_range("'Financeiro'!A1:B2")
        self.assertEqual(ctx.exception.status, 403)

    # -------------------------------------------------------------- escrita

    def test_append_e_verificacao_pos_escrita_passam(self):
        import verify_write
        importlib.reload(verify_write)

        row = exec_row("EXEC-NOVA")
        resp = self.sc.append_row("Execuções", row)
        updated = resp["updates"]["updatedRange"]
        self.assertEqual(updated, "Execuções!A3:J3")
        self.assertEqual(resp["updates"]["updatedRows"], 1)

        # verify_write le de volta, confere a tabela nativa e os dropdowns.
        self.assertTrue(verify_write.verify(updated, row))

    def test_verificacao_acusa_quando_tabela_nativa_nao_se_estende(self):
        import verify_write
        importlib.reload(verify_write)

        # Tabela nativa que cobre so ate a linha 2: a linha 3 fica de fora.
        self.service.tables["Execuções"]["range"]["endRowIndex"] = 2
        row = exec_row("EXEC-FORA")
        updated = self.sc.append_row("Execuções", row)["updates"]["updatedRange"]
        self.assertFalse(verify_write.verify(updated, row))

    def test_id_duplicado_vira_409(self):
        with self.assertRaises(self.sc.GatewayError) as ctx:
            self.sc.append_row("Execuções", exec_row("EXEC-EXISTENTE"))
        self.assertEqual(ctx.exception.status, 409)
        self.assertEqual(len(self.data["Execuções"]), 2)

    def test_escrita_em_aba_somente_leitura_vira_403(self):
        with self.assertRaises(self.sc.GatewayError) as ctx:
            self.sc.append_row("Não contatar", ["LEAD-X", "motivo"])
        self.assertEqual(ctx.exception.status, 403)

    def test_token_ausente_vira_401_com_dica(self):
        os.environ.pop("CRM_GATEWAY_TOKEN")
        with self.assertRaises(self.sc.GatewayError) as ctx:
            self.sc.read_range("'Leads'!A1:C")
        self.assertEqual(ctx.exception.status, 401)
        self.assertIn("X-CRM-Token", ctx.exception.message)

    # ------------------------------------------------------------ diagnostico

    def test_describe_auth_nao_imprime_token(self):
        self.assertNotIn(TOKEN, self.sc.describe_auth())

    def test_url_ausente_falha_com_instrucao(self):
        os.environ.pop("CRM_GATEWAY_URL")
        import sheets_client
        importlib.reload(sheets_client)
        with self.assertRaises(SystemExit) as ctx:
            sheets_client.read_range("'Leads'!A1:C")
        self.assertIn("CRM_GATEWAY_URL", str(ctx.exception))


if __name__ == "__main__":
    unittest.main(verbosity=2)

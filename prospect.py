#!/usr/bin/env python3
"""Rotina diaria de prospeccao -- AINDA NAO ATIVADA.

Este arquivo e um esqueleto inerte. Executa-lo hoje NAO grava nada: apenas
chama a inspecao da base. A prospeccao diaria so sera implementada apos o
teste de conexao ser validado e o usuario autorizar explicitamente.

Quando ativada, devera:
  1. Ler a aba Configuracao (publico, regiao, oferta, ticket minimo).
  2. Pesquisar ate 5 leads aderentes; fonte (URL + data) para cada sinal.
  3. Deduplicar por ID estavel + dominio normalizado; consultar 'Nao contatar'.
  4. Acrescentar linhas em Leads (nunca sobrescrever; preservar notas do Jonny).
  5. Registrar o resultado em Execucoes e ler de volta.

Envio de mensagens NAO faz parte desta rotina.
"""

import sys

from inspect_base import main as inspect_main

if __name__ == "__main__":
    print("Prospeccao diaria ainda NAO ativada. Rodando apenas inspecao da base.\n")
    inspect_main()
    sys.exit(0)

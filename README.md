# crm-captacao-leads

Automacao de captacao de leads que roda como **Claude Code cloud Routine** e
grava numa Google Sheets existente **atraves de um gateway proprio no Cloud
Run**. A sessao do Claude nunca tem credencial do Google.

## Onde roda
Ambiente de nuvem do Claude Code (claude.ai/code) vinculado a este repositorio
GitHub privado. As Routines executam nesse ambiente conforme agendamento, com o
Mac desligado. VM Ubuntu efemera, Python pre-instalado.

## Autenticacao -- sem chave privada em lugar nenhum

```
sessao Claude ──X-CRM-Token (injetado pelo proxy)──▶ Cloud Run (gateway)
gateway ──ADC da conta de servico anexada──▶ sheets.googleapis.com
```

### Por que nao da para autenticar direto
O recurso **API credentials** do ambiente de nuvem injeta um **header estatico**
nas requisicoes, depois que elas saem da VM -- a chave nunca chega ao Claude nem
as variaveis de ambiente. Mas a Google Sheets API so aceita
`Authorization: Bearer <access_token>` OAuth2 de **vida curta**, obtido assinando
um JWT com a chave privada da conta de servico. Nenhum header fixo autentica
`sheets.googleapis.com`; chave de API so serve para dados publicos e nunca para
escrita. Por isso existe o gateway.

### O que isso resolve
1. **Nenhuma chave JSON e gerada.** No Cloud Run a conta de servico e a
   identidade de execucao do servico e o token vem do metadata server (ADC).
2. **O token compartilhado fica no Google Secret Manager**, entregue ao servico
   por `--set-secrets`. Na ponta do Claude ele existe so como API credential --
   fora das variaveis de ambiente, ilegivel por qualquer comando da sessao.
3. **A politica passa a ser imposta pelo servidor**, nao pela disciplina do
   cliente: ID da planilha fixo, allowlist de abas, escrita so por append de uma
   linha, largura conferida contra o cabecalho, Status validado, dedup por ID.
   `batchUpdate` nao e alcancavel a partir da sessao.

O alerta da UI ("anyone who uses the environment can read the values") continua
valendo para variaveis de ambiente -- por isso nenhum segredo mora la. A unica
variavel usada e `CRM_GATEWAY_URL`, que e apenas um endereco.

### Reducao de alcance
1. Projeto Google Cloud dedicado e conta de servico usada so nisto.
2. Escopo unico: `https://www.googleapis.com/auth/spreadsheets`.
3. Planilha compartilhada **apenas** com o e-mail da conta de servico (Editor).
4. API credential restrita ao **host exato** do servico Cloud Run -- nunca
   `*.run.app`, que entregaria o token a qualquer servico Cloud Run.
5. Token rotacionavel a qualquer momento (nova versao no Secret Manager,
   redeploy, recadastro da credential).
6. `.gitignore` bloqueia `*.json` / `.env`.

## Deploy
Ainda **nao executado**. Runbook completo, incluindo os requisitos de
faturamento, em [`docs/deploy-gateway.md`](docs/deploy-gateway.md).

Resumo do faturamento: Cloud Run, Artifact Registry, Cloud Build e Secret
Manager **exigem uma conta de faturamento vinculada ao projeto**, mas o uso
previsto (uma execucao diaria) cabe com folga nos free tiers -- fatura esperada
de **R$ 0/mes**.

## Preparacao do ambiente
O campo de **setup do ambiente de nuvem fica vazio de proposito**. Quem prepara
o Python e o hook `SessionStart` do repositorio:

- `.claude/hooks/session-start.sh` -- roda **so na nuvem** (`CLAUDE_CODE_REMOTE`),
  localiza o repo por `CLAUDE_PROJECT_DIR`, cria `.venv` isolado
  (**sem** `--system-site-packages`), instala `requirements.txt` e roda `pip check`.
  `pip check` falhando aborta o hook com status != 0.
- Registrado em `.claude/settings.json`. E idempotente.

Por que o `.venv` isolado importa: a imagem da VM traz um `cryptography 41.0.7`
do Debian **quebrado** (sem `_cffi_backend`). O `google-auth` importa
`cryptography` ao carregar `service_account`, e o panico do binding Rust nao e
capturado pelo `try/except ImportError`. O `.venv` nao enxerga `dist-packages`,
entao a versao do PyPI prevalece.

Localmente, o mesmo passo a mao:
```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

## Testes
Nenhum toca a rede, o Google ou a planilha -- a Sheets API e substituida por um
duble que tambem **falha se alguem chamar `batchUpdate`**.

```bash
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m unittest discover -s tests -v
```

- `tests/test_gateway.py` -- politica do servidor (auth, allowlist, largura do
  cabecalho, Status, dedup, tabelas nativas, dropdowns).
- `tests/test_client_gateway.py` -- ponta a ponta: `sheets_client` e
  `verify_write` reais falando HTTP com o gateway real.

## Uso manual
Sempre pelo interpretador do `.venv`. Exige `CRM_GATEWAY_URL` definida e a API
credential cadastrada.

```bash
.venv/bin/python inspect_base.py                      # cabecalhos, limites, tabelas, IDs
.venv/bin/python append_execucao.py \
    --id "TESTE-CONEXAO-AAAAMMDD-HHMM" \
    --status "Concluída" \
    --resumo "Teste de conexão da rotina na nuvem; nenhuma prospecção executada."
.venv/bin/python read_execucoes.py --id "TESTE-CONEXAO-AAAAMMDD-HHMM"   # readback
```
`append_execucao.py` ja roda a verificacao pos-escrita (`verify_write.py`):
intervalo gravado, releitura, cobertura de tabela nativa e dropdowns.

## Sobre preservacao
Usa a escrita menos invasiva (`values.append` + `INSERT_ROWS`, nunca
`batchUpdate`). Isso **nao garante** por si so que a tabela nativa se estenda ou
que os dropdowns sejam herdados -- por isso a verificacao roda sempre e reporta
`PASS` / `ATENCAO` sem corrigir nada automaticamente.

## Ativacao futura (nao agora)
`prospect.py` esta inerte. A Routine diaria so sera criada apos o deploy, o teste
de conexao validado e autorizacao explicita.

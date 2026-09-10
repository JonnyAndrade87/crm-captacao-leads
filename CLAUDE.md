# CRM - Captacao de Leads (Studio Elephill)

Rotina de prospeccao executada como **Claude Code cloud Routine** (roda na nuvem,
independente do Mac). Grava numa Google Sheets existente via **Google Sheets API**
com **conta de servico**.

## Planilha (fonte de verdade)
- ID: `1hggTeE7kfhuodjM4FG3nO5Sg4N7_psJlBISodiA87p8`
- Abas: `Leads`, `Acompanhamento`, `Não contatar`, `Execuções`, `Configuração`
- Ler a aba **Configuração** no inicio de cada execucao e obedecer os parametros.

## Regras de escrita
- **Nunca** `spreadsheets.batchUpdate`. Somente `values.append`
  (`insertDataOption=INSERT_ROWS`, `valueInputOption=RAW`) e leitura
  (`values.get`, `spreadsheets.get`).
- `values.append` e a escrita menos invasiva disponivel, mas **nao garante**
  que uma tabela nativa do Sheets se estenda sozinha nem que o dropdown de uma
  coluna seja herdado pela linha nova.
- **Toda escrita e conferida depois** por `verify_write.py`: intervalo gravado,
  releitura dos valores, cobertura da tabela nativa e validacoes/dropdowns na
  linha. Em "ATENCAO", registrar o que falhou e nao declarar sucesso total.
- Antes de gravar: `inspect_base.py` (limites das abas + IDs existentes).
  Abortar se o ID ja existir. Alinhar a linha a ordem exata dos cabecalhos.

### Aba Execuções
- Colunas: `ID da execução | Início | Fim | Status | Candidatos analisados |
  Leads adicionados | Leads atualizados | Descartados | Erro / limitação | Resumo`.
- **Status aceita apenas**: `Em andamento`, `Concluída`, `Parcial`, `Falhou`.
- Teste de conexao bem-sucedido -> Status = `Concluída`. O rotulo
  "Teste de conexão" vai no **ID** e no **Resumo**, nunca no Status.

## Regras de conteúdo (da aba Configuração)
- Maximo 5 novos leads por execucao; aceitar zero quando nao houver aderentes.
- Deduplicar por ID estavel + dominio normalizado; separar filiais so quando justificavel.
- Consultar **Não contatar** antes de incluir ou sugerir abordagem.
- Toda evidencia precisa de fonte: URL + data. Nunca inventar telefone, e-mail,
  responsavel, investimento ou resultado.
- Fatos (sinal, problema) e hipoteses (oportunidade) ficam separados.
- Preservar as atualizacoes comerciais do Jonny; nao inferir resposta, reuniao,
  proposta ou venda a partir de pesquisa web.
- Rascunho de abordagem individual. **Envio de mensagens esta fora do escopo.**
- Apos gravar: ler de volta e registrar o resultado em **Execuções**.

## Ambiente Python (obrigatorio)
- **Todo comando do CRM roda por `.venv/bin/python`**, nunca pelo `python` do
  sistema. O Python do sistema traz um `cryptography` do Debian quebrado
  (sem `_cffi_backend`) que derruba o `google-auth` no import do
  `service_account`.
- O `.venv` e criado **sem** `--system-site-packages`, entao nao enxerga
  `dist-packages` e a versao do PyPI prevalece. Nunca "consertar" o Python do
  sistema com `pip install --ignore-installed`.
- Quem monta o `.venv` e o hook `SessionStart`
  (`.claude/hooks/session-start.sh`, registrado em `.claude/settings.json`):
  roda so na nuvem (`CLAUDE_CODE_REMOTE`), acha o repo por `CLAUDE_PROJECT_DIR`,
  instala `requirements.txt` e roda `pip check`. E idempotente.
- O campo de **setup do ambiente de nuvem fica vazio** de proposito.
- `cryptography`/`cffi` nao vao no `requirements.txt`: chegam como dependencia
  transitiva de `google-auth`.
- Se o `.venv` nao existir (hook ainda nao no branch padrao): `bash setup.sh`.

## Scripts
| Arquivo | Funcao |
|---|---|
| `.claude/hooks/session-start.sh` | Cria o `.venv` e instala deps na abertura da sessao (so na nuvem) |
| `sheets_client.py` | Auth (conta de servico via env) + leitura + append |
| `inspect_base.py` | Cabecalhos, limites, tabelas nativas e IDs existentes |
| `append_execucao.py` | Acrescenta 1 linha em `Execuções` e verifica |
| `verify_write.py` | Confere intervalo gravado, tabela nativa e dropdowns |
| `read_execucoes.py` | Leitura de volta da aba `Execuções` |
| `prospect.py` | Rotina diaria -- INERTE ate ativacao explicita |

## Credenciais e alcance (importante)
- `GOOGLE_SERVICE_ACCOUNT_JSON` e uma **variavel de ambiente** do ambiente de
  nuvem. Pela doc da Anthropic, ela e **legivel por qualquer comando da sessao
  (inclusive comandos do Claude)** e por quem usar o ambiente. **Nao e um
  segredo oculto do Claude.** O recurso "API credentials" (proxy) nao serve
  aqui, porque a conta de servico assina um JWT localmente com a chave privada.
- Reducao de alcance: projeto Google Cloud dedicado; conta de servico usada
  so nisto; escopo unico `spreadsheets`; planilha compartilhada **so** com o
  e-mail da conta de servico (Editor) -- ela nao acessa mais nada do Drive nem
  a conta Google; chave revogavel/rotacionavel a qualquer momento no Google Cloud.
- Nunca versionar a chave. `.gitignore` bloqueia `*.json` e `.env`.

## Estado atual
Conexao em validacao. Prospeccao diaria **nao** agendada. Credenciais nao
configuradas (`GOOGLE_SERVICE_ACCOUNT_JSON` ausente no ambiente).

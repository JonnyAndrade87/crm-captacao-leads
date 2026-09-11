# CRM - Captacao de Leads (Studio Elephill)

Rotina de prospeccao executada como **Claude Code cloud Routine** (roda na nuvem,
independente do Mac). Grava numa Google Sheets existente **atraves de um gateway
proprio no Cloud Run** -- a sessao nunca fala direto com a Google API e nunca tem
credencial do Google.

## Planilha (fonte de verdade)
- ID: `1hggTeE7kfhuodjM4FG3nO5Sg4N7_psJlBISodiA87p8`
- Abas: `Leads`, `Acompanhamento`, `Não contatar`, `Execuções`, `Configuração`
- Ler a aba **Configuração** no inicio de cada execucao e obedecer os parametros.
- O ID vive no **gateway** (env `SPREADSHEET_ID` do servico). O cliente pergunta
  qual e (`/v1/config`) -- nao pode escolher outra planilha.

## Regras de escrita
- **Nunca** `spreadsheets.batchUpdate`. Isso agora e estrutural: o gateway nao
  expoe essa operacao, entao nao ha como alcanca-la a partir da sessao.
- Escrita somente por `values.append` (`insertDataOption=INSERT_ROWS`,
  `valueInputOption=RAW`), **uma linha por chamada**, e so nas abas com
  `append=True`. `Não contatar` e `Configuração` sao somente leitura.
- O gateway recusa **antes de gravar**: aba fora da allowlist, largura diferente
  da do cabecalho, Status fora da lista fechada e ID ja existente (409).
- `values.append` e a escrita menos invasiva disponivel, mas **nao garante** que
  uma tabela nativa do Sheets se estenda sozinha nem que o dropdown de uma
  coluna seja herdado pela linha nova.
- **Toda escrita e conferida depois** por `verify_write.py`: intervalo gravado,
  releitura dos valores, cobertura da tabela nativa e validacoes/dropdowns na
  linha. Em "ATENCAO", registrar o que falhou e nao declarar sucesso total.
- `values.append` grava **logo apos o fim do range da tabela nativa**, nao apos a
  ultima celula preenchida. Tabela pre-dimensionada com linhas vazias = registro
  novo la embaixo. As tabelas ja foram encolhidas ate o conteudo real -- ver
  "Estado atual".

### Dropdown por coluna (verificado em 11/09/2026)
O dropdown de Status **nao** e validacao por celula: nenhuma celula da coluna D
tem `dataValidation`, nem o cabecalho, nem a linha gravada. A regra vive nas
**propriedades de coluna da tabela nativa** -- `columnType: DROPDOWN` mais
`dataValidationRule` com a lista fechada -- e vale para toda linha dentro do
range da tabela.

Duas consequencias:
1. Linha que cai **dentro** do range da tabela herda o dropdown automaticamente.
   Foi o que aconteceu com o registro de teste.
2. O item 4 do `verify_write.py` le `dataValidation` por celula e por isso imprime
   "nenhuma validacao/dropdown detectada" **mesmo quando o dropdown esta certo**.
   Nessa aba, o sinal confiavel e o item 3 (cobertura da tabela nativa), nao o 4.
   Nao tratar esse INFO como falha.
- Antes de gravar: `inspect_base.py` (limites das abas + IDs existentes).

### Aba Execuções
- Colunas: `ID da execução | Início | Fim | Status | Candidatos analisados |
  Leads adicionados | Leads atualizados | Descartados | Erro / limitação | Resumo`.
- **Status aceita apenas**: `Em andamento`, `Concluída`, `Parcial`, `Falhou`.
  O gateway valida a coluna 4 contra essa lista.
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
  `service_account` -- ainda usado pelos testes do gateway.
- O `.venv` e criado **sem** `--system-site-packages`, entao nao enxerga
  `dist-packages` e a versao do PyPI prevalece. Nunca "consertar" o Python do
  sistema com `pip install --ignore-installed`.
- Quem monta o `.venv` e o hook `SessionStart`
  (`.claude/hooks/session-start.sh`, registrado em `.claude/settings.json`):
  roda so na nuvem (`CLAUDE_CODE_REMOTE`), acha o repo por `CLAUDE_PROJECT_DIR`,
  instala `requirements.txt` e roda `pip check`. Se o `pip check` acusar
  inconsistencia, o hook aborta com status != 0 em vez de declarar sucesso.
  E idempotente.
- O campo de **setup do ambiente de nuvem fica vazio** de proposito.
- `requirements-dev.txt` (Flask) so e preciso para rodar os testes:
  `.venv/bin/python -m pip install -r requirements-dev.txt`.
- Se o `.venv` nao existir: `bash setup.sh`.

## Scripts
| Arquivo | Funcao |
|---|---|
| `.claude/hooks/session-start.sh` | Cria o `.venv` e instala deps na abertura da sessao (so na nuvem) |
| `gateway/main.py` | **Servico Cloud Run**: unica coisa que fala com a Google API; impoe a politica |
| `gateway/Dockerfile` | Imagem do gateway (sem credencial dentro) |
| `sheets_client.py` | Cliente HTTP do gateway (sem credencial do Google) |
| `inspect_base.py` | Cabecalhos, limites, tabelas nativas e IDs existentes |
| `append_execucao.py` | Acrescenta 1 linha em `Execuções` e verifica |
| `verify_write.py` | Confere intervalo gravado, tabela nativa e dropdowns |
| `web_audit.py` | Verifica site em navegador renderizado (desktop + celular); separa ACHADO de INCONCLUSIVO |
| `read_execucoes.py` | Leitura de volta da aba `Execuções` |
| `prospect.py` | Rotina diaria -- INERTE ate ativacao explicita |
| `tests/` | 65 testes (`unittest`), sem rede, sem Google e sem navegador |
| `docs/deploy-gateway.md` | Runbook de deploy, faturamento e API credential |

Rodar os testes: `.venv/bin/python -m unittest discover -s tests -v`

## Verificacao de site (`web_audit.py`)

Conferir site por HTML bruto gera falso positivo em serie. Ja aconteceram, todos
descartados depois de checar: lazy-load lido como imagem quebrada, `406` de WAF
lido como site fora do ar, timeout isolado lido como lentidao cronica, e
subdominio de plataforma lido como ausencia de dominio proprio.

Por isso a verificacao roda em Chromium, em duas viewports (1366x900 e 390x844),
e o resultado sai em duas pilhas separadas: **ACHADO** e **INCONCLUSIVO**.

- **Nenhum caminho de binario fica embutido.** Quem resolve o navegador e o
  proprio Playwright, lendo `PLAYWRIGHT_BROWSERS_PATH`.
- **A versao do pacote precisa casar com o navegador da imagem.** A imagem de
  nuvem traz o Chromium revisao **1194** (141.0.7390.37), que e o que o pacote
  **1.56.x** espera. Pacote mais novo procura outra revisao e o launch falha com
  "Executable doesn't exist". Ao subir de versao, confira a revisao em
  `playwright/driver/package/browsers.json` antes.
- **Rede:** o Chromium nao alcanca o proxy do ambiente sozinho. O modulo entao
  serve as requisicoes da pagina por `requests`, que le `HTTPS_PROXY` e o bundle
  de CA do ambiente. Isso **nao contorna restricao** -- e o mesmo proxy
  sancionado, por um cliente que sabe falar com ele. Verificacao de TLS fica
  ligada em todo ponto.
- **Certificado do alvo nao e avaliado**: o TLS e reterminado no proxy, entao o
  certificado visto seria o do proxy.
- **Autoteste obrigatorio:** antes de confiar em qualquer resultado, o modulo
  audita uma URL de controle. Controle falho = rodada INCONCLUSIVA, porque o
  problema esta na ferramenta e nao nos sites.
- Se uma viewport levar bloqueio e a outra passar, os achados da pagina saem
  marcados com `CONFIRMAR` -- o que passou pode ter passado degradado.

Regra que vale junto: ano de copyright antigo, plataforma usada, subdominio de
plataforma, contato so por formulario e registro de MX **nao justificam projeto
por si sos**.

## Credenciais e alcance (importante)
- **Nao existe chave privada JSON em lugar nenhum.** No Cloud Run a conta de
  servico e a *identidade de execucao* do servico; o token do Google vem do
  metadata server via `google.auth.default()` (ADC).
- O que autentica a sessao no gateway e um **token compartilhado** guardado no
  **Google Secret Manager**, entregue ao servico por `--set-secrets`. Ele viaja
  no header `X-CRM-Token`, injetado pelo **proxy do ambiente de nuvem**
  (recurso "API credentials") depois que a requisicao sai da VM.
  **O token nao esta em variavel de ambiente e nao e legivel por nenhum comando
  da sessao -- inclusive comandos do Claude.**
- `sheets_client.py` **nao envia** esse header em operacao normal; quem adiciona
  e o proxy. `CRM_GATEWAY_TOKEN` existe so como escape para testar contra um
  gateway em localhost e fica ausente na nuvem.
- A API credential precisa apontar para o **host exato** do servico Cloud Run.
  **Nunca `*.run.app`** -- o curinga entregaria o token a qualquer servico Cloud
  Run que a sessao resolvesse chamar.
- `CRM_GATEWAY_URL` (a URL do servico) fica em variavel de ambiente comum. Nao e
  segredo: e so um endereco; o segredo e o token.
- Reducao de alcance: projeto Google Cloud dedicado; conta de servico usada so
  nisto; escopo unico `spreadsheets`; planilha compartilhada **so** com o e-mail
  da conta de servico (Editor); o gateway so expoe leitura e append de uma linha
  nas abas permitidas de uma unica planilha.
- Nunca versionar segredo. `.gitignore` bloqueia `*.json` e `.env`.

## Estado atual
Gateway **publicado, autenticado e validado de ponta a ponta** (11/09/2026).

- Deploy no Cloud Run feito. Host:
  `https://crm-sheets-gateway-888305689319.southamerica-east1.run.app`
- API credential cadastrada no host exato (nao `*.run.app`). O proxy injeta o
  `X-CRM-Token`; sem ele o gateway devolveria 401, entao o `200` das chamadas ja
  prova que a credencial esta ativa.
- `/health` -> `200 {"status":"ok"}`.
- `/v1/config` -> `200`, com `spreadsheet_id` batendo com o desta pagina e a
  allowlist de abas como esperado. **Todas as rotas `/v1/*` sao POST**; um `GET`
  devolve `405` -- isso e a rota funcionando, nao falha do servico.
- Teste de escrita gravado e conferido: `TESTE-CONEXAO-20260911-101325`,
  Status `Concluída`, contadores zerados. Readback confere.

Prospeccao diaria continua **nao** agendada e `prospect.py` continua inerte.

Primeiros leads reais gravados e conferidos em 11/09/2026:
`LEAD-HOLYSANDWICHSHOP-PT` (Porto) e `LEAD-PAOBRASILBAKERY-COM` (Framingham, MA),
nas linhas 3 e 4. Nenhum marcado como contatado -- `Acompanhamento` segue so com
cabecalho e as abordagens ficaram como rascunho nao enviado.

### Linhas reservadas em branco -- RESOLVIDO (11/09/2026)
As tres tabelas nasceram pre-dimensionadas com 500 linhas reservadas, e como
`values.append` grava **depois do fim do range da tabela** (nao depois da ultima
celula preenchida), o registro de teste tinha caido na linha 502.

O Jonny apagou as linhas reservadas a mao na UI do Sheets -- o gateway nao expoe
`batchUpdate` de proposito. Estado conferido depois, so leitura:

| Aba | Tabela | Range | Linha de dados |
|---|---|---|---|
| `Leads` | `CRM_Leads` | `A1:W2` | 1, vazia |
| `Acompanhamento` | `CRM_Acompanhamento` | `A1:L2` | 1, vazia |
| `Execuções` | `CRM_Execucoes` | `A1:J2` | 1, com `TESTE-CONEXAO-20260911-101325` |

Cabecalhos intactos (23, 12 e 10 colunas, ordem original) e dropdowns de coluna
preservados: `Encaixe no perfil` em `Leads`, `Etapa` e `Canal` em
`Acompanhamento`, `Status` em `Execuções`. Redimensionar a tabela nao derruba a
regra, justamente porque ela e propriedade de coluna e nao das celulas.

**RESPONDIDO pelo primeiro append real (11/09/2026):** o append **nao**
aproveita a linha reservada vazia. Gravou em `Leads!A3:W3`, estendendo a tabela,
e a **linha 2 ficou em branco**. Confirma a regra: `values.append` grava depois
do fim do range da tabela, nao depois da ultima celula preenchida.

Consequencia pratica: `Leads` tem hoje uma linha 2 vazia entre o cabecalho e o
primeiro lead, removivel so a mao na UI do Sheets. `Acompanhamento` ainda tem a
linha reservada e vai se comportar igual no primeiro registro dela.

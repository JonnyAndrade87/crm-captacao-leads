# Deploy do gateway (Cloud Run)

Runbook do serviço que fica entre a sessão do Claude e a Google Sheets API.
**Nada aqui foi executado ainda.** O deploy exige a conta Google do Jonny.

## Por que existe

A Sheets API só aceita `Authorization: Bearer <access_token>` OAuth2 de vida
curta, assinado localmente com a chave privada da conta de serviço. O recurso
**API credentials** do ambiente de nuvem injeta um **header estático** — ele não
consegue autenticar `sheets.googleapis.com` direto.

O gateway resolve os dois lados:

```
sessão Claude ──X-CRM-Token (injetado pelo proxy)──▶ Cloud Run
Cloud Run ──ADC da conta de serviço anexada──▶ sheets.googleapis.com
```

**Nenhuma chave JSON é gerada em momento algum.** No Cloud Run a conta de
serviço é a identidade de execução e o token vem do metadata server.

## Requisitos de faturamento (confira ANTES de tentar o deploy)

| Serviço | Precisa de billing? | Free tier mensal | Custo esperado aqui |
|---|---|---|---|
| Cloud Run | **Sim** | 2 mi de requisições, 180 mil vCPU-s, 360 mil GiB-s | R$ 0 |
| Artifact Registry | **Sim** | 0,5 GB de armazenamento | R$ 0 (imagem ~150 MB) |
| Cloud Build | **Sim** | 2.500 minutos de build | R$ 0 (build ~2 min) |
| Secret Manager | **Sim** | 6 versões ativas + 10 mil acessos | R$ 0 (1 segredo, 1 acesso/dia) |
| Google Sheets API | Não | — | R$ 0 |

**Uma conta de faturamento válida precisa estar vinculada ao projeto.** Sem
isso, `gcloud run deploy` falha com `Billing account for project ... is not
found` — não é possível usar Cloud Run só no free tier sem billing vinculado.
O consumo previsto (uma execução diária) fica com folga dentro das cotas
gratuitas, então a fatura esperada é **R$ 0/mês**, mas o vínculo é obrigatório.

Recomendado: definir um **orçamento com alerta** em Faturamento → Orçamentos e
alertas (ex.: alerta em R$ 5) para não haver surpresa.

## Variáveis do runbook

```bash
PROJECT_ID="crm-captacao-leads"                  # projeto dedicado já existente
REGION="southamerica-east1"                      # São Paulo
SERVICE="crm-sheets-gateway"
SA_EMAIL="<conta-de-servico>@${PROJECT_ID}.iam.gserviceaccount.com"
SHEET_ID="1hggTeE7kfhuodjM4FG3nO5Sg4N7_psJlBISodiA87p8"
```

## Passo 1 — Faturamento e APIs (exige a conta do Jonny)

```bash
gcloud config set project "$PROJECT_ID"

# Confere se já há billing vinculado.
gcloud beta billing projects describe "$PROJECT_ID" \
  --format='value(billingEnabled)'          # precisa devolver True

# Se devolver False: vincule a conta de faturamento.
gcloud beta billing accounts list
gcloud beta billing projects link "$PROJECT_ID" --billing-account=<ID_DA_CONTA>

gcloud services enable \
  run.googleapis.com \
  cloudbuild.googleapis.com \
  artifactregistry.googleapis.com \
  secretmanager.googleapis.com \
  sheets.googleapis.com
```

## Passo 2 — Token no Secret Manager

O token é gerado e gravado numa tubulação só. **Ele nunca aparece na tela, no
histórico do shell, no Git ou em log** — e ninguém precisa lê-lo, nem o Jonny.

```bash
openssl rand -base64 48 | tr -d '\n' | \
  gcloud secrets create crm-gateway-token \
    --replication-policy=automatic \
    --data-file=-
```

Permitir que só a conta de serviço leia o segredo:

```bash
gcloud secrets add-iam-policy-binding crm-gateway-token \
  --member="serviceAccount:${SA_EMAIL}" \
  --role="roles/secretmanager.secretAccessor"
```

Rotação futura: `openssl rand -base64 48 | tr -d '\n' | gcloud secrets versions
add crm-gateway-token --data-file=-`, redeploy, e recadastrar a API credential
(o recurso não tem edição — apaga e adiciona de novo).

## Passo 3 — Deploy

```bash
gcloud run deploy "$SERVICE" \
  --source=gateway \
  --region="$REGION" \
  --service-account="$SA_EMAIL" \
  --set-env-vars="SPREADSHEET_ID=${SHEET_ID}" \
  --set-secrets="GATEWAY_TOKEN=crm-gateway-token:latest" \
  --allow-unauthenticated \
  --min-instances=0 \
  --max-instances=2 \
  --cpu=1 --memory=512Mi --timeout=60s
```

Sobre `--allow-unauthenticated`: é **necessário**. O proxy do ambiente não faz
autenticação IAM do Google; ele injeta um header estático. A porta de entrada
fica aberta e **a autenticação é o `X-CRM-Token`**, conferido pelo serviço com
`hmac.compare_digest`. Sem o header correto, **todo** endpoint (menos `/health`)
devolve 401 -- inclusive caminhos que nao existem.

Se a organização tiver a política `constraints/iam.allowedPolicyMemberDomains`,
`allUsers` pode ser bloqueado e o comando falha — nesse caso é preciso uma
exceção na política para este serviço.

`--min-instances=0` mantém escala a zero (custo zero em repouso) ao preço de uma
partida a frio de alguns segundos na primeira chamada do dia. Adequado aqui.

## Passo 4 — Host exato

```bash
gcloud run services describe "$SERVICE" --region="$REGION" \
  --format='value(status.url)'
# ex.: https://crm-sheets-gateway-123456789012.southamerica-east1.run.app
```

Fumaça sem token (deve responder `{"status":"ok"}`):

```bash
curl -sS "$(gcloud run services describe "$SERVICE" --region="$REGION" \
  --format='value(status.url)')/health"
```

> O caminho é `/health`, **sem o "z"**. O Cloud Run reserva caminhos terminados
> em `z` (`/healthz`, `/readyz`, ...) e responde um 404 HTML próprio antes de a
> requisição chegar ao serviço —
> [known issues](https://docs.cloud.google.com/run/docs/known-issues#reserved_url_paths).

## Passo 5 — API credential no ambiente de nuvem

Em claude.ai/code → editar o ambiente → **API credentials** → **Add credential**:

| Campo | Valor |
|---|---|
| Name | `CRM Sheets Gateway` |
| Credential type | Bearer (padrão) |
| Allowed websites | **o host exato do passo 4**, ex. `crm-sheets-gateway-123456789012.southamerica-east1.run.app` |
| Custom headers → Name | `X-CRM-Token` |
| Custom headers → Prefix | *(vazio)* |
| Custom headers → Value | o token do Secret Manager |

**Nunca use `*.run.app`.** Esse curinga entregaria o token a qualquer serviço
Cloud Run do mundo que a sessão resolvesse chamar. Apenas o host exato.

O valor do token é colado direto do Secret Manager na UI da Anthropic, sem
passar por chat nem por arquivo:

```bash
gcloud secrets versions access latest --secret=crm-gateway-token
```

Rode esse comando no terminal do Jonny, copie, cole na UI, limpe a tela
(`clear`). Não cole o valor em nenhuma conversa.

## Passo 6 — URL do gateway como variável de ambiente

Ainda no editor do ambiente, em **Environment variables**:

```
CRM_GATEWAY_URL=https://crm-sheets-gateway-123456789012.southamerica-east1.run.app
```

A URL **não é segredo** — é só um endereço; o segredo é o token, que continua
fora das variáveis de ambiente. Variáveis novas valem para sessões criadas
depois; a sessão em andamento mantém as que já tinha.

## Passo 7 — Compartilhar a planilha

A planilha precisa estar compartilhada com `$SA_EMAIL` como **Editor**. Se já
estava (do desenho anterior com chave JSON), nada muda: a identidade é a mesma
conta de serviço, só o jeito de obter o token mudou.

## Passo 8 — Validação em sessão nova

```bash
.venv/bin/python inspect_base.py     # não escreve nada
```

Só depois disso, e com autorização explícita, o teste de conexão que grava uma
linha em `Execuções`.

## Depois do deploy: apagar a chave JSON antiga

Se já existir alguma chave JSON da conta de serviço, ela deixa de ter uso:

```bash
gcloud iam service-accounts keys list --iam-account="$SA_EMAIL"
gcloud iam service-accounts keys delete <KEY_ID> --iam-account="$SA_EMAIL"
```

E remover `GOOGLE_SERVICE_ACCOUNT_JSON` das variáveis do ambiente, se estiver lá.

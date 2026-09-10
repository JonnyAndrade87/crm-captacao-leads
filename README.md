# crm-captacao-leads

Automacao de captacao de leads que roda como **Claude Code cloud Routine** e
grava numa Google Sheets existente pela **Google Sheets API** (conta de servico).

## Onde roda
Ambiente de nuvem do Claude Code (claude.ai/code) vinculado a este repositorio
GitHub privado. As Routines executam nesse ambiente conforme agendamento, com o
Mac desligado. VM Ubuntu efemera, Python pre-instalado.

## Credenciais -- leia antes de subir a chave
- A chave JSON da conta de servico entra como **variavel de ambiente**
  `GOOGLE_SERVICE_ACCOUNT_JSON` na config do ambiente de nuvem.
- Pela doc oficial da Anthropic (code.claude.com/docs/en/cloud-environments):
  as variaveis de ambiente sao **copiadas para variaveis comuns legiveis por
  qualquer comando da sessao** e **"anyone who uses the environment can read
  the values"**. O dialogo da propria Anthropic avisa para nao colocar segredos
  ali. Ou seja: **a chave sera legivel dentro da VM da sessao (inclusive por
  comandos do Claude).**
- O recurso "API credentials" (proxy que injeta um token sem o agente ver) e
  Pro/Max, mas **nao se aplica** a uma conta de servico Google: o fluxo assina
  um JWT localmente com a chave privada, entao a chave precisa estar na VM.
- Manter a chave fora de qualquer VM gerenciada pela Anthropic so e possivel com
  um **self-hosted environment** (runner proprio) -- bem mais pesado; opcional.

### Reducao de alcance (o que fazer em vez de "ocultar")
1. Projeto Google Cloud dedicado (`crm-captacao-leads`) e conta de servico usada
   so nisto.
2. Escopo unico: `https://www.googleapis.com/auth/spreadsheets`.
3. Compartilhar **apenas esta planilha** com o e-mail da conta de servico como
   Editor. Ela nao acessa mais nada do Drive nem a conta Google.
4. Rede do ambiente: **Custom** com so `*.googleapis.com` e `accounts.google.com`
   (marque tambem incluir os registries de pacotes -- o hook `SessionStart`
   precisa alcancar o PyPI para montar o `.venv`).
5. Revogar/rotacionar a chave a qualquer momento em Google Cloud > IAM >
   Service Accounts > Keys, sem efeito na sua conta.
6. `.gitignore` bloqueia `*.json` / `.env`; a chave nunca vai para o Git.

## Preparacao do ambiente
O campo de **setup do ambiente de nuvem fica vazio de proposito**. Quem prepara
o Python e o hook `SessionStart` do repositorio:

- `.claude/hooks/session-start.sh` -- roda **so na nuvem** (`CLAUDE_CODE_REMOTE`),
  localiza o repo por `CLAUDE_PROJECT_DIR`, cria `.venv` isolado
  (**sem** `--system-site-packages`), instala `requirements.txt` e roda `pip check`.
  `pip check` falhando aborta o hook com status != 0.
- Registrado em `.claude/settings.json`. Passa a valer para toda sessao nova
  depois que o commit chegar no branch padrao.
- E idempotente: se o `.venv` ja existe, o pip so confere o que falta.

Por que o `.venv` isolado importa: a imagem da VM traz um `cryptography 41.0.7`
do Debian **quebrado** (sem `_cffi_backend`). O `google-auth` importa
`cryptography` ao carregar `service_account`, e o panico do binding Rust nao e
capturado pelo `try/except ImportError` -- a autenticacao morreria ali. O `.venv`
nao enxerga `dist-packages`, entao a versao do PyPI prevalece e o problema some
sem mexer no Python do sistema.

`cryptography` e `cffi` **nao** precisam entrar no `requirements.txt`: chegam
como dependencia transitiva de `google-auth` (verificado com `pip show` e
`pip check` em venv limpo).

Localmente (fora da nuvem), o mesmo passo a mao:
```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

## Uso manual
Sempre pelo interpretador do `.venv` -- o `python` do sistema nao tem as
dependencias e traz o `cryptography` quebrado.

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
`prospect.py` esta inerte. A Routine diaria so sera criada apos o teste de
conexao validado e autorizacao explicita.

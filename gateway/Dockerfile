# Imagem do gateway do CRM. Nenhuma credencial entra aqui: no Cloud Run a
# identidade vem da conta de servico anexada ao servico (ADC / metadata server)
# e o token compartilhado chega por --set-secrets a partir do Secret Manager.
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir --requirement requirements.txt

COPY main.py ./

# Sem root.
RUN useradd --create-home --uid 1001 gateway
USER gateway

ENV PORT=8080
EXPOSE 8080

# App factory: o Flask so e montado quando GATEWAY_TOKEN e SPREADSHEET_ID existem.
CMD exec gunicorn --bind ":$PORT" --workers 1 --threads 8 --timeout 60 \
    --access-logfile - --error-logfile - "main:create_app()"

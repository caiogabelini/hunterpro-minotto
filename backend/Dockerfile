# Imagem ÚNICA, usada por dois App Services diferentes no EasyPanel:
#   - backend: usa o CMD padrão abaixo (uvicorn)
#   - worker:  sobrescreve o comando no campo "Start Command" do painel
#              (celery -A app.workers.celery_app worker --loglevel=info)
# Não existe Dockerfile separado pro worker -- ver README, seção
# "Deploy em Produção".
FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# chmod explícito em vez de confiar no bit de execução vindo do COPY:
# o modo do arquivo depende de como o contexto de build chegou (git
# preserva o bit, um upload/zip nem sempre). Sem isso o container morre
# no boot com "permission denied".
RUN chmod +x /app/entrypoint.sh /app/scripts/wait_for_db.py

EXPOSE 8000

# ENTRYPOINT + CMD, e não só CMD: o "Start Command" do EasyPanel é
# passado como CMD (argumentos do ENTRYPOINT), então o worker também
# passa pela espera do banco sem precisar repetir nada na configuração.
ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

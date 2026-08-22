#!/bin/sh
#
# Entrypoint compartilhado pelos DOIS serviços que usam esta imagem no
# EasyPanel: o backend (uvicorn) e o worker (celery). A imagem é a mesma;
# o que muda é só o comando de start, configurado no painel do EasyPanel
# por serviço. Ver README, seção "Deploy em Produção".
#
# Responsabilidade única: esperar o Postgres responder antes de entregar
# o controle pro processo real. Os dois serviços falam com o banco, então
# os dois se beneficiam -- por isso a espera vive aqui no entrypoint, e
# não embutida no CMD do uvicorn.
#
# `exec "$@"` substitui o shell pelo processo final, em vez de deixá-lo
# como filho. Isso importa em container: o processo real vira PID 1 e
# recebe SIGTERM direto do Docker no shutdown, permitindo que o uvicorn
# encerre as conexões e o celery termine a task em andamento. Sem o
# `exec`, o sinal pararia no shell e o processo levaria SIGKILL no fim
# do grace period.
#
set -e

python scripts/wait_for_db.py

exec "$@"

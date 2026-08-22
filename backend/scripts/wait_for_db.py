#!/usr/bin/env python3
"""
Espera o Postgres ficar acessível antes de subir a aplicação.

Resolve o problema clássico de deploy em orquestrador (EasyPanel/Docker):
o container do backend sobe em segundos, mas o serviço do Postgres pode
ainda estar inicializando -- ou, pior, o DNS interno pode ainda não estar
resolvendo o hostname do serviço. Sem essa espera, o uvicorn sobe, a
primeira request estoura em `OperationalError`/`could not translate host
name`, e o container fica "no ar" servindo erro em vez de falhar de forma
óbvia.

Roda via `entrypoint.sh`, antes do processo principal -- tanto no serviço
do backend (uvicorn) quanto no do worker (celery), já que os dois usam a
MESMA imagem e os dois falam com o banco. Ver README, seção
"Deploy em Produção".

Estratégia: tenta `SELECT 1` em loop, com backoff exponencial (1s, 2s,
4s... limitado por `WAIT_FOR_DB_INTERVALO_MAX`), até conectar ou estourar
`WAIT_FOR_DB_TIMEOUT`. Sai com código 0 se conectou, 1 se desistiu -- o
código 1 é de propósito: o container morre e o orquestrador reinicia,
que é o comportamento certo pra um banco que não subiu, em vez de a
aplicação ficar de pé e quebrar a cada request.

Cada tentativa usa um engine descartável (`dispose()` no finally) em vez
do `engine` global de `app.core.database`: uma tentativa falha deixa
conexão morta no pool, e reaproveitar esse pool depois é justamente o
tipo de estado sujo que essa espera existe pra evitar.

Uso:
    python scripts/wait_for_db.py

Variáveis de ambiente (todas opcionais):
    WAIT_FOR_DB_TIMEOUT        segundos totais até desistir (padrão: 60)
    WAIT_FOR_DB_INTERVALO_MAX  teto do backoff entre tentativas (padrão: 5)
"""
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.exc import SQLAlchemyError  # noqa: E402

from app.core.config import settings  # noqa: E402

TIMEOUT_PADRAO = 60.0
INTERVALO_INICIAL = 1.0
INTERVALO_MAX_PADRAO = 5.0


def _mascarar_url(url: str) -> str:
    """
    Esconde a senha da URL antes de logar.

    O log do container é visível no painel do EasyPanel, então imprimir
    a DATABASE_URL crua vazaria a senha do banco pra qualquer pessoa com
    acesso ao painel -- mesmo princípio da lição registrada no CLAUDE.md
    sobre nunca deixar segredo cair em saída visível.
    """
    if "://" not in url:
        return url
    esquema, resto = url.split("://", 1)
    if "@" not in resto:
        return url
    credenciais, host = resto.rsplit("@", 1)
    usuario = credenciais.split(":", 1)[0]
    return f"{esquema}://{usuario}:***@{host}"


def esperar_banco(
    database_url: str,
    timeout: float = TIMEOUT_PADRAO,
    intervalo_max: float = INTERVALO_MAX_PADRAO,
) -> bool:
    """
    Tenta conectar em `database_url` até conseguir ou estourar `timeout`.

    Devolve True se conectou, False se desistiu. Nunca levanta exceção --
    quem chama decide o que fazer com o False (o `main()` aqui sai com
    código 1).
    """
    limite = time.monotonic() + timeout
    intervalo = INTERVALO_INICIAL
    tentativa = 0
    ultimo_erro: Exception | None = None

    while True:
        tentativa += 1
        engine = create_engine(database_url, pool_pre_ping=True)
        try:
            with engine.connect() as conexao:
                conexao.execute(text("SELECT 1"))
            print(f"[wait_for_db] Banco respondeu na tentativa {tentativa}.", flush=True)
            return True
        except SQLAlchemyError as exc:
            ultimo_erro = exc
        except Exception as exc:  # DNS, socket, etc. -- não só erros do SQLAlchemy
            ultimo_erro = exc
        finally:
            engine.dispose()

        restante = limite - time.monotonic()
        if restante <= 0:
            print(
                f"[wait_for_db] Desistindo após {timeout:.0f}s e {tentativa} tentativa(s). "
                f"Último erro: {type(ultimo_erro).__name__}: {ultimo_erro}",
                file=sys.stderr,
                flush=True,
            )
            return False

        # Nunca dormir além do que ainda resta do timeout -- senão a
        # espera total estoura o limite pedido por causa do backoff.
        espera = min(intervalo, restante)
        print(
            f"[wait_for_db] Tentativa {tentativa} falhou ({type(ultimo_erro).__name__}). "
            f"Nova tentativa em {espera:.1f}s ({restante:.0f}s restantes).",
            flush=True,
        )
        time.sleep(espera)
        intervalo = min(intervalo * 2, intervalo_max)


def _float_do_ambiente(nome: str, padrao: float) -> float:
    """Lê um número do ambiente, caindo no padrão se ausente ou inválido."""
    bruto = os.getenv(nome)
    if not bruto:
        return padrao
    try:
        valor = float(bruto)
    except ValueError:
        print(f"[wait_for_db] {nome}={bruto!r} não é um número; usando {padrao}.", file=sys.stderr)
        return padrao
    return valor if valor > 0 else padrao


def main() -> None:
    timeout = _float_do_ambiente("WAIT_FOR_DB_TIMEOUT", TIMEOUT_PADRAO)
    intervalo_max = _float_do_ambiente("WAIT_FOR_DB_INTERVALO_MAX", INTERVALO_MAX_PADRAO)

    print(
        f"[wait_for_db] Aguardando {_mascarar_url(settings.DATABASE_URL)} "
        f"(timeout {timeout:.0f}s).",
        flush=True,
    )
    if not esperar_banco(settings.DATABASE_URL, timeout=timeout, intervalo_max=intervalo_max):
        sys.exit(1)


if __name__ == "__main__":
    main()

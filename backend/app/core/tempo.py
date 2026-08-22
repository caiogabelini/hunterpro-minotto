"""
Helper de data/hora em UTC.

`datetime.utcnow()` está deprecado no Python 3.12+ (o próprio warning
sugere `datetime.now(datetime.UTC)`). A troca direta, porém, muda o
tipo do valor: `utcnow()` devolve um datetime **naive**, `now(UTC)`
devolve um **aware**.

Isso importa aqui porque todas as colunas de data deste projeto são
`DateTime` sem `timezone=True` — ou seja, `timestamp without time zone`
no Postgres. Gravar um datetime aware numa coluna dessas faz o Postgres
converter o valor usando o `TimeZone` da sessão antes de descartar o
offset. Verificado empiricamente numa instância Postgres 16 descartável
nesta sessão:

    TimeZone=UTC                naive -> 12:00:00   aware -> 12:00:00
    TimeZone=America/Sao_Paulo  naive -> 12:00:00   aware -> 09:00:00

Ou seja: num servidor cujo `TimeZone` não seja UTC, a troca ingênua
deslocaria silenciosamente todo timestamp novo em algumas horas — e
ainda misturaria linhas deslocadas com as antigas na mesma coluna. No
SQLite (suíte de testes) o tzinfo é simplesmente ignorado, então o
problema *não* apareceria nos testes.

Por isso `agora_utc()` usa `datetime.now(UTC)` (sem warning) e devolve
o valor naive, byte a byte idêntico ao que `utcnow()` gravava antes.

Se um dia as colunas migrarem para `DateTime(timezone=True)`, o
`.replace(tzinfo=None)` daqui é o único ponto que precisa sair.
"""
from datetime import UTC, datetime


def agora_utc() -> datetime:
    """UTC atual, naive — substituto direto de `datetime.utcnow()`."""
    return datetime.now(UTC).replace(tzinfo=None)

"""
Redação de credenciais em texto que vai pra log.
================================================================================

⚠️ **POR QUE ISTO EXISTE — risco real, 23/08/2026.** Investigando por que
nenhum dos 6 leads da primeira busca real tinha e-mail, a reprodução da
chamada ao Hunter.io devolveu:

    HTTPStatusError: Client error '401 Unauthorized' for url
    'https://api.hunter.io/v2/email-finder?domain=...&api_key=<A CHAVE>'

A `api_key` ia na **query string**, então ela entrava na mensagem da
exceção. `_rodar_etapa` (app/workers/celery_app.py) chama
`logger.exception(...)` em toda falha de etapa — ou seja, com a chave
preenchida, o primeiro 401/timeout gravaria a `HUNTER_API_KEY` em texto
puro no log do worker. Naquele momento a chave estava vazia e nada vazou,
mas é exatamente a lição já registrada no CLAUDE.md depois do incidente
com a `GOOGLE_API_KEY`: **nunca deixar um segredo entrar num texto que
alguém vai ver.**

A correção principal foi tirar a chave da URL (ver
`app/services/email_enrichment.py`). Este módulo é a segunda camada, pra
quando não dá: o ZeroBounce só aceita `api_key` na query string, e não há
como sondar alternativa daqui (a API está atrás de um Cloudflare que
responde 403 a este ambiente). Então a chave dele vai continuar na URL, e
o que impede o vazamento é a redação no ponto onde o texto viraria log.

DUAS ESTRATÉGIAS, de propósito — uma não cobre a outra:

  1. **Por nome de parâmetro** (`_PADRAO_PARAM_SENSIVEL`): pega
     `?api_key=...`, `&token=...` etc. mesmo de um serviço que este
     projeto ainda não usa. Não depende de conhecer o valor.
  2. **Por valor configurado** (`_valores_secretos`): varre o texto
     atrás do conteúdo literal de qualquer campo secreto de `Settings` e
     troca por `***`. Pega o caso que a primeira não pega — a chave
     aparecendo num corpo de resposta, num header ecoado, num nome de
     parâmetro que ninguém previu.

⚠️ Strings vazias NUNCA entram na estratégia 2 — `texto.replace("", ...)`
insere o marcador entre cada caractere e destruiria a mensagem. É o caso
normal neste ambiente, onde várias chaves estão vazias.
"""
from __future__ import annotations

import re

MARCADOR = "***REDIGIDO***"

# Nomes de parâmetro de query string que carregam credencial. Casado sem
# diferenciar maiúscula/minúscula; o valor vai até o próximo `&` ou o fim.
_PADRAO_PARAM_SENSIVEL = re.compile(
    r"(?i)\b(api[_-]?key|apikey|key|token|access[_-]?token|secret|password|senha|auth)"
    r"(=|%3D)([^&\s'\"]*)"
)

# Sufixos de nome de campo em `Settings` que indicam segredo. Baseado nos
# nomes que já existem em app/core/config.py (HUNTER_API_KEY,
# ZEROBOUNCE_API_KEY, GOOGLE_API_KEY, EVOLUTION_KEY, SECRET_KEY, ...) --
# um campo novo com um desses sufixos é coberto automaticamente.
_SUFIXOS_SECRETOS = ("_KEY", "_TOKEN", "_SECRET", "_PASSWORD", "_SENHA", "KEY")


def _valores_secretos() -> list[str]:
    """Valores literais dos campos secretos de `Settings`, dos mais longos
    pros mais curtos.

    A ordem importa: se duas chaves compartilham prefixo, redigir a mais
    longa primeiro evita deixar um pedaço da outra pra trás.

    Import local de propósito -- `app.core.config` monta `Settings` lendo
    o `.env`, e este módulo precisa poder ser importado (e testado) sem
    arrastar essa inicialização como efeito de import.
    """
    from app.core.config import settings

    valores = []
    for campo in type(settings).model_fields:
        if not campo.upper().endswith(_SUFIXOS_SECRETOS):
            continue
        valor = getattr(settings, campo, None)
        # `and valor` descarta string vazia -- ver a ressalva na docstring
        # do módulo; é o caso normal aqui, várias chaves estão em branco.
        if isinstance(valor, str) and valor:
            valores.append(valor)
    return sorted(valores, key=len, reverse=True)


def redigir_segredos(texto: object) -> str:
    """
    Devolve `texto` com toda credencial reconhecível trocada por
    `***REDIGIDO***`.

    Aceita qualquer objeto (converte com `str()`) pra poder embrulhar uma
    exceção direto, sem quem chama ter que lembrar de converter.

    Nunca levanta: se a leitura de `Settings` falhar por qualquer motivo,
    ainda assim devolve o texto com a redação por nome de parâmetro
    aplicada. Um sanitizador que explode no meio de um `except` trocaria
    um vazamento por um crash.
    """
    resultado = _PADRAO_PARAM_SENSIVEL.sub(rf"\1\2{MARCADOR}", str(texto))

    try:
        for valor in _valores_secretos():
            resultado = resultado.replace(valor, MARCADOR)
    except Exception:  # pragma: no cover -- ver docstring
        pass

    return resultado

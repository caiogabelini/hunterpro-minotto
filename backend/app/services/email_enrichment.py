"""
Enriquecimento de e-mail — Hunter.io (encontrar) + validação de MX
(dnspython) + ZeroBounce (validar entregabilidade).
================================================================================

Contrato das duas APIs já validado em produção no projeto HunterPro SR
Contábil — implementado aqui seguindo exatamente esse formato:

  Hunter.io Email Finder:
    GET https://api.hunter.io/v2/email-finder
    Parâmetros: domain, first_name, last_name, api_key
    Resposta: {"data": {"email": "...", "score": 90, ...}, "meta": {...}}
    (email vem null em "data" quando o Hunter não encontra nada — não é
    erro HTTP, é resposta 200 válida)

  ZeroBounce Validate:
    GET https://api.zerobounce.net/v2/validate
    Parâmetros: email, api_key
    Resposta inclui "status": um de valid | invalid | catch-all |
    do_not_mail | abuse — só valid e catch-all contam como aprovado pra
    prospecção (ver ZEROBOUNCE_STATUS_APROVADOS).

`enriquecer_email` encadeia as três etapas nessa ordem — Hunter.io, MX,
ZeroBounce — parando cedo (sem consultar o ZeroBounce, que é uma consulta
paga) se o Hunter.io não achar e-mail ou se o domínio não tiver MX válido.

O QUE ESTE MÓDULO **NÃO** FAZ (de propósito):
  - Não calcula pontos de score — devolve o resultado bruto de cada
    etapa; a conversão pro critério `email_validado` (rules.py) fica pro
    `compute_lead_score`, ainda não implementado.
  - Não tenta gerar e-mail por padrão (ex.: nome.sobrenome@dominio) se o
    Hunter.io não encontrar nada — decisão de produto ainda não
    confirmada com o cliente.
  - Não usa `client.base_url` como nos outros módulos HTTP deste
    projeto: Hunter.io e ZeroBounce são hosts diferentes, então as
    funções passam a URL absoluta em cada chamada — um único client sem
    base_url serve pros dois.
"""
from __future__ import annotations

from dataclasses import dataclass

import dns.exception
import dns.resolver
import httpx

from app.core.config import settings

HUNTER_EMAIL_FINDER_URL = "https://api.hunter.io/v2/email-finder"
ZEROBOUNCE_VALIDATE_URL = "https://api.zerobounce.net/v2/validate"
DEFAULT_TIMEOUT = 15.0

# Status que o ZeroBounce pode devolver: valid | invalid | catch-all |
# do_not_mail | abuse. Só esses dois contam como e-mail utilizável.
ZEROBOUNCE_STATUS_APROVADOS = {"valid", "catch-all"}


@dataclass
class HunterEmailResult:
    email: str | None
    score: int | None
    encontrado: bool


@dataclass
class ZeroBounceResult:
    status: str
    aprovado: bool


@dataclass
class EmailEnrichmentResult:
    """Resultado consolidado das três etapas. Campos ficam None quando a
    etapa correspondente nem chegou a rodar (ex.: `zerobounce_status` fica
    None se o Hunter.io não achou e-mail ou o MX não validou)."""

    email: str | None
    hunter_score: int | None
    mx_valido: bool | None
    zerobounce_status: str | None
    aprovado: bool


def _dividir_nome(nome: str | None) -> tuple[str | None, str | None]:
    """Divide um nome completo em (primeiro_nome, sobrenome) pro Hunter.io
    Email Finder, que pede os dois campos separados."""
    if not nome:
        return None, None
    partes = nome.strip().split(maxsplit=1)
    if len(partes) == 1:
        return partes[0], None
    return partes[0], partes[1]


def _get_client(client: httpx.Client | None) -> tuple[httpx.Client, bool]:
    """Sem base_url — Hunter.io e ZeroBounce são hosts diferentes, as
    funções abaixo sempre passam a URL absoluta."""
    if client is not None:
        return client, False
    return httpx.Client(timeout=DEFAULT_TIMEOUT), True


def buscar_email_hunter(
    dominio: str,
    nome_decisor: str | None = None,
    client: httpx.Client | None = None,
) -> HunterEmailResult:
    """
    Busca o e-mail mais provável do decisor via Hunter.io Email Finder.

    `encontrado=False` (email=None) é uma resposta válida do Hunter.io,
    não um erro — nem todo domínio tem e-mail descobrível por eles.
    """
    primeiro_nome, sobrenome = _dividir_nome(nome_decisor)
    client, owns_client = _get_client(client)

    params: dict[str, str] = {"domain": dominio, "api_key": settings.HUNTER_API_KEY}
    if primeiro_nome:
        params["first_name"] = primeiro_nome
    if sobrenome:
        params["last_name"] = sobrenome

    try:
        resp = client.get(HUNTER_EMAIL_FINDER_URL, params=params)
        resp.raise_for_status()
        data = resp.json().get("data") or {}
        email = data.get("email")

        return HunterEmailResult(email=email, score=data.get("score"), encontrado=email is not None)
    finally:
        if owns_client:
            client.close()


def validar_mx(dominio: str, resolver=dns.resolver.resolve) -> bool:
    """
    Confirma que o domínio tem registro MX válido — passo barato antes
    de gastar uma consulta paga no ZeroBounce com um domínio que
    claramente não recebe e-mail.

    `resolver` é injetável (mesmo padrão do `client` HTTP nos outros
    módulos) — por padrão usa `dns.resolver.resolve` de verdade, os
    testes passam um fake que não bate em DNS real.
    """
    try:
        resposta = resolver(dominio, "MX")
        return len(resposta) > 0
    except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer, dns.resolver.NoNameservers, dns.exception.Timeout):
        return False


def validar_zerobounce(email: str, client: httpx.Client | None = None) -> ZeroBounceResult:
    """Valida entregabilidade via ZeroBounce. "aprovado" só pra status
    "valid" ou "catch-all" — ver ZEROBOUNCE_STATUS_APROVADOS."""
    client, owns_client = _get_client(client)

    try:
        resp = client.get(
            ZEROBOUNCE_VALIDATE_URL,
            params={"email": email, "api_key": settings.ZEROBOUNCE_API_KEY},
        )
        resp.raise_for_status()
        status = resp.json().get("status", "")

        return ZeroBounceResult(status=status, aprovado=status in ZEROBOUNCE_STATUS_APROVADOS)
    finally:
        if owns_client:
            client.close()


def enriquecer_email(
    dominio: str,
    nome_decisor: str | None = None,
    client: httpx.Client | None = None,
    resolver=dns.resolver.resolve,
) -> EmailEnrichmentResult:
    """
    Encadeia Hunter.io -> MX -> ZeroBounce. Para cedo (sem bater no
    ZeroBounce, que é uma consulta paga) se o Hunter.io não achar e-mail
    ou se o MX do domínio não for válido.
    """
    hunter = buscar_email_hunter(dominio, nome_decisor, client=client)
    if not hunter.encontrado:
        return EmailEnrichmentResult(
            email=None, hunter_score=None, mx_valido=None, zerobounce_status=None, aprovado=False
        )

    dominio_email = hunter.email.split("@")[-1]
    mx_valido = validar_mx(dominio_email, resolver=resolver)
    if not mx_valido:
        return EmailEnrichmentResult(
            email=hunter.email,
            hunter_score=hunter.score,
            mx_valido=False,
            zerobounce_status=None,
            aprovado=False,
        )

    zerobounce = validar_zerobounce(hunter.email, client=client)

    return EmailEnrichmentResult(
        email=hunter.email,
        hunter_score=hunter.score,
        mx_valido=True,
        zerobounce_status=zerobounce.status,
        aprovado=zerobounce.aprovado,
    )

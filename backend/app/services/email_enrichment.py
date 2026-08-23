"""
Enriquecimento de e-mail — Hunter.io (encontrar) + validação de MX
(dnspython) + ZeroBounce (validar entregabilidade).
================================================================================

Contrato das duas APIs já validado em produção no projeto HunterPro SR
Contábil — implementado aqui seguindo exatamente esse formato:

  Hunter.io Email Finder:
    GET https://api.hunter.io/v2/email-finder
    Parâmetros: domain, first_name, last_name
    Credencial: header `Authorization: Bearer {HUNTER_API_KEY}`
    Resposta: {"data": {"email": "...", "score": 90, ...}, "meta": {...}}
    (email vem null em "data" quando o Hunter não encontra nada — não é
    erro HTTP, é resposta 200 válida)

  ZeroBounce Validate:
    GET https://api.zerobounce.net/v2/validate
    Parâmetros: email, api_key (na query — ver ressalva de segurança)
    Resposta inclui "status": um de valid | invalid | catch-all |
    do_not_mail | abuse — só valid e catch-all contam como aprovado pra
    prospecção (ver ZEROBOUNCE_STATUS_APROVADOS).

`enriquecer_email` encadeia as três etapas nessa ordem — Hunter.io, MX,
ZeroBounce — parando cedo (sem consultar o ZeroBounce, que é uma consulta
paga) se o Hunter.io não achar e-mail ou se o domínio não tiver MX válido.

⚠️ CREDENCIAIS NÃO VÃO NA URL (corrigido em 23/08/2026):
Até então a `api_key` das DUAS APIs ia na query string, e a mensagem de
erro do httpx (`Client error '401' for url '...?api_key=...'`) entrava no
`logger.exception` de `_rodar_etapa` — ou seja, a chave iria pro log em
texto puro no primeiro 401/timeout. Ver `app/core/segredos.py` pro
histórico. O que mudou:

  - **Hunter.io**: chave migrada pra header `Authorization: Bearer`.
    ✅ **VERIFICADO CONTRA A API REAL** em 23/08/2026, com chave válida:
    a mesma requisição devolve 401 `authentication_failed` sem
    credencial e 200 com e-mail encontrado usando só o header. O header
    é lido de fato — não é suposição.
    (Registro histórico, caso alguém repita a checagem: sondar isso SEM
    chave válida é inútil. O Hunter devolve `"No user found for the API
    key supplied"` idêntico pra query, Bearer, X-API-KEY e nenhuma
    credencial, então não dá pra distinguir "leu o header" de "ignorou".)
  - **ZeroBounce**: continua na query string. Não é desleixo — a API não
    documenta alternativa e não deu pra sondar: `api.zerobounce.net`
    responde 403 (Cloudflare) a este ambiente, com ou sem chave. A
    proteção aqui é 100% a redação no log.

Toda exceção HTTP dos dois é reembrulhada por `_erro_http_seguro`, que
passa a mensagem por `redigir_segredos` antes de deixá-la subir.

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
from app.core.segredos import redigir_segredos

HUNTER_EMAIL_FINDER_URL = "https://api.hunter.io/v2/email-finder"
ZEROBOUNCE_VALIDATE_URL = "https://api.zerobounce.net/v2/validate"
DEFAULT_TIMEOUT = 15.0

# Status que o ZeroBounce pode devolver: valid | invalid | catch-all |
# do_not_mail | abuse. Só esses dois contam como e-mail utilizável.
ZEROBOUNCE_STATUS_APROVADOS = {"valid", "catch-all"}

# Partículas que não servem como sobrenome pro Hunter.io -- ver
# `_dividir_nome`. Comparadas em minúsculo.
PARTICULAS_DE_SOBRENOME = {"de", "da", "do", "das", "dos", "e", "del", "di", "la", "van", "von"}


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
    """
    Divide um nome completo em (primeiro_nome, sobrenome) pro Hunter.io
    Email Finder, que pede os dois campos separados.

    ⚠️ **Corrigido em 23/08/2026 pra nomes brasileiros.** A versão
    anterior usava `split(maxsplit=1)`, então TODO o resto virava
    sobrenome: "ALBA REGINA RIBEIRO DE SOUZA" (decisora real de um lead
    da primeira busca) virava first_name="ALBA",
    last_name="REGINA RIBEIRO DE SOUZA". O Hunter espera um sobrenome,
    não o resto do nome inteiro, então isso derrubava a taxa de acerto em
    qualquer nome com 3+ palavras -- que é a maioria aqui.

    Agora o ÚLTIMO token é o sobrenome, e os do meio são descartados:
    "ALBA REGINA RIBEIRO DE SOUZA" -> ("ALBA", "SOUZA").

    Partículas finais ("DE", "DA", "DOS", ...) são puladas: "JOSE DE
    SOUZA DE" -> ("JOSE", "SOUZA"). Uma partícula NO MEIO do sobrenome
    composto não é reanexada ("DE SOUZA" vira só "SOUZA") -- é o que o
    Hunter espera, e reconstruir sobrenome composto é adivinhação sem
    ganho comprovado.

    Se sobrar só partícula depois do primeiro nome ("JOAO DE"), devolve
    sobrenome None em vez de uma partícula solta -- `buscar_email_hunter`
    simplesmente não manda `last_name`, e o Hunter busca só pelo domínio
    + primeiro nome.
    """
    if not nome:
        return None, None

    partes = nome.strip().split()
    if not partes:
        return None, None
    if len(partes) == 1:
        return partes[0], None

    primeiro_nome = partes[0]
    for token in reversed(partes[1:]):
        if token.lower() not in PARTICULAS_DE_SOBRENOME:
            return primeiro_nome, token
    return primeiro_nome, None


def _get_client(client: httpx.Client | None) -> tuple[httpx.Client, bool]:
    """Sem base_url — Hunter.io e ZeroBounce são hosts diferentes, as
    funções abaixo sempre passam a URL absoluta."""
    if client is not None:
        return client, False
    return httpx.Client(timeout=DEFAULT_TIMEOUT), True


class ErroHttpSeguro(RuntimeError):
    """
    Erro de chamada HTTP com a mensagem já passada por
    `redigir_segredos` -- ver a ressalva de credenciais na docstring do
    módulo.

    Existe pra que NENHUM caminho de exceção deste módulo carregue uma
    credencial. `httpx.HTTPStatusError` põe a URL completa na mensagem, e
    `_rodar_etapa` (app/workers/celery_app.py) loga a exceção inteira --
    então reembrulhar aqui, na origem, é mais confiável do que confiar em
    todo chamador futuro lembrar de sanitizar.

    Herda de `RuntimeError`, não de `httpx.HTTPError`: quem trata erro
    deste módulo já captura genericamente (`_rodar_etapa` pega
    `Exception`), e herdar de `httpx.HTTPError` convidaria alguém a ler
    `.response`/`.request` -- que é justamente onde a credencial mora.
    """


def _executar_http(descricao: str, chamada):
    """
    Roda uma chamada HTTP convertendo qualquer falha num
    `ErroHttpSeguro` de mensagem redigida.

    `descricao` é um rótulo fixo, escrito à mão (ex.: "Hunter.io
    email-finder") -- nunca a URL montada, justamente pra não reintroduzir
    o problema pelo texto que nós mesmos escrevemos.
    """
    try:
        return chamada()
    except httpx.HTTPError as exc:
        raise ErroHttpSeguro(f"{descricao}: {redigir_segredos(exc)}") from None


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

    # ⚠️ A chave vai no HEADER, não em `params` -- ver a ressalva de
    # credenciais na docstring do módulo. Nada aqui pode entrar na URL.
    params: dict[str, str] = {"domain": dominio}
    if primeiro_nome:
        params["first_name"] = primeiro_nome
    if sobrenome:
        params["last_name"] = sobrenome

    # Chave vazia -> nenhum header. O httpx recusa `"Bearer "` com
    # `Illegal header value`, o que mascararia o 401 real com um erro
    # local confuso. O caminho normal nem chega aqui sem chave --
    # `enrich_email` (app/workers/celery_app.py) já pula a etapa --, mas a
    # função tem que se sustentar sozinha.
    headers = (
        {"Authorization": f"Bearer {settings.HUNTER_API_KEY}"}
        if settings.HUNTER_API_KEY
        else {}
    )

    try:
        resp = _executar_http(
            "Hunter.io email-finder",
            lambda: client.get(HUNTER_EMAIL_FINDER_URL, params=params, headers=headers),
        )
        if resp.status_code == httpx.codes.UNAUTHORIZED:
            # Mensagem própria em vez de deixar o `raise_for_status`
            # genérico subir: 401 aqui quase sempre é chave errada,
            # expirada ou sem créditos, e dizer isso poupa a próxima
            # pessoa de reabrir a investigação de 23/08/2026. O suporte a
            # Bearer em si já está verificado (ver docstring do módulo),
            # então NÃO é a primeira hipótese.
            raise ErroHttpSeguro(
                "Hunter.io email-finder: 401 authentication_failed. Confira a HUNTER_API_KEY "
                "(inválida, expirada ou sem créditos). O envio da chave via header "
                "Authorization: Bearer está verificado contra a API real -- não é a causa provável."
            )
        _executar_http("Hunter.io email-finder", resp.raise_for_status)
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
        # ⚠️ Aqui a chave VAI na query string -- ver a ressalva de
        # credenciais na docstring do módulo (o ZeroBounce não documenta
        # alternativa, e não deu pra sondar daqui). `_executar_http`
        # garante que ela não chegue à mensagem de nenhuma exceção.
        resp = _executar_http(
            "ZeroBounce validate",
            lambda: client.get(
                ZEROBOUNCE_VALIDATE_URL,
                params={"email": email, "api_key": settings.ZEROBOUNCE_API_KEY},
            ),
        )
        _executar_http("ZeroBounce validate", resp.raise_for_status)
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

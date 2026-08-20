"""
Testes do enriquecimento de e-mail (Hunter.io + MX + ZeroBounce).

Usa httpx.MockTransport pras duas APIs (não precisa de rede real) e um
`resolver` fake injetado no lugar de dns.resolver.resolve pra validação
de MX (mesmo padrão de client injetável dos outros módulos) — mesmo
espírito de test_cnes.py e test_whatsapp.py.
"""
import dns.exception
import dns.resolver
import httpx
import pytest

from app.services.email_enrichment import (
    ZEROBOUNCE_STATUS_APROVADOS,
    buscar_email_hunter,
    enriquecer_email,
    validar_mx,
    validar_zerobounce,
)

DOMINIO = "clinicateste.com.br"
EMAIL_ENCONTRADO = "decisor@clinicateste.com.br"


def _fake_resolver_com_mx(dominio: str, tipo: str):
    return ["mx1.exemplo.com."]


def _fake_resolver_sem_dominio(dominio: str, tipo: str):
    raise dns.resolver.NXDOMAIN()


def _fake_resolver_sem_registro_mx(dominio: str, tipo: str):
    raise dns.resolver.NoAnswer()


def _fake_resolver_timeout(dominio: str, tipo: str):
    raise dns.exception.Timeout()


# --- buscar_email_hunter ---


def test_buscar_email_hunter_encontrado():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v2/email-finder"
        assert request.url.params["domain"] == DOMINIO
        assert request.url.params["first_name"] == "Ciclana"
        assert request.url.params["last_name"] == "Administradora"
        return httpx.Response(200, json={"data": {"email": EMAIL_ENCONTRADO, "score": 90}})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    resultado = buscar_email_hunter(DOMINIO, "Ciclana Administradora", client=client)

    assert resultado.encontrado is True
    assert resultado.email == EMAIL_ENCONTRADO
    assert resultado.score == 90


def test_buscar_email_hunter_nao_encontrado():
    """Hunter.io devolve 200 com data.email null quando não acha nada --
    não é erro HTTP, é uma resposta válida."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": {"email": None, "score": None}})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    resultado = buscar_email_hunter(DOMINIO, "Ciclana Administradora", client=client)

    assert resultado.encontrado is False
    assert resultado.email is None


def test_buscar_email_hunter_sem_nome_nao_envia_first_last_name():
    def handler(request: httpx.Request) -> httpx.Response:
        assert "first_name" not in request.url.params
        assert "last_name" not in request.url.params
        return httpx.Response(200, json={"data": {"email": None}})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    buscar_email_hunter(DOMINIO, None, client=client)


def test_buscar_email_hunter_nome_de_uma_palavra_so_manda_first_name():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["first_name"] == "Ciclana"
        assert "last_name" not in request.url.params
        return httpx.Response(200, json={"data": {"email": None}})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    buscar_email_hunter(DOMINIO, "Ciclana", client=client)


def test_buscar_email_hunter_erro_http_propaga_excecao():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(httpx.HTTPStatusError):
        buscar_email_hunter(DOMINIO, client=client)


# --- validar_mx ---


def test_validar_mx_com_registro_valido():
    assert validar_mx(DOMINIO, resolver=_fake_resolver_com_mx) is True


def test_validar_mx_dominio_inexistente():
    assert validar_mx("dominio-que-nao-existe.com.br", resolver=_fake_resolver_sem_dominio) is False


def test_validar_mx_sem_registro_mx():
    assert validar_mx(DOMINIO, resolver=_fake_resolver_sem_registro_mx) is False


def test_validar_mx_timeout():
    assert validar_mx(DOMINIO, resolver=_fake_resolver_timeout) is False


# --- validar_zerobounce: cada status possível ---


def _handler_zerobounce_com_status(status: str):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v2/validate"
        assert request.url.params["email"] == EMAIL_ENCONTRADO
        return httpx.Response(200, json={"status": status, "sub_status": ""})

    return handler


@pytest.mark.parametrize("status", ["valid", "catch-all"])
def test_validar_zerobounce_status_aprovado(status: str):
    client = httpx.Client(transport=httpx.MockTransport(_handler_zerobounce_com_status(status)))
    resultado = validar_zerobounce(EMAIL_ENCONTRADO, client=client)

    assert resultado.status == status
    assert resultado.aprovado is True


@pytest.mark.parametrize("status", ["invalid", "do_not_mail", "abuse"])
def test_validar_zerobounce_status_reprovado(status: str):
    client = httpx.Client(transport=httpx.MockTransport(_handler_zerobounce_com_status(status)))
    resultado = validar_zerobounce(EMAIL_ENCONTRADO, client=client)

    assert resultado.status == status
    assert resultado.aprovado is False


def test_zerobounce_status_aprovados_sao_exatamente_valid_e_catch_all():
    assert ZEROBOUNCE_STATUS_APROVADOS == {"valid", "catch-all"}


def test_validar_zerobounce_erro_http_propaga_excecao():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(httpx.HTTPStatusError):
        validar_zerobounce(EMAIL_ENCONTRADO, client=client)


# --- enriquecer_email: encadeamento completo ---


def test_enriquecer_email_hunter_nao_encontra_para_antes_do_mx_e_zerobounce():
    chamadas = []

    def handler(request: httpx.Request) -> httpx.Response:
        chamadas.append(request.url.path)
        return httpx.Response(200, json={"data": {"email": None}})

    def resolver_nao_deveria_ser_chamado(dominio: str, tipo: str):
        raise AssertionError("MX não deveria ser checado sem e-mail encontrado")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    resultado = enriquecer_email(DOMINIO, "Ciclana Administradora", client=client, resolver=resolver_nao_deveria_ser_chamado)

    assert resultado.email is None
    assert resultado.mx_valido is None
    assert resultado.zerobounce_status is None
    assert resultado.aprovado is False
    assert chamadas == ["/v2/email-finder"]  # só bateu no Hunter.io


def test_enriquecer_email_mx_invalido_para_antes_do_zerobounce():
    chamadas = []

    def handler(request: httpx.Request) -> httpx.Response:
        chamadas.append(request.url.path)
        return httpx.Response(200, json={"data": {"email": EMAIL_ENCONTRADO, "score": 80}})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    resultado = enriquecer_email(
        DOMINIO, "Ciclana Administradora", client=client, resolver=_fake_resolver_sem_dominio
    )

    assert resultado.email == EMAIL_ENCONTRADO
    assert resultado.mx_valido is False
    assert resultado.zerobounce_status is None
    assert resultado.aprovado is False
    assert chamadas == ["/v2/email-finder"]  # não chegou a bater no ZeroBounce


def test_enriquecer_email_fluxo_completo_aprovado():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v2/email-finder":
            return httpx.Response(200, json={"data": {"email": EMAIL_ENCONTRADO, "score": 95}})
        return httpx.Response(200, json={"status": "valid"})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    resultado = enriquecer_email(
        DOMINIO, "Ciclana Administradora", client=client, resolver=_fake_resolver_com_mx
    )

    assert resultado.email == EMAIL_ENCONTRADO
    assert resultado.hunter_score == 95
    assert resultado.mx_valido is True
    assert resultado.zerobounce_status == "valid"
    assert resultado.aprovado is True


def test_enriquecer_email_fluxo_completo_reprovado_no_zerobounce():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v2/email-finder":
            return httpx.Response(200, json={"data": {"email": EMAIL_ENCONTRADO, "score": 95}})
        return httpx.Response(200, json={"status": "invalid"})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    resultado = enriquecer_email(
        DOMINIO, "Ciclana Administradora", client=client, resolver=_fake_resolver_com_mx
    )

    assert resultado.mx_valido is True
    assert resultado.zerobounce_status == "invalid"
    assert resultado.aprovado is False

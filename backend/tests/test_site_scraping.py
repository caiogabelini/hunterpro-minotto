"""
Testes da varredura de site via Firecrawl.

Usa httpx.MockTransport (não precisa de rede real) — mesmo padrão de
test_cnes.py e test_whatsapp.py.
"""
import json

import httpx
import pytest

from app.services.site_scraping import extrair_instagram, scrape_site

URL_SITE = "https://clinicateste.com.br"


# --- scrape_site ---


def test_scrape_site_sucesso():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/v1/scrape"
        assert request.headers["authorization"].startswith("Bearer ")
        return httpx.Response(
            200,
            json={
                "success": True,
                "data": {"markdown": "# Clínica Teste\n\nAtendemos com RQE 12345.", "metadata": {}},
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    resultado = scrape_site(URL_SITE, client=client)

    assert resultado.sucesso is True
    assert "RQE 12345" in resultado.markdown


def test_scrape_site_envia_url_e_formats_no_corpo():
    corpo_recebido = {}

    def handler(request: httpx.Request) -> httpx.Response:
        corpo_recebido.update(json.loads(request.content))
        return httpx.Response(200, json={"success": True, "data": {"markdown": ""}})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    scrape_site(URL_SITE, client=client)

    assert corpo_recebido == {"url": URL_SITE, "formats": ["markdown"]}


def test_scrape_site_falha_reportada_pelo_firecrawl_nao_lanca_excecao():
    """success: false é uma resposta 200 válida (ex.: site fora do ar),
    não um erro HTTP -- não deve lançar exceção."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"success": False, "error": "Site indisponível"})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    resultado = scrape_site(URL_SITE, client=client)

    assert resultado.sucesso is False
    assert resultado.markdown == ""


def test_scrape_site_erro_http_propaga_excecao():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(httpx.HTTPStatusError):
        scrape_site(URL_SITE, client=client)


# --- extrair_instagram ---


def test_extrair_instagram_encontra_perfil():
    texto = "Siga a gente no Instagram: https://www.instagram.com/clinicateste/"
    assert extrair_instagram(texto) == "clinicateste"


def test_extrair_instagram_sem_link_retorna_none():
    texto = "Site institucional sem nenhuma rede social mencionada."
    assert extrair_instagram(texto) is None


def test_extrair_instagram_ignora_path_reservado_sozinho():
    """Só um link pra um post/reel/etc, sem link de perfil em lugar
    nenhum -- não é um perfil, deve ignorar e devolver None."""
    texto = "Confira: https://www.instagram.com/reel/Cxyz123abc/"
    assert extrair_instagram(texto) is None


def test_extrair_instagram_pula_reservado_e_acha_perfil_de_verdade():
    texto = (
        "Post recente: instagram.com/p/Cxyz123abc/ -- siga a clínica: "
        "instagram.com/clinicateste"
    )
    assert extrair_instagram(texto) == "clinicateste"


@pytest.mark.parametrize(
    "path_reservado",
    ["p", "reel", "reels", "stories", "explore", "accounts", "share"],
)
def test_extrair_instagram_ignora_cada_path_reservado(path_reservado: str):
    texto = f"instagram.com/{path_reservado}/algumacoisa"
    assert extrair_instagram(texto) is None


def test_extrair_instagram_remove_barra_final():
    texto = "instagram.com/clinicateste/"
    assert extrair_instagram(texto) == "clinicateste"


def test_extrair_instagram_remove_ponto_final_de_frase():
    """'.' é um caractere válido em nome de usuário do Instagram, então o
    regex captura -- mas um ponto final de frase não devia grudar no
    nome extraído."""
    texto = "Segue a gente lá: instagram.com/clinicateste."
    assert extrair_instagram(texto) == "clinicateste"

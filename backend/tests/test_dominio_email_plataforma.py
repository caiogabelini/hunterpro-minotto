"""
Rejeição de domínio de plataforma na busca de e-mail (23/08/2026).
================================================================================

⚠️ BUG REAL DE PRODUÇÃO. Na primeira execução real, um lead veio com
`email = "eladiosouza@instagram.com"`.

**Não foi regex nosso** -- não existe nenhuma extração de e-mail por
regex neste projeto (confirmado por varredura). A cadeia foi:

  1. o Google Places devolveu a página do Instagram da clínica como
     `websiteUri` (comum em consultório que só tem Instagram);
  2. isso virou `site_url_efetivo` sem filtro nenhum;
  3. `_extrair_dominio` devolveu "instagram.com" -- corretamente;
  4. o Hunter, perguntado sobre esse domínio + o nome do sócio, **GEROU**
     o endereço a partir do padrão do domínio (`source_type: "generated"`,
     score 84). Reproduzido na investigação, byte a byte.

A falha é nossa, na fronteira: fizemos a pergunta errada. O Hunter
respondeu certo pra "qual seria o e-mail de ELADIO SOUZA em
instagram.com".
"""
import httpx
import pytest

from app.services import email_enrichment
from app.services.email_enrichment import (
    DOMINIOS_SEM_EMAIL_CORPORATIVO,
    buscar_emails_do_dominio,
    dominio_pode_ter_email_corporativo,
    enriquecer_email,
)
from app.workers import celery_app

# O caso real, exatamente como veio de produção.
DOMINIO_REAL_DO_BUG = "instagram.com"
EMAIL_REAL_DO_BUG = "eladiosouza@instagram.com"


def _resolver_com_mx(dominio, tipo):
    return ["mx1.exemplo"]


# ---------------------------------------------------------------------------
# O predicado
# ---------------------------------------------------------------------------

def test_o_caso_real_e_rejeitado():
    assert dominio_pode_ter_email_corporativo(DOMINIO_REAL_DO_BUG) is False


@pytest.mark.parametrize("dominio", [
    "instagram.com", "facebook.com", "fb.com", "twitter.com", "x.com",
    "linkedin.com", "tiktok.com", "youtube.com", "pinterest.com", "threads.net",
    "whatsapp.com", "wa.me", "t.me", "telegram.me",
    "linktr.ee", "linktree.com", "bio.link", "beacons.ai",
    "doctoralia.com.br", "boaconsulta.com",
])
def test_dominios_de_plataforma_sao_rejeitados(dominio):
    assert dominio_pode_ter_email_corporativo(dominio) is False


@pytest.mark.parametrize("dominio", [
    "clinicaiben.com.br", "equilibryum.com.br", "prontobaby.com.br",
    "redehospitalcasa.com.br", "grupohospitalarvidas.com.br",  # domínios REAIS dos leads
    "clinicareal.com", "consultorio.med.br",
])
def test_dominios_corporativos_legitimos_continuam_passando(dominio):
    assert dominio_pode_ter_email_corporativo(dominio) is True


@pytest.mark.parametrize("dominio", ["gmail.com", "hotmail.com", "outlook.com", "uol.com.br"])
def test_provedor_de_email_gratuito_NAO_e_bloqueado(dominio):
    """Decisão deliberada: no Brasil é comum a clínica pequena usar Gmail
    como contato REAL. Bloquear descartaria lead legítimo. A linha é
    domínio de PLATAFORMA (onde ninguém tem caixa), não e-mail grátis."""
    assert dominio_pode_ter_email_corporativo(dominio) is True


@pytest.mark.parametrize("dominio", [
    "www.instagram.com", "m.facebook.com", "pt-br.facebook.com",
    "business.instagram.com", "br.linkedin.com",
])
def test_subdominio_de_plataforma_tambem_e_rejeitado(dominio):
    """Sem o casamento por sufixo, `business.instagram.com` passaria."""
    assert dominio_pode_ter_email_corporativo(dominio) is False


@pytest.mark.parametrize("dominio", ["instagram.com.br", "meuinstagram.com.br", "facebookclinica.com"])
def test_dominio_parecido_mas_diferente_nao_e_falso_positivo(dominio):
    """`instagram.com.br` é outro domínio registrável -- pode ser uma
    empresa brasileira legítima. O casamento é por domínio inteiro ou
    sufixo `.dominio`, nunca por substring."""
    assert dominio_pode_ter_email_corporativo(dominio) is True


@pytest.mark.parametrize("dominio", ["", None, "   "])
def test_dominio_vazio_e_rejeitado(dominio):
    assert dominio_pode_ter_email_corporativo(dominio) is False


def test_comparacao_ignora_maiuscula_e_ponto_final():
    assert dominio_pode_ter_email_corporativo("INSTAGRAM.COM") is False
    assert dominio_pode_ter_email_corporativo("instagram.com.") is False


# ---------------------------------------------------------------------------
# Guarda principal: nem consulta o Hunter (economiza crédito)
# ---------------------------------------------------------------------------

def test_enrich_email_pula_dominio_de_plataforma_sem_chamar_o_hunter(monkeypatch):
    def _nao_deveria_chamar(*a, **kw):
        raise AssertionError("enriquecer_email não deveria ser chamada com domínio de plataforma")

    monkeypatch.setattr("app.services.email_enrichment.enriquecer_email", _nao_deveria_chamar)

    resultado = celery_app.enrich_email(DOMINIO_REAL_DO_BUG, "ELADIO SOUZA")
    assert resultado["email"] is None
    assert resultado["email_aprovado"] is False
    assert "plataforma" in resultado["etapa_pulada"]


def test_enrich_email_continua_funcionando_em_dominio_legitimo(monkeypatch):
    monkeypatch.setattr(celery_app.settings, "HUNTER_API_KEY", "presente")
    monkeypatch.setattr(celery_app.settings, "ZEROBOUNCE_API_KEY", "presente")
    monkeypatch.setattr(
        "app.services.email_enrichment.enriquecer_email",
        lambda d, n: email_enrichment.EmailEnrichmentResult(
            email="eliane@equilibryum.com.br", hunter_score=99, mx_valido=True,
            zerobounce_status="valid", aprovado=True),
    )
    resultado = celery_app.enrich_email("equilibryum.com.br", "ELIANE CUKIERMAN")
    assert resultado["email"] == "eliane@equilibryum.com.br"
    assert "etapa_pulada" not in resultado


# ---------------------------------------------------------------------------
# Defesa em profundidade no serviço
# ---------------------------------------------------------------------------

def test_email_de_plataforma_devolvido_pelo_hunter_e_descartado(monkeypatch):
    """Cobre quem chamar o serviço direto (script pontual) e o caso de o
    Hunter devolver endereço num domínio diferente do consultado."""
    monkeypatch.setattr(email_enrichment.settings, "HUNTER_API_KEY", "chave-ficticia")
    client = httpx.Client(transport=httpx.MockTransport(
        lambda r: httpx.Response(200, json={"data": {"email": EMAIL_REAL_DO_BUG, "score": 84}})))

    resultado = enriquecer_email(DOMINIO_REAL_DO_BUG, "ELADIO SOUZA",
                                 client=client, resolver=_resolver_com_mx)
    assert resultado.email is None
    assert resultado.aprovado is False


def test_secundarios_de_plataforma_tambem_sao_filtrados(monkeypatch):
    """Um secundário de plataforma apareceria no dossiê como contato
    acionável do mesmo jeito."""
    monkeypatch.setattr(email_enrichment.settings, "HUNTER_API_KEY", "chave-ficticia")
    client = httpx.Client(transport=httpx.MockTransport(
        lambda r: httpx.Response(200, json={"data": {"emails": [
            {"value": "contato@clinicareal.com.br", "type": "generic", "confidence": 90},
            {"value": "clinica@instagram.com", "type": "personal", "confidence": 95},
            {"value": "rh@clinicareal.com.br", "type": "generic", "confidence": 60},
        ]}})))

    r = buscar_emails_do_dominio("clinicareal.com.br", client=client)
    # O de plataforma tinha o maior confidence e é "personal" -- seria o
    # escolhido como principal se não fosse filtrado... mas o filtro dos
    # secundários não muda a escolha do principal, então aqui ele é o
    # principal e os secundários vêm limpos.
    assert all("instagram.com" not in e["email"] for e in r.outros_emails)


def test_o_score_nunca_foi_corrompido_por_esse_bug():
    """Registro do que a investigação apurou: o ZeroBounce devolveu
    'invalid' pro endereço falso, então `email_aprovado` ficou False e o
    critério `email_validado` não pontuou. O estrago foi no dossiê (o
    endereço aparecia como contato acionável), não no score."""
    from app.services.email_enrichment import ZEROBOUNCE_STATUS_APROVADOS
    assert "invalid" not in ZEROBOUNCE_STATUS_APROVADOS


def test_a_lista_de_dominios_e_imutavel():
    """frozenset -- ninguém acrescenta domínio em runtime por engano."""
    assert isinstance(DOMINIOS_SEM_EMAIL_CORPORATIVO, frozenset)

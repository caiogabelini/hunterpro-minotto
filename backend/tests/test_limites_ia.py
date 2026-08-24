"""
Controle de custo das gerações de IA no dossiê (24/08/2026).
================================================================================

Cada "Gerar mensagem"/"Gerar Insights" é uma chamada paga à Anthropic, e
os botões eram clicáveis sem limite. Com a tela indo pra usuários
"client", isso é risco real de custo.

Ver `app/api/routes/limites_ia.py` pro racional das duas formas de contar
(histórico de `lead_messages` vs. contador em `Lead`) e de por que o
reset usa marca d'água em vez de apagar linhas.
"""
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.routes import leads as rotas_leads
from app.core.database import Base, get_db
from app.core.security import hash_password
from app.core.tempo import agora_utc
from app.main import app
from app.models.lead import Lead
from app.models.lead_message import LeadMessage
from app.models.user import User, UserRole
from app.services.ai_enrichment import MensagemGerada

LIMITE = 2  # settings.LIMITE_GERACOES_IA_POR_LEAD


@pytest.fixture
def db_session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,  # ver "Test infra gotcha" no CLAUDE.md
    )
    Base.metadata.create_all(engine)
    sessao = sessionmaker(bind=engine)()
    app.dependency_overrides[get_db] = lambda: sessao
    yield sessao
    app.dependency_overrides.clear()


@pytest.fixture
def client():
    return TestClient(app)


def _criar_usuario(db, email, role):
    u = User(email=email, senha_hash=hash_password("senha12345"), role=role.value)
    db.add(u)
    db.commit()
    return u


def _token(client, email):
    r = client.post("/api/auth/login", json={"email": email, "senha": "senha12345"})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


@pytest.fixture
def token_client(client, db_session):
    _criar_usuario(db_session, "cliente@x.com", UserRole.CLIENT)
    return _token(client, "cliente@x.com")


@pytest.fixture
def token_admin(client, db_session):
    _criar_usuario(db_session, "admin@x.com", UserRole.ADMIN)
    return _token(client, "admin@x.com")


@pytest.fixture
def lead(db_session):
    lead = Lead(cnpj="11222333000181", razao_social="CLINICA TESTE", email="c@x.com",
                decisor_nome="MARIA SILVA", whatsapp_ativo=True, telefone="5511987654321")
    db_session.add(lead)
    db_session.commit()
    db_session.refresh(lead)
    return lead


@pytest.fixture(autouse=True)
def _ia_falsa(monkeypatch):
    """Nenhum teste aqui pode chamar a IA de verdade -- é justamente o
    gasto que este recurso existe pra controlar."""
    monkeypatch.setattr(
        rotas_leads, "gerar_mensagem_abordagem",
        lambda dados, canal: MensagemGerada(conteudo="Olá!", assunto="Assunto" if canal == "email" else None),
    )
    monkeypatch.setattr(
        rotas_leads, "gerar_insights_estrategicos",
        lambda dados: {"resumo_estrategico": "ok", "potencial_oportunidade": "alto",
                       "recomendacao_abordagem": ["a"], "estrategia_comunicacao": "x",
                       "cta_sugerido": "y"},
    )


def _h(token):
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# Limite de gerações
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("canal", ["email", "whatsapp"])
def test_mensagem_bloqueia_no_429_apos_o_limite(client, lead, token_client, canal):
    for i in range(LIMITE):
        r = client.post(f"/api/leads/{lead.id}/gerar-abordagem/{canal}", headers=_h(token_client))
        assert r.status_code == 200, f"geração {i+1} deveria passar: {r.text}"

    r = client.post(f"/api/leads/{lead.id}/gerar-abordagem/{canal}", headers=_h(token_client))
    assert r.status_code == 429
    assert "Limite de gerações atingido" in r.json()["detail"]
    assert "administrador" in r.json()["detail"]


def test_insights_bloqueia_no_429_apos_o_limite(client, lead, token_client):
    for i in range(LIMITE):
        r = client.post(f"/api/leads/{lead.id}/gerar-insights", headers=_h(token_client))
        assert r.status_code == 200, f"geração {i+1} deveria passar: {r.text}"

    r = client.post(f"/api/leads/{lead.id}/gerar-insights", headers=_h(token_client))
    assert r.status_code == 429


def test_limite_e_por_canal_nao_compartilhado(client, lead, token_client):
    """Esgotar e-mail não pode bloquear WhatsApp -- são custos separados."""
    for _ in range(LIMITE):
        client.post(f"/api/leads/{lead.id}/gerar-abordagem/email", headers=_h(token_client))

    assert client.post(f"/api/leads/{lead.id}/gerar-abordagem/email", headers=_h(token_client)).status_code == 429
    assert client.post(f"/api/leads/{lead.id}/gerar-abordagem/whatsapp", headers=_h(token_client)).status_code == 200
    assert client.post(f"/api/leads/{lead.id}/gerar-insights", headers=_h(token_client)).status_code == 200


def test_limite_e_por_lead_nao_global(client, db_session, token_client, lead):
    outro = Lead(cnpj="99888777000166", razao_social="OUTRA CLINICA")
    db_session.add(outro)
    db_session.commit()
    db_session.refresh(outro)

    for _ in range(LIMITE):
        client.post(f"/api/leads/{lead.id}/gerar-abordagem/email", headers=_h(token_client))

    assert client.post(f"/api/leads/{lead.id}/gerar-abordagem/email", headers=_h(token_client)).status_code == 429
    assert client.post(f"/api/leads/{outro.id}/gerar-abordagem/email", headers=_h(token_client)).status_code == 200


def test_geracao_que_falha_na_ia_nao_gasta_cota(client, lead, token_client, monkeypatch):
    """502 não pode consumir cota -- o usuário não recebeu nada."""
    monkeypatch.setattr(rotas_leads, "gerar_insights_estrategicos", lambda dados: {})
    assert client.post(f"/api/leads/{lead.id}/gerar-insights", headers=_h(token_client)).status_code == 502

    db = client.app.dependency_overrides[get_db]()
    assert db.get(Lead, lead.id).insights_geracoes_count == 0


def test_429_nao_chama_a_ia(client, lead, token_client, monkeypatch):
    """O ponto do limite é NÃO GASTAR -- checar depois de gerar seria inútil."""
    for _ in range(LIMITE):
        client.post(f"/api/leads/{lead.id}/gerar-insights", headers=_h(token_client))

    def _nao_deveria_chamar(dados):
        raise AssertionError("a IA não pode ser chamada com o limite atingido")

    monkeypatch.setattr(rotas_leads, "gerar_insights_estrategicos", _nao_deveria_chamar)
    assert client.post(f"/api/leads/{lead.id}/gerar-insights", headers=_h(token_client)).status_code == 429


# ---------------------------------------------------------------------------
# Contagens expostas no dossiê
# ---------------------------------------------------------------------------

def test_get_lead_expoe_contagens_e_limite(client, lead, token_client):
    client.post(f"/api/leads/{lead.id}/gerar-abordagem/email", headers=_h(token_client))
    client.post(f"/api/leads/{lead.id}/gerar-insights", headers=_h(token_client))

    corpo = client.get(f"/api/leads/{lead.id}", headers=_h(token_client)).json()
    assert corpo["geracoes_ia"] == {"email": 1, "whatsapp": 0, "insights": 1, "limite": LIMITE}


def test_lista_de_leads_nao_calcula_geracoes(client, lead, token_client):
    """Evita N+1 -- nenhuma tela de lista usa esse dado."""
    corpo = client.get("/api/leads", headers=_h(token_client)).json()
    assert corpo[0]["geracoes_ia"] is None


# ---------------------------------------------------------------------------
# Reset pelo admin
# ---------------------------------------------------------------------------

def test_admin_reseta_e_libera_novas_geracoes(client, lead, token_client, token_admin):
    for _ in range(LIMITE):
        client.post(f"/api/leads/{lead.id}/gerar-abordagem/email", headers=_h(token_client))
    assert client.post(f"/api/leads/{lead.id}/gerar-abordagem/email", headers=_h(token_client)).status_code == 429

    r = client.post(f"/api/admin/leads/{lead.id}/resetar-limite-ia/email", headers=_h(token_admin))
    assert r.status_code == 200
    assert r.json()["geracoes_ia"]["email"] == 0

    assert client.post(f"/api/leads/{lead.id}/gerar-abordagem/email", headers=_h(token_client)).status_code == 200


def test_reset_nao_apaga_o_historico_de_mensagens(client, db_session, lead, token_client, token_admin):
    """A razão de `lead_messages` existir é guardar o histórico -- resetar
    o limite não pode destruí-lo."""
    for _ in range(LIMITE):
        client.post(f"/api/leads/{lead.id}/gerar-abordagem/email", headers=_h(token_client))
    client.post(f"/api/admin/leads/{lead.id}/resetar-limite-ia/email", headers=_h(token_admin))

    linhas = db_session.query(LeadMessage).filter(LeadMessage.lead_id == lead.id).all()
    assert len(linhas) == LIMITE, "as mensagens antigas continuam no histórico"


def test_reset_de_insights_zera_o_contador(client, db_session, lead, token_client, token_admin):
    for _ in range(LIMITE):
        client.post(f"/api/leads/{lead.id}/gerar-insights", headers=_h(token_client))
    db_session.expire_all()
    assert db_session.get(Lead, lead.id).insights_geracoes_count == LIMITE

    client.post(f"/api/admin/leads/{lead.id}/resetar-limite-ia/insights", headers=_h(token_admin))
    db_session.expire_all()
    assert db_session.get(Lead, lead.id).insights_geracoes_count == 0
    assert client.post(f"/api/leads/{lead.id}/gerar-insights", headers=_h(token_client)).status_code == 200


def test_reset_e_por_tipo_nao_libera_os_outros(client, lead, token_client, token_admin):
    for _ in range(LIMITE):
        client.post(f"/api/leads/{lead.id}/gerar-abordagem/email", headers=_h(token_client))
        client.post(f"/api/leads/{lead.id}/gerar-abordagem/whatsapp", headers=_h(token_client))

    client.post(f"/api/admin/leads/{lead.id}/resetar-limite-ia/email", headers=_h(token_admin))

    assert client.post(f"/api/leads/{lead.id}/gerar-abordagem/email", headers=_h(token_client)).status_code == 200
    assert client.post(f"/api/leads/{lead.id}/gerar-abordagem/whatsapp", headers=_h(token_client)).status_code == 429


def test_client_nao_pode_resetar(client, lead, token_client):
    """Quem é limitado não pode ser quem libera."""
    r = client.post(f"/api/admin/leads/{lead.id}/resetar-limite-ia/email", headers=_h(token_client))
    assert r.status_code == 403


def test_reset_sem_token_retorna_401(client, lead):
    assert client.post(f"/api/admin/leads/{lead.id}/resetar-limite-ia/email").status_code == 401


def test_reset_com_tipo_invalido_retorna_422(client, lead, token_admin):
    r = client.post(f"/api/admin/leads/{lead.id}/resetar-limite-ia/telepatia", headers=_h(token_admin))
    assert r.status_code == 422


def test_reset_de_lead_inexistente_retorna_404(client, token_admin):
    r = client.post("/api/admin/leads/nao-existe/resetar-limite-ia/email", headers=_h(token_admin))
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Contador agregado do Dashboard
# ---------------------------------------------------------------------------

def test_dashboard_soma_os_tres_tipos_do_mes(client, db_session, lead, token_client):
    client.post(f"/api/leads/{lead.id}/gerar-abordagem/email", headers=_h(token_client))
    client.post(f"/api/leads/{lead.id}/gerar-abordagem/whatsapp", headers=_h(token_client))
    client.post(f"/api/leads/{lead.id}/gerar-insights", headers=_h(token_client))

    corpo = client.get("/api/dashboard/summary", headers=_h(token_client)).json()
    assert corpo["total_geracoes_ia_mes"] == 3  # 1 e-mail + 1 whatsapp + 1 insights


def test_dashboard_ignora_geracoes_de_meses_anteriores(client, db_session, lead, token_client):
    antiga = LeadMessage(lead_id=lead.id, canal="email", conteudo="velha",
                         gerado_em=agora_utc() - timedelta(days=60))
    db_session.add(antiga)
    db_session.commit()

    client.post(f"/api/leads/{lead.id}/gerar-abordagem/email", headers=_h(token_client))

    corpo = client.get("/api/dashboard/summary", headers=_h(token_client)).json()
    assert corpo["total_geracoes_ia_mes"] == 1  # só a do mês corrente


def test_dashboard_zero_sem_nenhuma_geracao(client, lead, token_client):
    corpo = client.get("/api/dashboard/summary", headers=_h(token_client)).json()
    assert corpo["total_geracoes_ia_mes"] == 0

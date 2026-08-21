"""
Testes da task orquestradora `executar_busca_mensal`
(app/workers/celery_app.py) -- o painel admin de "busca mensal".

Mesmo padrão de test_process_lead_pipeline.py: `gerar_semente_leads` e
`process_lead_pipeline` são substituídas por fakes via monkeypatch,
direto no namespace do módulo `app.workers.celery_app` -- evita rodar o
pipeline de verdade (rede real, Redis real). Persistência via sessão
SQLite em memória, injetada por `db=`, mesmo padrão de client
injetável do resto do projeto.
"""
from app.core.database import Base
from app.models.busca_leads import BuscaLeads, StatusBusca
from app.models.user import User, UserRole
from app.workers import celery_app
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


def _db_session_sqlite():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _criar_admin_e_busca(db_session) -> BuscaLeads:
    admin = User(email="admin@minotto.com.br", senha_hash="hash", role=UserRole.ADMIN.value, ativo=True)
    db_session.add(admin)
    db_session.commit()
    db_session.refresh(admin)

    busca = BuscaLeads(iniciado_por_id=admin.id, status=StatusBusca.EXECUTANDO.value)
    db_session.add(busca)
    db_session.commit()
    db_session.refresh(busca)
    return busca


def _semente(quantidade: int) -> dict:
    return {
        "status": "ok",
        "total_encontrado": quantidade,
        "estabelecimentos": [
            {"cnpj": f"1111111100{i:04d}", "razao_social": f"Clinica {i}", "uf": "SP"} for i in range(quantidade)
        ],
    }


def test_marca_executando_e_preenche_total_cnpjs_antes_de_processar(monkeypatch):
    db_session = _db_session_sqlite()
    busca = _criar_admin_e_busca(db_session)

    totais_vistos_durante_loop = []

    def fake_gerar_semente_leads(**kwargs):
        return _semente(3)

    def fake_process_lead_pipeline(cnpj, razao_social=None):
        # confirma que, no momento em que o loop já está processando
        # CNPJs, a busca já foi marcada "executando" e já tem
        # total_cnpjs_encontrados preenchido -- não só no fim.
        db_session.expire_all()
        atual = db_session.get(BuscaLeads, busca.id)
        totais_vistos_durante_loop.append((atual.status, atual.total_cnpjs_encontrados))
        return {"cnpj": cnpj, "lead_id": "fake-lead-id"}

    monkeypatch.setattr(celery_app, "gerar_semente_leads", fake_gerar_semente_leads)
    monkeypatch.setattr(celery_app, "process_lead_pipeline", fake_process_lead_pipeline)

    celery_app.executar_busca_mensal(busca.id, db=db_session)

    assert totais_vistos_durante_loop == [
        (StatusBusca.EXECUTANDO.value, 3),
        (StatusBusca.EXECUTANDO.value, 3),
        (StatusBusca.EXECUTANDO.value, 3),
    ]


def test_conclui_com_sucesso_quando_todos_os_cnpjs_processam(monkeypatch):
    db_session = _db_session_sqlite()
    busca = _criar_admin_e_busca(db_session)

    monkeypatch.setattr(celery_app, "gerar_semente_leads", lambda **kwargs: _semente(4))
    monkeypatch.setattr(
        celery_app, "process_lead_pipeline", lambda cnpj, razao_social=None: {"cnpj": cnpj, "lead_id": "x"}
    )

    resultado = celery_app.executar_busca_mensal(busca.id, db=db_session)

    db_session.refresh(busca)
    assert busca.status == StatusBusca.CONCLUIDO.value
    assert busca.total_cnpjs_encontrados == 4
    assert busca.total_leads_processados == 4
    assert busca.erros == []
    assert busca.concluido_em is not None
    assert resultado["status"] == StatusBusca.CONCLUIDO.value


def test_erro_em_um_cnpj_nao_impede_o_processamento_dos_outros(monkeypatch):
    db_session = _db_session_sqlite()
    busca = _criar_admin_e_busca(db_session)

    monkeypatch.setattr(celery_app, "gerar_semente_leads", lambda **kwargs: _semente(3))

    def fake_process_lead_pipeline(cnpj, razao_social=None):
        if cnpj.endswith("0001"):
            raise RuntimeError("Receita Federal fora do ar")
        return {"cnpj": cnpj, "lead_id": "x"}

    monkeypatch.setattr(celery_app, "process_lead_pipeline", fake_process_lead_pipeline)

    celery_app.executar_busca_mensal(busca.id, db=db_session)

    db_session.refresh(busca)
    # 1 falhou, 2 processaram -- o loop não parou no primeiro erro
    assert busca.total_leads_processados == 2
    assert len(busca.erros) == 1
    assert "Receita Federal fora do ar" in busca.erros[0]
    # status ainda é "concluido" -- só 1/3 (~33%) falhou, abaixo do critério de 50%
    assert busca.status == StatusBusca.CONCLUIDO.value


def test_status_erro_quando_mais_da_metade_dos_cnpjs_falha(monkeypatch):
    db_session = _db_session_sqlite()
    busca = _criar_admin_e_busca(db_session)

    monkeypatch.setattr(celery_app, "gerar_semente_leads", lambda **kwargs: _semente(4))

    contador = {"n": 0}

    def fake_process_lead_pipeline(cnpj, razao_social=None):
        contador["n"] += 1
        if contador["n"] <= 3:  # 3 de 4 falham (75%)
            raise RuntimeError("falhou")
        return {"cnpj": cnpj, "lead_id": "x"}

    monkeypatch.setattr(celery_app, "process_lead_pipeline", fake_process_lead_pipeline)

    celery_app.executar_busca_mensal(busca.id, db=db_session)

    db_session.refresh(busca)
    assert busca.status == StatusBusca.ERRO.value
    assert busca.total_leads_processados == 1
    assert len(busca.erros) == 3


def test_semente_sem_arquivos_conclui_com_zero_cnpjs(monkeypatch):
    db_session = _db_session_sqlite()
    busca = _criar_admin_e_busca(db_session)

    monkeypatch.setattr(
        celery_app, "gerar_semente_leads", lambda **kwargs: {"status": "sem_arquivos", "dir": "/tmp/qualquer"}
    )

    def fake_process_lead_pipeline(cnpj, razao_social=None):
        raise AssertionError("não deveria ser chamado -- semente sem estabelecimentos")

    monkeypatch.setattr(celery_app, "process_lead_pipeline", fake_process_lead_pipeline)

    celery_app.executar_busca_mensal(busca.id, db=db_session)

    db_session.refresh(busca)
    assert busca.status == StatusBusca.CONCLUIDO.value
    assert busca.total_cnpjs_encontrados == 0
    assert busca.total_leads_processados == 0
    assert busca.erros == []


def test_gerar_semente_leads_falha_marca_busca_como_erro(monkeypatch):
    db_session = _db_session_sqlite()
    busca = _criar_admin_e_busca(db_session)

    def fake_gerar_semente_leads(**kwargs):
        raise RuntimeError("arquivo corrompido")

    monkeypatch.setattr(celery_app, "gerar_semente_leads", fake_gerar_semente_leads)

    resultado = celery_app.executar_busca_mensal(busca.id, db=db_session)

    db_session.refresh(busca)
    assert busca.status == StatusBusca.ERRO.value
    assert busca.concluido_em is not None
    assert any("arquivo corrompido" in e for e in busca.erros)
    assert resultado["status"] == StatusBusca.ERRO.value


def test_busca_inexistente_nao_lanca_excecao(monkeypatch):
    db_session = _db_session_sqlite()

    def fake_gerar_semente_leads(**kwargs):
        raise AssertionError("não deveria nem tentar rodar a busca")

    monkeypatch.setattr(celery_app, "gerar_semente_leads", fake_gerar_semente_leads)

    resultado = celery_app.executar_busca_mensal("id-que-nao-existe", db=db_session)

    assert resultado["status"] == "erro"

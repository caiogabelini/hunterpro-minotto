"""
Testes da task orquestradora `executar_busca_mensal`
(app/workers/celery_app.py) -- o painel admin de "busca mensal".

Mesmo padrão de test_process_lead_pipeline.py: `gerar_semente_leads` e
`process_lead_pipeline` são substituídas por fakes via monkeypatch,
direto no namespace do módulo `app.workers.celery_app` -- evita rodar o
pipeline de verdade (rede real, Redis real). Persistência via sessão
SQLite em memória, injetada por `db=`, mesmo padrão de client
injetável do resto do projeto.

Desde 22/08/2026 a task tem DUAS fases (pré-seleção gratuita, depois
enriquecimento pago só no top N -- ver app/scoring/pre_selecao.py), então
todo teste aqui precisa de um Redis fake: a fase 1 consulta o índice PGFN
e ABORTA se ele nunca foi populado. A fixture `redis_fake` (autouse)
cuida disso -- ela substitui `redis.from_url` e já marca o índice como
populado, para que o caminho padrão dos testes seja "índice existe". Os
testes que querem exercitar o aborto desligam isso explicitamente.
"""
import json

import pytest
from app.core.database import Base
from app.models.busca_leads import BuscaLeads, StatusBusca
from app.models.user import User, UserRole
from app.services.pgfn import REDIS_KEY_PREFIX, REDIS_KEY_UPDATED_AT
from app.workers import celery_app
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


class FakeRedis:
    """Mínimo do redis-py que a fase 1 usa: get (carimbo do índice) e
    mget (lookup em lote da dívida ativa)."""

    def __init__(self):
        self._store: dict[str, str] = {}

    def marcar_indice_pgfn_populado(self):
        self._store[REDIS_KEY_UPDATED_AT] = "1755820800"

    def registrar_divida(self, cnpj: str, qtd: int):
        self._store[f"{REDIS_KEY_PREFIX}{cnpj}"] = json.dumps(
            {"qtd_inscricoes_ativas": qtd, "valor_total": 1000.0 * qtd, "algum_ajuizado": False}
        )

    def get(self, key):
        val = self._store.get(key)
        return val.encode() if val else None

    def mget(self, keys):
        return [self._store[k].encode() if k in self._store else None for k in keys]


@pytest.fixture
def redis_fake(monkeypatch):
    """
    Substitui `redis.from_url` por um fake em memória e já deixa o índice
    PGFN marcado como populado -- sem isso TODO teste cairia no aborto por
    índice ausente (guarda de custo, ver `_indice_pgfn_populado`).

    Não é autouse: os testes que exercitam justamente o aborto precisam de
    um fake SEM o carimbo, e recebem `redis_fake_vazio`.
    """
    fake = FakeRedis()
    fake.marcar_indice_pgfn_populado()
    monkeypatch.setattr("redis.from_url", lambda *a, **kw: fake)
    return fake


@pytest.fixture
def redis_fake_vazio(monkeypatch):
    """Fake sem o carimbo `pgfn:index_updated_at` -- índice nunca populado."""
    fake = FakeRedis()
    monkeypatch.setattr("redis.from_url", lambda *a, **kw: fake)
    return fake


@pytest.fixture(autouse=True)
def sem_tabela_de_municipios(monkeypatch, tmp_path):
    """
    Aponta o diretório de dados abertos pra uma pasta vazia, para que
    nenhum teste leia por acidente arquivos reais da Receita que estejam
    na máquina de quem roda a suíte.

    Efeito colateral relevante pros asserts: sem a tabela MUNICCSV, a
    Zona Franca não é resolvida e `executar_busca_mensal` acrescenta um
    AVISO em `busca.erros`. Por isso os testes de caminho feliz checam
    erros de CNPJ com `_erros_de_cnpj()` em vez de `busca.erros == []`.
    """
    monkeypatch.setattr(celery_app.settings, "RECEITA_FEDERAL_DADOS_ABERTOS_DIR", str(tmp_path))


def _erros_de_cnpj(busca) -> list[str]:
    """Só os erros por CNPJ, descartando os avisos de degradação da fase 1."""
    return [e for e in (busca.erros or []) if not e.startswith("Tabela de municípios")]


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
            {
                "cnpj": f"1111111100{i:04d}",
                "razao_social": f"Clinica {i}",
                "uf": "SP",
                # Campos que a PRÉ-SELEÇÃO lê (ver gerar_semente_leads).
                # "02" = ativa, então todo candidato pontua o critério
                # unidade_cnpj_ativo e o ranking fica decidido por PGFN /
                # Zona Franca / desempate por CNPJ.
                "municipio_codigo_rfb": "7107",
                "situacao_cadastral": "02",
            }
            for i in range(quantidade)
        ],
    }


def test_marca_executando_e_preenche_total_cnpjs_antes_de_processar(monkeypatch, redis_fake):
    db_session = _db_session_sqlite()
    busca = _criar_admin_e_busca(db_session)

    totais_vistos_durante_loop = []

    def fake_gerar_semente_leads(**kwargs):
        return _semente(3)

    def fake_process_lead_pipeline(cnpj, razao_social=None, cache_cnes=None):
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


def test_conclui_com_sucesso_quando_todos_os_cnpjs_processam(monkeypatch, redis_fake):
    db_session = _db_session_sqlite()
    busca = _criar_admin_e_busca(db_session)

    monkeypatch.setattr(celery_app, "gerar_semente_leads", lambda **kwargs: _semente(4))
    monkeypatch.setattr(
        celery_app, "process_lead_pipeline", lambda cnpj, razao_social=None, cache_cnes=None: {"cnpj": cnpj, "lead_id": "x"}
    )

    resultado = celery_app.executar_busca_mensal(busca.id, db=db_session)

    db_session.refresh(busca)
    assert busca.status == StatusBusca.CONCLUIDO.value
    assert busca.total_cnpjs_encontrados == 4
    assert busca.total_leads_processados == 4
    assert _erros_de_cnpj(busca) == []
    assert busca.concluido_em is not None
    assert resultado["status"] == StatusBusca.CONCLUIDO.value


def test_erro_em_um_cnpj_nao_impede_o_processamento_dos_outros(monkeypatch, redis_fake):
    db_session = _db_session_sqlite()
    busca = _criar_admin_e_busca(db_session)

    monkeypatch.setattr(celery_app, "gerar_semente_leads", lambda **kwargs: _semente(3))

    def fake_process_lead_pipeline(cnpj, razao_social=None, cache_cnes=None):
        if cnpj.endswith("0001"):
            raise RuntimeError("Receita Federal fora do ar")
        return {"cnpj": cnpj, "lead_id": "x"}

    monkeypatch.setattr(celery_app, "process_lead_pipeline", fake_process_lead_pipeline)

    celery_app.executar_busca_mensal(busca.id, db=db_session)

    db_session.refresh(busca)
    # 1 falhou, 2 processaram -- o loop não parou no primeiro erro
    assert busca.total_leads_processados == 2
    erros_cnpj = _erros_de_cnpj(busca)
    assert len(erros_cnpj) == 1
    assert "Receita Federal fora do ar" in erros_cnpj[0]
    # status ainda é "concluido" -- só 1/3 (~33%) falhou, abaixo do critério de 50%
    assert busca.status == StatusBusca.CONCLUIDO.value


def test_status_erro_quando_mais_da_metade_dos_cnpjs_falha(monkeypatch, redis_fake):
    db_session = _db_session_sqlite()
    busca = _criar_admin_e_busca(db_session)

    monkeypatch.setattr(celery_app, "gerar_semente_leads", lambda **kwargs: _semente(4))

    contador = {"n": 0}

    def fake_process_lead_pipeline(cnpj, razao_social=None, cache_cnes=None):
        contador["n"] += 1
        if contador["n"] <= 3:  # 3 de 4 falham (75%)
            raise RuntimeError("falhou")
        return {"cnpj": cnpj, "lead_id": "x"}

    monkeypatch.setattr(celery_app, "process_lead_pipeline", fake_process_lead_pipeline)

    celery_app.executar_busca_mensal(busca.id, db=db_session)

    db_session.refresh(busca)
    assert busca.status == StatusBusca.ERRO.value
    assert busca.total_leads_processados == 1
    assert len(_erros_de_cnpj(busca)) == 3


def test_semente_sem_arquivos_conclui_com_zero_cnpjs(monkeypatch):
    db_session = _db_session_sqlite()
    busca = _criar_admin_e_busca(db_session)

    monkeypatch.setattr(
        celery_app, "gerar_semente_leads", lambda **kwargs: {"status": "sem_arquivos", "dir": "/tmp/qualquer"}
    )

    def fake_process_lead_pipeline(cnpj, razao_social=None, cache_cnes=None):
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


# ---------------------------------------------------------------------------
# Pré-seleção: só os melhores candidatos chegam ao enriquecimento PAGO
# ---------------------------------------------------------------------------


def test_so_chama_process_lead_pipeline_para_os_selecionados(monkeypatch, redis_fake):
    """
    O teste que justifica esta sessão inteira: com um universo maior que o
    top N, o pipeline pago roda SÓ nos selecionados -- não em todo CNPJ da
    semente, como acontecia antes de 22/08/2026.
    """
    db_session = _db_session_sqlite()
    busca = _criar_admin_e_busca(db_session)

    monkeypatch.setattr(celery_app, "gerar_semente_leads", lambda **kwargs: _semente(500))
    monkeypatch.setattr(celery_app, "_top_n_pre_selecao", lambda: 60)

    processados = []

    def fake_process_lead_pipeline(cnpj, razao_social=None, cache_cnes=None):
        processados.append(cnpj)
        return {"cnpj": cnpj, "lead_id": "x"}

    monkeypatch.setattr(celery_app, "process_lead_pipeline", fake_process_lead_pipeline)

    resultado = celery_app.executar_busca_mensal(busca.id, db=db_session)

    assert len(processados) == 60, "gastou API em mais CNPJs do que o top N"
    assert len(set(processados)) == 60, "processou o mesmo CNPJ mais de uma vez"

    db_session.refresh(busca)
    assert busca.total_cnpjs_encontrados == 500   # universo inteiro registrado
    assert busca.total_cnpjs_selecionados == 60   # corte aplicado
    assert busca.total_leads_processados == 60
    assert resultado["total_cnpjs_encontrados"] == 500
    assert resultado["total_cnpjs_selecionados"] == 60


def test_universo_menor_que_o_top_n_processa_todo_mundo(monkeypatch, redis_fake):
    db_session = _db_session_sqlite()
    busca = _criar_admin_e_busca(db_session)

    monkeypatch.setattr(celery_app, "gerar_semente_leads", lambda **kwargs: _semente(5))
    monkeypatch.setattr(celery_app, "_top_n_pre_selecao", lambda: 60)

    processados = []
    monkeypatch.setattr(
        celery_app,
        "process_lead_pipeline",
        lambda cnpj, razao_social=None, cache_cnes=None: processados.append(cnpj),
    )

    celery_app.executar_busca_mensal(busca.id, db=db_session)

    assert len(processados) == 5
    db_session.refresh(busca)
    assert busca.total_cnpjs_selecionados == 5


def test_candidatos_com_divida_ativa_sao_priorizados_no_corte(monkeypatch, redis_fake):
    """
    Sinal gratuito realmente muda QUEM recebe o enriquecimento pago: só
    dois CNPJs do universo têm dívida ativa no índice, e o corte é de 2.
    """
    db_session = _db_session_sqlite()
    busca = _criar_admin_e_busca(db_session)

    monkeypatch.setattr(celery_app, "gerar_semente_leads", lambda **kwargs: _semente(20))
    monkeypatch.setattr(celery_app, "_top_n_pre_selecao", lambda: 2)

    # dois CNPJs no fim da lista (desempate por CNPJ os deixaria de fora)
    redis_fake.registrar_divida("11111111000018", 6)
    redis_fake.registrar_divida("11111111000019", 4)

    processados = []
    monkeypatch.setattr(
        celery_app,
        "process_lead_pipeline",
        lambda cnpj, razao_social=None, cache_cnes=None: processados.append(cnpj),
    )

    celery_app.executar_busca_mensal(busca.id, db=db_session)

    assert processados == ["11111111000018", "11111111000019"]


def test_composicao_da_selecao_volta_no_resultado(monkeypatch, redis_fake):
    """
    A composição existe pra tornar visível o efeito emergente dos pesos
    (ver o aviso no topo de app/scoring/pre_selecao.py) já na primeira
    execução real, em vez de só depois de abrir os leads um a um.
    """
    db_session = _db_session_sqlite()
    busca = _criar_admin_e_busca(db_session)

    monkeypatch.setattr(celery_app, "gerar_semente_leads", lambda **kwargs: _semente(10))
    monkeypatch.setattr(celery_app, "_top_n_pre_selecao", lambda: 5)
    redis_fake.registrar_divida("11111111000000", 6)
    monkeypatch.setattr(
        celery_app, "process_lead_pipeline", lambda cnpj, razao_social=None, cache_cnes=None: None
    )

    resultado = celery_app.executar_busca_mensal(busca.id, db=db_session)

    composicao = resultado["composicao_selecao"]
    assert composicao["total"] == 5
    assert composicao["com_divida_ativa"] == 1
    assert composicao["score_preliminar_max"] > composicao["score_preliminar_min"]


def test_taxa_de_falha_e_calculada_sobre_os_selecionados_nao_sobre_o_universo(monkeypatch, redis_fake):
    """
    Regressão de leitura: 60 processados de 300 mil encontrados NÃO é 99,98%
    de falha. O denominador tem que ser o lote selecionado.
    """
    db_session = _db_session_sqlite()
    busca = _criar_admin_e_busca(db_session)

    monkeypatch.setattr(celery_app, "gerar_semente_leads", lambda **kwargs: _semente(1000))
    monkeypatch.setattr(celery_app, "_top_n_pre_selecao", lambda: 10)

    contador = {"n": 0}

    def fake_process_lead_pipeline(cnpj, razao_social=None, cache_cnes=None):
        contador["n"] += 1
        if contador["n"] <= 2:  # 2 de 10 selecionados falham = 20%
            raise RuntimeError("falhou")

    monkeypatch.setattr(celery_app, "process_lead_pipeline", fake_process_lead_pipeline)

    celery_app.executar_busca_mensal(busca.id, db=db_session)

    db_session.refresh(busca)
    # 20% dos selecionados falharam -> abaixo do critério de 50% -> concluído
    assert busca.status == StatusBusca.CONCLUIDO.value
    assert busca.total_leads_processados == 8


# ---------------------------------------------------------------------------
# Guardas que abortam ANTES de gastar dinheiro
# ---------------------------------------------------------------------------


def test_aborta_sem_gastar_se_o_indice_pgfn_nunca_foi_populado(monkeypatch, redis_fake_vazio):
    db_session = _db_session_sqlite()
    busca = _criar_admin_e_busca(db_session)

    monkeypatch.setattr(celery_app, "gerar_semente_leads", lambda **kwargs: _semente(100))

    def nao_deveria_rodar(cnpj, razao_social=None, cache_cnes=None):
        raise AssertionError("gastou API sem o índice PGFN -- seleção seria aleatória")

    monkeypatch.setattr(celery_app, "process_lead_pipeline", nao_deveria_rodar)

    resultado = celery_app.executar_busca_mensal(busca.id, db=db_session)

    db_session.refresh(busca)
    assert busca.status == StatusBusca.ERRO.value
    assert busca.total_leads_processados is None
    assert any("refresh_pgfn_index" in e for e in busca.erros)
    assert resultado["status"] == StatusBusca.ERRO.value


def test_falha_na_coleta_de_sinais_aborta_sem_gastar(monkeypatch, redis_fake):
    db_session = _db_session_sqlite()
    busca = _criar_admin_e_busca(db_session)

    monkeypatch.setattr(celery_app, "gerar_semente_leads", lambda **kwargs: _semente(100))

    def coleta_quebrada(*args, **kwargs):
        raise RuntimeError("Redis fora do ar")

    monkeypatch.setattr(celery_app, "_coletar_sinais_gratuitos", coleta_quebrada)
    monkeypatch.setattr(
        celery_app,
        "process_lead_pipeline",
        lambda *a, **kw: (_ for _ in ()).throw(AssertionError("não deveria gastar")),
    )

    celery_app.executar_busca_mensal(busca.id, db=db_session)

    db_session.refresh(busca)
    assert busca.status == StatusBusca.ERRO.value
    assert any("Redis fora do ar" in e for e in busca.erros)


def test_sem_tabela_de_municipios_registra_aviso_mas_continua(monkeypatch, redis_fake):
    """
    Degradação consciente: sem a MUNICCSV, Zona Franca não entra na
    pré-seleção. A busca continua (ranking só por PGFN), mas o admin vê
    o motivo em `erros` -- nunca degrada em silêncio.
    """
    db_session = _db_session_sqlite()
    busca = _criar_admin_e_busca(db_session)

    monkeypatch.setattr(celery_app, "gerar_semente_leads", lambda **kwargs: _semente(3))
    monkeypatch.setattr(
        celery_app, "process_lead_pipeline", lambda cnpj, razao_social=None, cache_cnes=None: None
    )

    celery_app.executar_busca_mensal(busca.id, db=db_session)

    db_session.refresh(busca)
    assert busca.status == StatusBusca.CONCLUIDO.value
    assert busca.total_leads_processados == 3
    assert any("MUNICCSV" in e for e in busca.erros)


# ---------------------------------------------------------------------------
# Cache de município do CNES
# ---------------------------------------------------------------------------


def test_cache_cnes_e_compartilhado_entre_os_leads_da_mesma_execucao(monkeypatch, redis_fake):
    db_session = _db_session_sqlite()
    busca = _criar_admin_e_busca(db_session)

    monkeypatch.setattr(celery_app, "gerar_semente_leads", lambda **kwargs: _semente(4))

    caches_recebidos = []

    def fake_process_lead_pipeline(cnpj, razao_social=None, cache_cnes=None):
        caches_recebidos.append(cache_cnes)
        # simula a etapa CNES guardando o município no cache
        cache_cnes["355030"] = ["estabelecimento-fake"]

    monkeypatch.setattr(celery_app, "process_lead_pipeline", fake_process_lead_pipeline)

    celery_app.executar_busca_mensal(busca.id, db=db_session)

    assert len(caches_recebidos) == 4
    # é o MESMO objeto em todas as chamadas -- não uma cópia por lead
    assert all(c is caches_recebidos[0] for c in caches_recebidos)


def test_cache_cnes_nao_e_reaproveitado_entre_execucoes_diferentes(monkeypatch, redis_fake):
    """
    O cache tem vida útil de UMA execução. Se fosse de módulo, a busca do
    mês seguinte reaproveitaria o cadastro do CNES do mês anterior sem
    ninguém perceber.
    """
    db_session = _db_session_sqlite()
    monkeypatch.setattr(celery_app, "gerar_semente_leads", lambda **kwargs: _semente(2))

    caches_por_execucao = []

    def fake_process_lead_pipeline(cnpj, razao_social=None, cache_cnes=None):
        cache_cnes[f"municipio-{cnpj}"] = ["x"]
        caches_por_execucao.append(cache_cnes)

    monkeypatch.setattr(celery_app, "process_lead_pipeline", fake_process_lead_pipeline)

    primeira = _criar_admin_e_busca(db_session)
    celery_app.executar_busca_mensal(primeira.id, db=db_session)
    cache_primeira = caches_por_execucao[-1]

    segunda = BuscaLeads(iniciado_por_id=primeira.iniciado_por_id, status=StatusBusca.EXECUTANDO.value)
    db_session.add(segunda)
    db_session.commit()
    db_session.refresh(segunda)
    celery_app.executar_busca_mensal(segunda.id, db=db_session)
    cache_segunda = caches_por_execucao[-1]

    assert cache_segunda is not cache_primeira, "a segunda busca reusou o cache da primeira"


def test_enrich_cnes_com_cache_nao_repagina_o_mesmo_municipio(monkeypatch):
    """
    Nível da etapa: dois CNPJs do mesmo município -> UMA paginação só.
    É aqui que mora a economia real (até 500 requisições por município).
    """
    chamadas = []

    def fake_fetch(codigo_municipio, client=None):
        chamadas.append(str(codigo_municipio))
        return []

    monkeypatch.setattr("app.services.cnes.fetch_estabelecimentos_por_municipio", fake_fetch)

    cache: dict = {}
    celery_app.enrich_cnes("11111111000101", 355030, cache_municipios=cache)
    celery_app.enrich_cnes("22222222000102", 355030, cache_municipios=cache)
    celery_app.enrich_cnes("33333333000103", 330455, cache_municipios=cache)

    # 2 municípios distintos -> 2 buscas, não 3
    assert chamadas == ["355030", "330455"]
    assert set(cache) == {"355030", "330455"}


def test_enrich_cnes_normaliza_a_chave_do_cache_entre_int_e_str(monkeypatch):
    """
    O código do município chega como int (de enrich_receita_federal) ou
    str (chamada manual). Sem normalizar, 355030 e "355030" seriam duas
    entradas e o cache erraria o alvo em silêncio.
    """
    chamadas = []
    monkeypatch.setattr(
        "app.services.cnes.fetch_estabelecimentos_por_municipio",
        lambda codigo_municipio, client=None: chamadas.append(codigo_municipio) or [],
    )

    cache: dict = {}
    celery_app.enrich_cnes("11111111000101", 355030, cache_municipios=cache)
    celery_app.enrich_cnes("22222222000102", "355030", cache_municipios=cache)

    assert len(chamadas) == 1
    assert list(cache) == ["355030"]


def test_enrich_cnes_sem_cache_mantem_o_comportamento_antigo(monkeypatch):
    """Chamada avulsa (cache=None) continua buscando sempre da API."""
    chamadas = []
    monkeypatch.setattr(
        "app.services.cnes.fetch_estabelecimentos_por_municipio",
        lambda codigo_municipio, client=None: chamadas.append(codigo_municipio) or [],
    )

    celery_app.enrich_cnes("11111111000101", 355030)
    celery_app.enrich_cnes("22222222000102", 355030)

    assert len(chamadas) == 2


def test_composicao_separa_as_duas_fases_da_pre_selecao(monkeypatch, redis_fake):
    """
    Integração das duas fases: com 4 candidatos com dívida e cota de 6, o
    lote sai com 4 da fase 1 (dívida, ordenados por inscrições) e 2 da
    fase 2 (completando a cota).
    """
    db_session = _db_session_sqlite()
    busca = _criar_admin_e_busca(db_session)

    monkeypatch.setattr(celery_app, "gerar_semente_leads", lambda **kwargs: _semente(20))
    monkeypatch.setattr(celery_app, "_top_n_pre_selecao", lambda: 6)

    for cnpj, qtd in [
        ("11111111000015", 2),
        ("11111111000016", 9),
        ("11111111000017", 1),
        ("11111111000018", 5),
    ]:
        redis_fake.registrar_divida(cnpj, qtd)

    processados = []
    monkeypatch.setattr(
        celery_app,
        "process_lead_pipeline",
        lambda cnpj, razao_social=None, cache_cnes=None: processados.append(cnpj),
    )

    resultado = celery_app.executar_busca_mensal(busca.id, db=db_session)

    composicao = resultado["composicao_selecao"]
    assert composicao["por_divida_ativa"] == 4
    assert composicao["para_completar_cota"] == 2
    assert composicao["inscricoes_pgfn_max"] == 9
    assert composicao["inscricoes_pgfn_min"] == 1

    # os 4 com dívida vêm primeiro, na ordem decrescente de inscrições
    assert processados[:4] == [
        "11111111000016",  # 9
        "11111111000018",  # 5
        "11111111000015",  # 2
        "11111111000017",  # 1
    ]


def test_cota_cheia_de_devedores_nao_processa_ninguem_sem_divida(monkeypatch, redis_fake):
    """
    Ponta a ponta da regra principal: havendo devedores suficientes,
    nenhum CNPJ sem dívida recebe enriquecimento pago.
    """
    db_session = _db_session_sqlite()
    busca = _criar_admin_e_busca(db_session)

    monkeypatch.setattr(celery_app, "gerar_semente_leads", lambda **kwargs: _semente(50))
    monkeypatch.setattr(celery_app, "_top_n_pre_selecao", lambda: 5)

    # 8 devedores para uma cota de 5 -- sobra devedor de fora, então
    # nenhuma vaga pode ir para quem não deve.
    devedores = {f"11111111000{i:03d}": 1 + (i % 4) for i in range(40, 48)}
    for cnpj, qtd in devedores.items():
        redis_fake.registrar_divida(cnpj, qtd)

    processados = []
    monkeypatch.setattr(
        celery_app,
        "process_lead_pipeline",
        lambda cnpj, razao_social=None, cache_cnes=None: processados.append(cnpj),
    )

    resultado = celery_app.executar_busca_mensal(busca.id, db=db_session)

    assert len(processados) == 5
    assert all(cnpj in devedores for cnpj in processados)
    assert resultado["composicao_selecao"]["para_completar_cota"] == 0

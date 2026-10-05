"""
Exclusão de CNPJs que já estão na tabela `leads` antes do corte da
pré-seleção (05/10/2026) -- a segunda busca mensal não pode trazer
nenhum lead repetido.

Cobre três camadas:
  - funções puras de app/scoring/pre_selecao.py (normalização + exclusão);
  - `executar_busca_mensal` de ponta a ponta (fakes de semente/pipeline,
    SQLite em memória, Redis fake) -- inclusive que a exclusão vem ANTES
    do corte, pra cota continuar cheia;
  - o dry-run `scripts/simular_selecao.py`, que não pode gravar nada.
"""
import importlib.util
import json
from pathlib import Path

import pytest
from app.core.database import Base
from app.models.busca_leads import BuscaLeads, StatusBusca
from app.models.lead import KanbanStatus, Lead
from app.models.user import User, UserRole
from app.scoring.pre_selecao import excluir_cnpjs_ja_na_base, normalizar_cnpj, resumir_composicao
from app.services.pgfn import REDIS_KEY_PREFIX, REDIS_KEY_UPDATED_AT
from app.workers import celery_app
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


class FakeRedis:
    """Só GET/MGET -- o que a fase gratuita usa."""

    def __init__(self):
        self._store: dict[str, str] = {REDIS_KEY_UPDATED_AT: "1755820800"}

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
    fake = FakeRedis()
    monkeypatch.setattr("redis.from_url", lambda *a, **kw: fake)
    return fake


@pytest.fixture(autouse=True)
def sem_arquivos_reais(monkeypatch, tmp_path):
    """Nenhum teste lê arquivos reais da Receita da máquina de quem roda."""
    monkeypatch.setattr(celery_app.settings, "RECEITA_FEDERAL_DADOS_ABERTOS_DIR", str(tmp_path))


@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _cnpj(i: int) -> str:
    return f"1111111100{i:04d}"


def _com_mascara(cnpj: str) -> str:
    return f"{cnpj[:2]}.{cnpj[2:5]}.{cnpj[5:8]}/{cnpj[8:12]}-{cnpj[12:]}"


def _semente(quantidade: int) -> dict:
    return {
        "status": "ok",
        "estabelecimentos": [
            {
                "cnpj": _cnpj(i),
                "razao_social": f"Clinica {i}",
                "uf": "SP",
                "municipio_codigo_rfb": "7107",
                "situacao_cadastral": "02",
            }
            for i in range(quantidade)
        ],
    }


def _criar_lead(db, cnpj: str, status: str = KanbanStatus.NOVO_LEAD.value) -> None:
    db.add(Lead(cnpj=cnpj, razao_social=f"Existente {cnpj}", kanban_status=status))
    db.commit()


def _criar_busca(db) -> BuscaLeads:
    admin = User(email="admin@minotto.com.br", senha_hash="hash", role=UserRole.ADMIN.value, ativo=True)
    db.add(admin)
    db.commit()
    busca = BuscaLeads(iniciado_por_id=admin.id, status=StatusBusca.EXECUTANDO.value)
    db.add(busca)
    db.commit()
    db.refresh(busca)
    return busca


def _rodar_busca(monkeypatch, db, semente: dict) -> tuple[dict, list[str]]:
    processados: list[str] = []

    def fake_pipeline(cnpj, razao_social=None, cache_cnes=None):
        processados.append(cnpj)
        return {"cnpj": cnpj, "lead_id": "x"}

    monkeypatch.setattr(celery_app, "gerar_semente_leads", lambda **kw: semente)
    monkeypatch.setattr(celery_app, "process_lead_pipeline", fake_pipeline)
    busca = _criar_busca(db)
    return celery_app.executar_busca_mensal(busca.id, db=db), processados


# --- funções puras --------------------------------------------------------


def test_normalizar_cnpj_deixa_so_digitos():
    assert normalizar_cnpj("12.345.678/0001-90") == "12345678000190"
    assert normalizar_cnpj("12345678000190") == "12345678000190"
    assert normalizar_cnpj(None) == ""


@pytest.mark.parametrize(
    "na_base, no_candidato",
    [
        ("12.345.678/0001-90", "12345678000190"),  # base com máscara, semente crua
        ("12345678000190", "12.345.678/0001-90"),  # o inverso
    ],
)
def test_cnpj_com_e_sem_mascara_casam(na_base, no_candidato):
    candidatos = [{"cnpj": no_candidato}, {"cnpj": "99999999000199"}]
    novos, excluidos = excluir_cnpjs_ja_na_base(candidatos, {na_base})
    assert excluidos == 1
    assert [c["cnpj"] for c in novos] == ["99999999000199"]


def test_exclusao_nao_muta_a_entrada():
    candidatos = [{"cnpj": "1"}, {"cnpj": "2"}]
    excluir_cnpjs_ja_na_base(candidatos, {"1"})
    assert len(candidatos) == 2


def test_composicao_registra_excluidos_mesmo_com_lote_vazio():
    assert resumir_composicao([], excluidos_ja_na_base=7)["excluidos_ja_na_base"] == 7


# --- executar_busca_mensal ------------------------------------------------


def test_cnpj_ja_existente_nao_e_processado(monkeypatch, db_session, redis_fake):
    _criar_lead(db_session, _cnpj(1))

    resultado, processados = _rodar_busca(monkeypatch, db_session, _semente(3))

    assert _cnpj(1) not in processados
    assert sorted(processados) == [_cnpj(0), _cnpj(2)]
    assert resultado["composicao_selecao"]["excluidos_ja_na_base"] == 1


def test_exclusao_acontece_antes_do_corte_e_cota_continua_cheia(monkeypatch, db_session, redis_fake):
    """
    70 candidatos, cota padrão de 60. Os 10 que JÁ estão na base são
    justamente os de maior dívida -- seriam os primeiros do lote. Se a
    exclusão viesse depois do corte, o lote sairia com 50; vindo antes,
    sai com 60, todos novos.

    Cota fixada aqui (50 x 1.2) em vez de lida do `.env`: o de
    desenvolvimento costuma ter `LEADS_POR_BUSCA` reduzido.
    """
    monkeypatch.setattr(celery_app.settings, "LEADS_POR_BUSCA", 50)
    monkeypatch.setattr(celery_app.settings, "LEADS_MARGEM_PRE_SELECAO", 1.2)
    assert celery_app._top_n_pre_selecao() == 60
    for i in range(70):
        redis_fake.registrar_divida(_cnpj(i), 100 - i)  # 0..9 têm mais dívida
    for i in range(10):
        _criar_lead(db_session, _cnpj(i))

    resultado, processados = _rodar_busca(monkeypatch, db_session, _semente(70))

    assert len(processados) == 60
    assert not set(processados) & {_cnpj(i) for i in range(10)}
    assert set(processados) == {_cnpj(i) for i in range(10, 70)}
    assert resultado["total_cnpjs_selecionados"] == 60
    assert resultado["composicao_selecao"]["excluidos_ja_na_base"] == 10
    assert not any(e.startswith("Lote incompleto") for e in resultado["erros"])


@pytest.mark.parametrize(
    "status",
    [KanbanStatus.PERDIDO.value, KanbanStatus.GANHO.value, KanbanStatus.NEGOCIACAO.value],
)
def test_leads_em_qualquer_etapa_inclusive_perdido_e_ganho_sao_excluidos(
    monkeypatch, db_session, redis_fake, status
):
    _criar_lead(db_session, _cnpj(0), status=status)

    _, processados = _rodar_busca(monkeypatch, db_session, _semente(2))

    assert processados == [_cnpj(1)]


def test_lead_gravado_com_mascara_casa_com_semente_sem_mascara(monkeypatch, db_session, redis_fake):
    _criar_lead(db_session, _com_mascara(_cnpj(0)))

    _, processados = _rodar_busca(monkeypatch, db_session, _semente(2))

    assert processados == [_cnpj(1)]


def test_aviso_quando_nao_sobram_candidatos_novos_suficientes(monkeypatch, db_session, redis_fake):
    for i in range(3):
        _criar_lead(db_session, _cnpj(i))

    resultado, processados = _rodar_busca(monkeypatch, db_session, _semente(5))

    assert len(processados) == 2
    avisos = [e for e in resultado["erros"] if e.startswith("Lote incompleto")]
    assert len(avisos) == 1
    assert "3 candidatos" in avisos[0]
    # é aviso, não falha
    assert resultado["status"] == StatusBusca.CONCLUIDO.value


def test_falha_ao_ler_a_base_aborta_sem_gastar(monkeypatch, db_session, redis_fake):
    def explode(_db):
        raise RuntimeError("banco fora")

    monkeypatch.setattr(celery_app, "_cnpjs_ja_na_base", explode)

    resultado, processados = _rodar_busca(monkeypatch, db_session, _semente(3))

    assert processados == []
    assert resultado["status"] == StatusBusca.ERRO.value


# --- dry-run --------------------------------------------------------------


def _carregar_script():
    caminho = Path(__file__).resolve().parent.parent / "scripts" / "simular_selecao.py"
    spec = importlib.util.spec_from_file_location("simular_selecao", caminho)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


def test_dry_run_exclui_repetidos_nao_grava_nada_e_intersecao_zero(monkeypatch, db_session, redis_fake):
    script = _carregar_script()
    for i in range(6):
        redis_fake.registrar_divida(_cnpj(i), 10 - i)
    _criar_lead(db_session, _cnpj(0), status=KanbanStatus.GANHO.value)
    _criar_lead(db_session, _com_mascara(_cnpj(1)), status=KanbanStatus.PERDIDO.value)

    def proibido(*a, **kw):
        raise AssertionError("dry-run não pode chamar etapa paga")

    monkeypatch.setattr(celery_app, "process_lead_pipeline", proibido)
    leads_antes = db_session.query(Lead).count()

    r = script.simular(db_session, redis_fake, top_n=3, semente=_semente(6))

    assert r["total_candidatos"] == 6
    assert r["excluidos_ja_na_base"] == 2
    assert [c["cnpj"] for c in r["selecionados"]] == [_cnpj(2), _cnpj(3), _cnpj(4)]
    assert [c["qtd_inscricoes_ativas"] for c in r["selecionados"]] == [8, 7, 6]
    assert r["intersecao_com_leads"] == []
    assert db_session.query(Lead).count() == leads_antes
    assert db_session.query(BuscaLeads).count() == 0


def test_dry_run_intersecao_detecta_cnpj_mascarado_independente_da_exclusao(db_session):
    """A checagem de interseção não reaproveita o código da exclusão --
    tem que achar sozinha um lead gravado com máscara."""
    script = _carregar_script()
    _criar_lead(db_session, _com_mascara(_cnpj(0)))

    assert script.contar_intersecao_com_leads(db_session, [_cnpj(0), _cnpj(1)]) == [_com_mascara(_cnpj(0))]


def test_dry_run_sem_indice_pgfn_nao_roda(db_session):
    script = _carregar_script()
    redis = FakeRedis()
    redis._store.clear()

    assert script.simular(db_session, redis, top_n=3, semente=_semente(3)) == {"indice_pgfn_populado": False}

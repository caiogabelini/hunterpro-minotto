"""
Exclusão por termo no nome (medicina ocupacional / segurança do trabalho)
antes do corte da pré-seleção (14/10/2026) -- o cliente não quer esse
perfil, e não há CNAE exclusivo pra filtrar na semente.

Reaproveita os fakes de test_exclusao_cnpjs_ja_na_base.py (Redis fake,
SQLite em memória, semente/pipeline falsos).
"""
import pytest
from app.models.busca_leads import BuscaLeads
from app.models.lead import Lead
from app.scoring.pre_selecao import (
    excluir_por_termos_de_nome,
    normalizar_texto_para_termos,
    resumir_composicao,
    termo_que_casa,
)
from app.workers import celery_app

from tests.test_exclusao_cnpjs_ja_na_base import (  # noqa: F401  (fixtures)
    _carregar_script,
    _cnpj,
    _criar_lead,
    _rodar_busca,
    db_session,
    redis_fake,
    sem_arquivos_reais,
)

TERMOS = [
    "OCUPACIONAL",
    "MEDICINA DO TRABALHO",
    "SEGURANCA DO TRABALHO",
    "SAUDE E SEGURANCA",
    "MEDICINA E SEGURANCA",
    "SEG E MED",
]


@pytest.fixture(autouse=True)
def termos_padrao(monkeypatch):
    """Fixa a lista em vez de depender do .env de quem roda."""
    monkeypatch.setattr(celery_app.settings, "TERMOS_EXCLUSAO_NOME_CANDIDATO", list(TERMOS))


def _semente_com_nomes(nomes: dict[int, str], quantidade: int, fantasia: dict[int, str] | None = None) -> dict:
    fantasia = fantasia or {}
    return {
        "status": "ok",
        "estabelecimentos": [
            {
                "cnpj": _cnpj(i),
                "razao_social": nomes.get(i, f"Clinica {i}"),
                "nome_fantasia": fantasia.get(i, ""),
                "uf": "SP",
                "cnae_fiscal_principal": "8630503",
                "municipio_codigo_rfb": "7107",
                "situacao_cadastral": "02",
            }
            for i in range(quantidade)
        ],
    }


# --- funções puras --------------------------------------------------------


@pytest.mark.parametrize(
    "nome",
    [
        "INSTITUTO MULTICLINICA SAUDE OCUPACIONAL LTDA",
        "MED - OCUPACIONAL SERVICOS MEDICOS",
        "PREVIWORK MEDICINA DO TRABALHO LTDA",
        "VOLO SEGURANCA DO TRABALHO EIRELI",
        "PWD SAUDE E SEGURANCA LTDA",
        "CRN MEDICINA E SEGURANCA LTDA",
        "SANTOS FILHO SEG. E MED. DO TRABALHO",
    ],
)
def test_termo_ocupacional_e_excluido(nome):
    assert termo_que_casa([nome], TERMOS) is not None


@pytest.mark.parametrize(
    "nome",
    [
        "CLINICA DE CARDIOLOGIA SAO LUCAS LTDA",
        "CENTRO MEDICO PREOCUPACIONAL LTDA",  # contém as letras, não a palavra
        "INSTITUTO DE MEDICINA INTEGRADA",
        "SEGURA MED CLINICA",  # "SEG E MED" não casa em pedaço de palavra
        "",
    ],
)
def test_clinica_comum_sem_o_termo_passa(nome):
    assert termo_que_casa([nome], TERMOS) is None


@pytest.mark.parametrize(
    "nome",
    [
        "Clínica Saúde Ocupacional",
        "clinica de medicina do trabalho",
        "SEGURANÇA DO TRABALHO E SAÚDE LTDA",
        "Saúde e Segurança Ltda.",
    ],
)
def test_acento_e_caixa_nao_importam(nome):
    assert termo_que_casa([nome], TERMOS) is not None


def test_termo_escrito_com_acento_na_configuracao_tambem_casa():
    assert termo_que_casa(["PWD SEGURANCA DO TRABALHO"], ["Segurança do Trabalho"]) == "SEGURANCA DO TRABALHO"


def test_normalizacao_trata_pontuacao_como_separador():
    assert normalizar_texto_para_termos("Saúde-Ocupacional Ltda.") == "SAUDE OCUPACIONAL LTDA"
    assert normalizar_texto_para_termos("SEG.E MED.") == "SEG E MED"
    assert normalizar_texto_para_termos(None) == ""


def test_nome_fantasia_tambem_e_verificado():
    candidatos = [{"cnpj": "1", "razao_social": "J SILVA SERVICOS MEDICOS LTDA", "nome_fantasia": "Ocupacional Já"}]
    mantidos, excluidos = excluir_por_termos_de_nome(candidatos, TERMOS)
    assert mantidos == []
    assert excluidos[0]["termo_exclusao"] == "OCUPACIONAL"


def test_lista_vazia_desliga_o_filtro_e_entrada_nao_e_mutada():
    candidatos = [{"cnpj": "1", "razao_social": "SAUDE OCUPACIONAL"}]
    mantidos, excluidos = excluir_por_termos_de_nome(candidatos, [])
    assert mantidos == candidatos and excluidos == []
    excluir_por_termos_de_nome(candidatos, TERMOS)
    assert "termo_exclusao" not in candidatos[0]


def test_composicao_separa_os_dois_tipos_de_exclusao():
    c = resumir_composicao([], excluidos_ja_na_base=3, excluidos_por_termo_no_nome=5)
    assert c["excluidos_ja_na_base"] == 3
    assert c["excluidos_por_termo_no_nome"] == 5


# --- executar_busca_mensal ------------------------------------------------


def test_exclusao_por_termo_acontece_antes_do_corte_e_cota_continua_cheia(monkeypatch, db_session, redis_fake):
    """
    80 candidatos, cota 60. Os 10 de maior dívida são medicina ocupacional
    e 5 outros já estão na base. Vindo antes do corte, as duas exclusões
    cedem a vaga: o lote sai com 60, nenhum ocupacional, nenhum repetido.
    """
    monkeypatch.setattr(celery_app.settings, "LEADS_POR_BUSCA", 50)
    monkeypatch.setattr(celery_app.settings, "LEADS_MARGEM_PRE_SELECAO", 1.2)
    for i in range(80):
        redis_fake.registrar_divida(_cnpj(i), 200 - i)
    ocupacionais = {i: f"CLINICA {i} SAUDE OCUPACIONAL LTDA" for i in range(10)}
    for i in range(10, 15):
        _criar_lead(db_session, _cnpj(i))

    resultado, processados = _rodar_busca(monkeypatch, db_session, _semente_com_nomes(ocupacionais, 80))

    assert len(processados) == 60
    assert set(processados) == {_cnpj(i) for i in range(15, 75)}
    composicao = resultado["composicao_selecao"]
    assert composicao["excluidos_por_termo_no_nome"] == 10
    assert composicao["excluidos_ja_na_base"] == 5
    assert not any(e.startswith("Lote incompleto") for e in resultado["erros"])


def test_cnpj_na_base_e_ocupacional_conta_so_como_ja_na_base(monkeypatch, db_session, redis_fake):
    _criar_lead(db_session, _cnpj(0))

    resultado, processados = _rodar_busca(
        monkeypatch, db_session, _semente_com_nomes({0: "X MEDICINA DO TRABALHO", 1: "Y OCUPACIONAL"}, 3)
    )

    assert processados == [_cnpj(2)]
    assert resultado["composicao_selecao"]["excluidos_ja_na_base"] == 1
    assert resultado["composicao_selecao"]["excluidos_por_termo_no_nome"] == 1


def test_leads_ja_na_base_com_termo_nao_sao_alterados(monkeypatch, db_session, redis_fake):
    """O filtro age só sobre candidatos da semente -- não toca em `leads`."""
    db_session.add(Lead(cnpj=_cnpj(0), razao_social="ANTIGA SAUDE OCUPACIONAL", kanban_status="negociacao"))
    db_session.commit()

    _rodar_busca(monkeypatch, db_session, _semente_com_nomes({0: "ANTIGA SAUDE OCUPACIONAL"}, 2))

    lead = db_session.query(Lead).filter_by(cnpj=_cnpj(0)).one()
    assert lead.razao_social == "ANTIGA SAUDE OCUPACIONAL"
    assert lead.kanban_status == "negociacao"


# --- dry-run --------------------------------------------------------------


def test_dry_run_lista_excluidos_por_termo_que_seriam_selecionados(monkeypatch, db_session, redis_fake):
    script = _carregar_script()
    for i in range(8):
        redis_fake.registrar_divida(_cnpj(i), 20 - i)
    nomes = {0: "ALFA SAUDE OCUPACIONAL", 6: "OMEGA MEDICINA DO TRABALHO"}  # 0 no topo, 6 fora do top 3

    def proibido(*a, **kw):
        raise AssertionError("dry-run não pode chamar etapa paga")

    monkeypatch.setattr(celery_app, "process_lead_pipeline", proibido)

    r = script.simular(db_session, redis_fake, top_n=3, semente=_semente_com_nomes(nomes, 8))

    assert r["excluidos_por_termo_no_nome"] == 2
    assert [c["cnpj"] for c in r["selecionados"]] == [_cnpj(1), _cnpj(2), _cnpj(3)]
    seriam = r["excluidos_por_termo_que_seriam_selecionados"]
    assert [c["cnpj"] for c in seriam] == [_cnpj(0)]
    assert seriam[0]["qtd_inscricoes_ativas"] == 20
    assert seriam[0]["cnae_fiscal_principal"] == "8630503"
    assert seriam[0]["termo_exclusao"] == "OCUPACIONAL"
    assert r["cnae_dos_excluidos_por_termo"] == {"8630503": 2}
    assert db_session.query(BuscaLeads).count() == 0

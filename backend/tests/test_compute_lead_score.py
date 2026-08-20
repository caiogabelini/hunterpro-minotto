"""
Testes do cálculo do lead score (app/scoring/compute_lead_score.py).

Testa a função pura `montar_score_lead` com dicts de `enriquecimentos`
sintéticos, no mesmo formato que cada task de app/workers/celery_app.py
devolve — não precisa de Celery, Redis nem rede real rodando.
"""
from app.scoring.compute_lead_score import (
    decisor_identificavel_points,
    montar_score_lead,
    presenca_digital_points,
    rating_google_points,
    rqe_especialista_points,
    unidade_cnpj_ativo_points,
    zona_franca_points,
)
from app.scoring.rules import SCORING_CRITERIA, total_weight

ENRIQUECIMENTOS_MAXIMOS = {
    "enrich_pgfn_divida_ativa": {"qtd_inscricoes_ativas": 6},
    "enrich_zona_franca": True,
    "enrich_cnes": {"encontrado_cnes": True, "cnes_codigo": "1234567"},
    "enrich_receita_federal": {"decisor_nome": "CICLANA ADMINISTRADORA", "situacao_cadastral": "ATIVA"},
    "enrich_site_firecrawl": {
        "scrape_sucesso": True,
        "site_content_resumo": "# Clínica Teste\nAtendemos com RQE 12345.",
        "instagram": "clinicateste",
        "rqe_mencionado": True,
        "rqe_numero": "12345",
        "servicos": ["Cardiologia"],
    },
    "validate_whatsapp": {"whatsapp_ativo": True, "numero_valido": True},
    "enrich_email": {"email_aprovado": True, "email": "contato@clinicateste.com.br"},
}

ENRIQUECIMENTOS_ZERO = {
    "enrich_pgfn_divida_ativa": {"qtd_inscricoes_ativas": 0},
    "enrich_zona_franca": False,
    "enrich_cnes": {"encontrado_cnes": False},
    "enrich_receita_federal": {"decisor_nome": None, "situacao_cadastral": "BAIXADA"},
    "enrich_site_firecrawl": {
        "scrape_sucesso": False,
        "site_content_resumo": "",
        "instagram": None,
        "rqe_mencionado": False,
        "rqe_numero": None,
        "servicos": [],
    },
    "validate_whatsapp": {"whatsapp_ativo": False, "numero_valido": True},
    "enrich_email": {"email_aprovado": False, "email": None},
}


# --- score máximo / score zero ---


def test_score_maximo_bate_100():
    resultado = montar_score_lead(ENRIQUECIMENTOS_MAXIMOS)
    assert resultado.score_total == 100.0


def test_score_maximo_bate_o_peso_total_configurado():
    """Sanity check -- o teto do score tem que ser sempre o total de
    pesos configurado em rules.py, não um 100 hardcoded que dessincroniza
    se os pesos mudarem."""
    resultado = montar_score_lead(ENRIQUECIMENTOS_MAXIMOS)
    assert resultado.score_total == total_weight()


def test_score_zero_quando_nenhum_sinal_positivo():
    resultado = montar_score_lead(ENRIQUECIMENTOS_ZERO)
    assert resultado.score_total == 0.0


def test_score_zero_com_enriquecimentos_vazio():
    """Nenhuma task rodou ainda pro lead -- não deve quebrar, só dar 0
    em tudo."""
    resultado = montar_score_lead({})
    assert resultado.score_total == 0.0
    assert all(d.points == 0.0 for d in resultado.breakdown)


# --- breakdown ---


def test_breakdown_tem_um_item_por_criterio_na_mesma_ordem():
    resultado = montar_score_lead(ENRIQUECIMENTOS_MAXIMOS)
    assert [d.key for d in resultado.breakdown] == [c.key for c in SCORING_CRITERIA]


def test_breakdown_traz_label_peso_e_camada_de_cada_criterio():
    resultado = montar_score_lead(ENRIQUECIMENTOS_MAXIMOS)
    pgfn = next(d for d in resultado.breakdown if d.key == "divida_ativa_pgfn")

    assert pgfn.label == "Dívida ativa PGFN"
    assert pgfn.weight == 20
    assert pgfn.layer.value == "estruturado"
    assert pgfn.points == 20.0


def test_breakdown_pontos_maximos_por_criterio():
    resultado = montar_score_lead(ENRIQUECIMENTOS_MAXIMOS)
    pontos = {d.key: d.points for d in resultado.breakdown}

    assert pontos["divida_ativa_pgfn"] == 20.0
    assert pontos["zona_franca_alc"] == 20.0
    assert pontos["rqe_especialista"] == 15.0
    assert pontos["whatsapp_ativo"] == 10.0
    assert pontos["email_validado"] == 10.0
    assert pontos["presenca_digital"] == 10.0
    assert pontos["decisor_identificavel"] == 10.0
    assert pontos["unidade_cnpj_ativo"] == 5.0
    assert pontos["rating_google"] == 0.0


def test_breakdown_pontos_zerados_por_criterio():
    resultado = montar_score_lead(ENRIQUECIMENTOS_ZERO)
    pontos = {d.key: d.points for d in resultado.breakdown}

    assert all(valor == 0.0 for valor in pontos.values())


# --- soma bate com o total ---


def test_soma_do_breakdown_bate_com_score_total_maximo():
    resultado = montar_score_lead(ENRIQUECIMENTOS_MAXIMOS)
    assert sum(d.points for d in resultado.breakdown) == resultado.score_total


def test_soma_do_breakdown_bate_com_score_total_zero():
    resultado = montar_score_lead(ENRIQUECIMENTOS_ZERO)
    assert sum(d.points for d in resultado.breakdown) == resultado.score_total


def test_soma_do_breakdown_bate_com_score_total_cenario_misto():
    enriquecimentos_mistos = {
        "enrich_pgfn_divida_ativa": {"qtd_inscricoes_ativas": 3},  # 10 de 20
        "enrich_zona_franca": True,  # 20
        "enrich_cnes": {"encontrado_cnes": False},
        "enrich_receita_federal": {"decisor_nome": None, "situacao_cadastral": "ATIVA"},  # 0 + 5
        "enrich_site_firecrawl": {
            "scrape_sucesso": True,
            "site_content_resumo": "conteudo",
            "instagram": None,
            "rqe_mencionado": False,
        },  # presenca_digital: só site -> metade (5); rqe: (False, False) -> 0
        "validate_whatsapp": {"whatsapp_ativo": False},
        "enrich_email": {"email_aprovado": True},  # 10
    }

    resultado = montar_score_lead(enriquecimentos_mistos)

    assert sum(d.points for d in resultado.breakdown) == resultado.score_total
    pontos = {d.key: d.points for d in resultado.breakdown}
    assert pontos["divida_ativa_pgfn"] == 10.0
    assert pontos["zona_franca_alc"] == 20.0
    assert pontos["presenca_digital"] == 5.0
    assert pontos["email_validado"] == 10.0
    assert pontos["whatsapp_ativo"] == 0.0
    assert pontos["decisor_identificavel"] == 0.0
    assert pontos["unidade_cnpj_ativo"] == 5.0


# --- rqe_especialista: as duas fontes divergindo ---


def test_rqe_ia_e_cnes_concordam_positivo_da_peso_cheio():
    assert rqe_especialista_points(rqe_mencionado=True, encontrado_cnes=True) == 15.0


def test_rqe_ia_e_cnes_concordam_negativo_da_zero():
    assert rqe_especialista_points(rqe_mencionado=False, encontrado_cnes=False) == 0.0


def test_rqe_divergencia_so_ia_menciona():
    """IA achou menção a RQE no site, mas o CNPJ não apareceu no CNES --
    vale mais que só o CNES sozinho (ver justificativa no módulo)."""
    pontos = rqe_especialista_points(rqe_mencionado=True, encontrado_cnes=False)
    assert pontos == 10.0  # 2/3 de 15


def test_rqe_divergencia_so_cnes_confirma():
    """CNPJ tem unidade registrada no CNES, mas a IA não achou menção a
    RQE no site -- sinal mais fraco especificamente pra RQE, já que o
    CNES não tem campo de especialidade."""
    pontos = rqe_especialista_points(rqe_mencionado=False, encontrado_cnes=True)
    assert pontos == 5.0  # 1/3 de 15


def test_rqe_divergencia_ia_vale_mais_que_cnes_sozinho():
    """As duas divergências não empatam -- a ordem de confiança
    (IA sozinha > CNES sozinho) é uma decisão explícita desta sessão."""
    so_ia = rqe_especialista_points(rqe_mencionado=True, encontrado_cnes=False)
    so_cnes = rqe_especialista_points(rqe_mencionado=False, encontrado_cnes=True)
    assert so_ia > so_cnes


# --- outras funções de pontuação, isoladas ---


def test_zona_franca_binario():
    assert zona_franca_points(True) == 20.0
    assert zona_franca_points(False) == 0.0


def test_presenca_digital_dois_sinais_peso_cheio():
    assert presenca_digital_points(tem_site=True, tem_instagram=True) == 10.0


def test_presenca_digital_um_sinal_metade():
    assert presenca_digital_points(tem_site=True, tem_instagram=False) == 5.0
    assert presenca_digital_points(tem_site=False, tem_instagram=True) == 5.0


def test_presenca_digital_nenhum_sinal_zero():
    assert presenca_digital_points(tem_site=False, tem_instagram=False) == 0.0


def test_decisor_identificavel_binario():
    assert decisor_identificavel_points("CICLANA ADMINISTRADORA") == 10.0
    assert decisor_identificavel_points(None) == 0.0
    assert decisor_identificavel_points("") == 0.0


def test_unidade_cnpj_ativo_binario_case_insensitive():
    assert unidade_cnpj_ativo_points("ATIVA") == 5.0
    assert unidade_cnpj_ativo_points("ativa") == 5.0
    assert unidade_cnpj_ativo_points("BAIXADA") == 0.0
    assert unidade_cnpj_ativo_points(None) == 0.0


def test_rating_google_sempre_zero_por_causa_do_peso_zero():
    """Peso confirmado em 0 -- mesmo com rating alto, o resultado é 0."""
    assert rating_google_points(4.5) == 0.0
    assert rating_google_points(None) == 0.0
    assert rating_google_points(2.0) == 0.0


def test_rating_google_formula_existe_com_peso_customizado():
    """A fórmula em si (>=4.0 vale peso cheio) só fica visível passando
    um peso diferente de 0 -- confirma que o campo está "calculado
    pra consistência/histórico", não fixado em zero por acidente."""
    assert rating_google_points(4.5, weight=10) == 10.0
    assert rating_google_points(3.9, weight=10) == 0.0

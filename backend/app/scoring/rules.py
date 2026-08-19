"""
Regras do Lead Score — HunterPro Minotto Contabilidade
========================================================

Pesos definidos ao vivo com o Douglas Minotto na sessão de 10/08/2026.
Total: 100 pontos. Nicho: clínicas e consultórios médicos (qualquer
especialidade com RQE), abrangência nacional.

✅ Fórmula da Dívida Ativa PGFN CONFIRMADA com o Douglas (19/08/2026,
via WhatsApp): "sim, era isso mesmo" — 0 a 6 inscrições ativas, pontos
proporcionais até o teto do peso do critério. Ver `pgfn_points()`
abaixo e `compute_pgfn_criterion_score()` para a implementação.

Cada critério é representado por um `ScoringCriterion`, com o peso
máximo que ele pode contribuir e a "camada" de confiabilidade
(estruturado / inferência / validação) — essa classificação já foi
usada na apresentação de kickoff pro Douglas, mantém a mesma linguagem.
"""
from dataclasses import dataclass
from enum import Enum


class SignalLayer(str, Enum):
    """Nível de confiabilidade do sinal — mesma linguagem usada na
    apresentação de kickoff (páginas 02 e 03 do material)."""

    ESTRUTURADO = "estruturado"   # dado direto de fonte oficial (Receita, PGFN, CNES, Google Places)
    INFERENCIA = "inferencia"     # interpretado por IA (leitura de site/redes sociais)
    VALIDACAO = "validacao"       # confirmação de canal (WhatsApp ativo, e-mail entregável)


@dataclass(frozen=True)
class ScoringCriterion:
    key: str
    label: str
    weight: int
    layer: SignalLayer
    source: str
    notes: str = ""


# ---------------------------------------------------------------------------
# Pesos fechados com o Douglas em 10/08/2026 — soma = 100
# ---------------------------------------------------------------------------
SCORING_CRITERIA: list[ScoringCriterion] = [
    ScoringCriterion(
        key="divida_ativa_pgfn",
        label="Dívida ativa PGFN",
        weight=20,
        layer=SignalLayer.ESTRUTURADO,
        source="Dados abertos PGFN (dadosabertos.pgfn.gov.br) — CSV trimestral, cruzado por CNPJ",
        notes=(
            "Fórmula CONFIRMADA com o Douglas em 19/08/2026: "
            "pontos = min(nº inscrições ativas, 6) × (peso ÷ 6). "
            "Reduzido de 40 → 20 na sessão de pesos para abrir espaço para o critério Zona Franca."
        ),
    ),
    ScoringCriterion(
        key="zona_franca_alc",
        label="Área de Livre Comércio / Zona Franca",
        weight=20,
        layer=SignalLayer.ESTRUTURADO,
        source="Lista estática de municípios (Suframa) — ver app/scoring/zona_franca.py",
        notes=(
            "Critério NOVO, pedido pelo Douglas na sessão de 10/08. "
            "É prioridade, não filtro exclusivo — não restringir a busca só a essas cidades."
        ),
    ),
    ScoringCriterion(
        key="rqe_especialista",
        label="RQE / especialista registrado",
        weight=15,
        layer=SignalLayer.INFERENCIA,
        source="Leitura de site/Instagram via IA (Firecrawl + Claude) + cruzamento com CNES",
        notes="RQE não tem base pública em lote — via inferência, reforçada pelo CNES quando disponível.",
    ),
    ScoringCriterion(
        key="whatsapp_ativo",
        label="WhatsApp ativo",
        weight=10,
        layer=SignalLayer.VALIDACAO,
        source="Evolution API",
    ),
    ScoringCriterion(
        key="email_validado",
        label="E-mail validado",
        weight=10,
        layer=SignalLayer.VALIDACAO,
        source="Validação de MX + ZeroBounce",
    ),
    ScoringCriterion(
        key="presenca_digital",
        label="Presença digital forte",
        weight=10,
        layer=SignalLayer.INFERENCIA,
        source="Site + Instagram/LinkedIn ativos, lidos pela IA",
    ),
    ScoringCriterion(
        key="decisor_identificavel",
        label="Decisor identificável",
        weight=10,
        layer=SignalLayer.ESTRUTURADO,
        source="Administrador via Receita Federal / decisor via LinkedIn",
    ),
    ScoringCriterion(
        key="unidade_cnpj_ativo",
        label="CNPJ ativo (unidade)",
        weight=5,
        layer=SignalLayer.ESTRUTURADO,
        source="Situação cadastral via Receita Federal",
        notes=(
            "Reduzido de 20 → 5. Douglas esclareceu que não precisa ser 'múltiplas unidades' "
            "— 1 unidade com CNPJ ativo já é suficiente como pré-requisito."
        ),
    ),
    ScoringCriterion(
        key="rating_google",
        label="Rating Google 4.0+",
        weight=0,
        layer=SignalLayer.ESTRUTURADO,
        source="Google Places API",
        notes="Zerado a pedido do Douglas: 'pra mim não importa'. Mantido no schema para referência futura.",
    ),
]


def total_weight() -> int:
    return sum(c.weight for c in SCORING_CRITERIA)


def get_criterion(key: str) -> ScoringCriterion | None:
    return next((c for c in SCORING_CRITERIA if c.key == key), None)


# ---------------------------------------------------------------------------
# Fórmula da Dívida Ativa PGFN — CONFIRMADA com o Douglas em 19/08/2026
# ---------------------------------------------------------------------------
PGFN_TETO_INSCRICOES = 6


def pgfn_points(qtd_inscricoes_ativas: int, weight: int | None = None) -> float:
    """
    Converte nº de inscrições ativas na PGFN em pontos do critério
    'divida_ativa_pgfn'.

    Fórmula confirmada com o Douglas: pontos sobem proporcionalmente
    até 6 inscrições, onde bate o peso máximo do critério.

        pontos = min(qtd_inscricoes_ativas, 6) × (peso ÷ 6)

    `weight` é opcional — por padrão usa o peso atual configurado em
    SCORING_CRITERIA (20), mas pode ser sobrescrito (por exemplo, se o
    Douglas pedir pra voltar ao peso original de 40 no futuro, sem
    precisar mudar a fórmula).
    """
    if weight is None:
        criterion = get_criterion("divida_ativa_pgfn")
        weight = criterion.weight if criterion else 0

    qtd_efetiva = min(max(qtd_inscricoes_ativas, 0), PGFN_TETO_INSCRICOES)
    return qtd_efetiva * (weight / PGFN_TETO_INSCRICOES)


# Sanity check — roda no import, quebra cedo se alguém mudar peso e esquecer de ajustar o total
assert total_weight() == 100, (
    f"Soma dos pesos do lead score deveria ser 100, está {total_weight()}. "
    "Confirme a mudança com o cliente antes de alterar."
)

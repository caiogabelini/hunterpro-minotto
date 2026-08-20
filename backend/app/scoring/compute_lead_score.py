"""
Cálculo do lead score total — combina os resultados de todos os módulos
de enriquecimento nos 9 critérios de app/scoring/rules.py.
================================================================================

Este arquivo existe separado de `rules.py` de propósito: `rules.py` só
tem a fórmula da dívida ativa PGFN, CONFIRMADA com o Douglas em
19/08/2026. As fórmulas dos outros 8 critérios, implementadas aqui, são
decisões desta sessão — plausíveis e documentadas, mas **não
confirmadas com o cliente**. Separar os dois arquivos deixa claro, só
pelo import, o que é contrato fechado e o que ainda pode mudar numa
conversa com o Douglas.

⚠️ PRA CADA CRITÉRIO ABAIXO CUJA REGRA NÃO SAIU 100% FECHADA NAS
REUNIÕES, A DECISÃO TOMADA AQUI ESTÁ COMENTADA NA FUNÇÃO CORRESPONDENTE.
Resumo rápido de onde isso se aplica:
  - `rqe_especialista_points`: como combinar o sinal da IA (menção a RQE
    no site) com o sinal do CNES (unidade de saúde registrada) quando as
    duas fontes divergem — rules.py já registra que "a decisão de como
    combinar os dois no score ainda não foi confirmada com o cliente"
    (mesma nota em app/services/cnes.py). Pontuação graduada por
    combinação, ver `_RQE_FRACAO_POR_COMBINACAO`.
  - `presenca_digital_points`: o critério "Presença digital forte" nunca
    teve uma régua objetiva definida nas reuniões — só a fonte
    ("Site + Instagram/LinkedIn ativos, lidos pela IA"). Regra escolhida
    nesta sessão, ver a função.
  - `rating_google_points`: peso já é 0 (confirmado: "pra mim não
    importa"), mas a fórmula existe pro campo aparecer no breakdown por
    consistência/histórico — `search_google_places` ainda não traz
    rating nenhum (task NotImplementedError), então hoje esse critério
    sempre entra como 0 na prática, independente da fórmula.

Os outros critérios (zona_franca_alc, whatsapp_ativo, email_validado,
decisor_identificavel, unidade_cnpj_ativo) são binários — a fonte de
dado ou confirma o sinal (pontuação cheia) ou não (zero). Não achei
ambiguidade nesses pra registrar.

O QUE ESTE MÓDULO FAZ:
  - Uma função `_points` por critério (mesmo padrão de `pgfn_points` em
    rules.py: recebe o sinal já extraído, não o dict bruto da task;
    `weight` opcional pra sobrescrever o peso configurado)
  - `montar_score_lead(enriquecimentos)` — a combinação: recebe um dict
    com o resultado de cada task de enriquecimento já rodada pra um
    lead (chaveado pelo nome da task, ex.: "enrich_pgfn_divida_ativa"),
    extrai o sinal relevante de cada uma, e devolve o score total (soma
    de `SCORING_CRITERIA[i].weight`, sempre 0-100) + o breakdown
    detalhado por critério (label, peso, pontos, camada de
    confiabilidade) — pro dossiê do lead poder mostrar de onde vieram
    os pontos, não só o total.

O QUE ESTE MÓDULO **NÃO** FAZ (de propósito):
  - Não calcula `prioridade` (A/B/C) — não foi pedido nesta sessão e não
    há régua de corte confirmada com o cliente; ficaria pra uma sessão
    futura, fácil de derivar de `score_total` quando o Douglas definir
    os cortes.
  - Não busca os resultados de enriquecimento sozinho (não bate CNPJ
    nenhum, não lê o banco) — `montar_score_lead` é uma função pura,
    recebe os dados já prontos. Ver `compute_lead_score` em
    app/workers/celery_app.py pra ressalva sobre a orquestração/
    persistência desses resultados ainda não existir.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.scoring.rules import SCORING_CRITERIA, SignalLayer, get_criterion, pgfn_points


def _weight_or_default(key: str, weight: int | None) -> int:
    if weight is not None:
        return weight
    criterion = get_criterion(key)
    return criterion.weight if criterion else 0


def zona_franca_points(is_zona_franca: bool, weight: int | None = None) -> float:
    """Binário: município em Área de Livre Comércio/Zona Franca vale o
    peso cheio; fora dela, zero."""
    weight = _weight_or_default("zona_franca_alc", weight)
    return float(weight) if is_zona_franca else 0.0


# Combinação (rqe_mencionado via IA, encontrado_cnes) -> fração do peso
# do critério. NÃO CONFIRMADO COM O CLIENTE (ver docstring do módulo) —
# decisão desta sessão:
#   - as duas fontes concordam que existe unidade/especialidade médica
#     (True, True) -> confiança alta, peso cheio
#   - só a IA menciona RQE no site, sem reforço estrutural do CNES
#     (True, False) -> inferência sozinha vale mais que confirmação
#     estrutural sozinha, porque é um sinal mais direto (menciona RQE
#     explicitamente) -- 2/3 do peso
#   - só o CNES confirma a unidade de saúde, sem a IA achar menção a RQE
#     no site (False, True) -> sinal mais fraco pra esse critério
#     específico, porque a CNES não tem campo de especialidade (ver
#     app/services/cnes.py) -- confirma "é estabelecimento de saúde",
#     não "tem RQE" -- 1/3 do peso
#   - nenhuma das duas fontes -> zero
_RQE_FRACAO_POR_COMBINACAO: dict[tuple[bool, bool], float] = {
    (True, True): 1.0,
    (True, False): 2 / 3,
    (False, True): 1 / 3,
    (False, False): 0.0,
}


def rqe_especialista_points(rqe_mencionado: bool, encontrado_cnes: bool, weight: int | None = None) -> float:
    """Cruza o sinal de IA (menção a RQE no site) com o sinal do CNES
    (unidade de saúde registrada) — ver `_RQE_FRACAO_POR_COMBINACAO` e a
    ressalva no topo do módulo sobre essa combinação não estar
    confirmada com o cliente."""
    weight = _weight_or_default("rqe_especialista", weight)
    fracao = _RQE_FRACAO_POR_COMBINACAO[(bool(rqe_mencionado), bool(encontrado_cnes))]
    return weight * fracao


def whatsapp_ativo_points(tem_whatsapp: bool, weight: int | None = None) -> float:
    """Binário: WhatsApp confirmado ativo pela Evolution API vale o peso
    cheio; qualquer outra coisa (inativo, número inválido), zero."""
    weight = _weight_or_default("whatsapp_ativo", weight)
    return float(weight) if tem_whatsapp else 0.0


def email_validado_points(email_aprovado: bool, weight: int | None = None) -> float:
    """Binário: e-mail aprovado (ZeroBounce "valid"/"catch-all", ver
    app/services/email_enrichment.py) vale o peso cheio; qualquer outra
    coisa, zero."""
    weight = _weight_or_default("email_validado", weight)
    return float(weight) if email_aprovado else 0.0


def presenca_digital_points(tem_site: bool, tem_instagram: bool, weight: int | None = None) -> float:
    """
    ⚠️ REGRA NÃO ESPECIFICADA COM EXATIDÃO NAS REUNIÕES — decisão desta
    sessão, pendente de validação com o Douglas se necessário.

    A fonte documentada em rules.py é "Site + Instagram/LinkedIn ativos,
    lidos pela IA" — LinkedIn ainda não tem etapa implementada no
    pipeline (`enrich_linkedin` continua placeholder), então por
    enquanto o critério usa só site + Instagram, os dois vindos de
    `enrich_site_firecrawl`:

      - site COM conteúdo real (scrape com sucesso e markdown não vazio)
        E Instagram encontrado -> peso cheio (presença forte nos dois
        canais)
      - só um dos dois -> metade do peso (presença parcial)
      - nenhum dos dois -> zero

    Quando `enrich_linkedin` existir, essa função deveria virar um
    critério de 3 sinais em vez de 2 — reavaliar a graduação nesse
    momento, não só acrescentar o LinkedIn como um "terço" a mais sem
    conversar com o cliente sobre o peso relativo.
    """
    weight = _weight_or_default("presenca_digital", weight)
    sinais_positivos = sum([bool(tem_site), bool(tem_instagram)])

    if sinais_positivos == 2:
        return float(weight)
    if sinais_positivos == 1:
        return weight / 2
    return 0.0


def decisor_identificavel_points(decisor_nome: str | None, weight: int | None = None) -> float:
    """Binário: um nome de decisor veio da Receita Federal
    (`identificar_decisor`, ver app/services/receita_federal.py) vale o
    peso cheio; sem nome nenhum, zero."""
    weight = _weight_or_default("decisor_identificavel", weight)
    return float(weight) if decisor_nome else 0.0


def unidade_cnpj_ativo_points(situacao_cadastral: str | None, weight: int | None = None) -> float:
    """Binário: situação cadastral "ATIVA" na Receita Federal vale o
    peso cheio; qualquer outra situação (ou ausente), zero. Comparação
    sem diferenciar maiúscula/minúscula — o valor vem de
    `descricao_situacao_cadastral` da BrasilAPI, observado como "ATIVA"
    maiúsculo, mas não custa ser tolerante a variação de caixa."""
    weight = _weight_or_default("unidade_cnpj_ativo", weight)
    ativa = bool(situacao_cadastral) and situacao_cadastral.strip().upper() == "ATIVA"
    return float(weight) if ativa else 0.0


def rating_google_points(rating: float | None, weight: int | None = None) -> float:
    """
    Peso já confirmado em 0 ("pra mim não importa", Douglas) — essa
    função existe só pro critério aparecer calculado no breakdown, por
    consistência/histórico (ver rules.py). `search_google_places` ainda
    não traz rating nenhum (`NotImplementedError`), então `rating` chega
    None na prática hoje — a fórmula (rating >= 4.0 -> peso cheio) é só
    pra quando essa fonte existir; com peso 0 o resultado é sempre 0
    de qualquer forma.
    """
    weight = _weight_or_default("rating_google", weight)
    if rating is None:
        return 0.0
    return float(weight) if rating >= 4.0 else 0.0


@dataclass
class CriterionScoreDetail:
    """Um critério já pontuado pra um lead específico — o que o dossiê
    do lead precisa pra mostrar "de onde vieram os pontos"."""

    key: str
    label: str
    weight: int
    layer: SignalLayer
    points: float


@dataclass
class LeadScoreResult:
    score_total: float
    breakdown: list[CriterionScoreDetail]


def montar_score_lead(enriquecimentos: dict) -> LeadScoreResult:
    """
    Combina o resultado de cada task de enriquecimento (chaveado pelo
    nome da task — ver app/workers/celery_app.py) nos 9 critérios de
    `SCORING_CRITERIA`, devolvendo o score total (soma dos pesos, sempre
    0-100 já que os pesos somam 100) e o breakdown por critério.

    Chaves esperadas em `enriquecimentos` (todas opcionais — uma task
    que ainda não rodou pro lead, ou que falhou, simplesmente conta como
    "sem sinal" pro(s) critério(s) correspondente(s), não quebra o
    cálculo):
      - "enrich_pgfn_divida_ativa": dict com "qtd_inscricoes_ativas"
      - "enrich_zona_franca": bool
      - "enrich_cnes": dict com "encontrado_cnes"
      - "enrich_receita_federal": dict com "decisor_nome", "situacao_cadastral"
      - "enrich_site_firecrawl": dict com "rqe_mencionado", "scrape_sucesso",
        "site_content_resumo", "instagram"
      - "validate_whatsapp": dict com "whatsapp_ativo"
      - "enrich_email": dict com "email_aprovado"
      - "search_google_places": dict com "rating" (task ainda não
        implementada — sempre ausente hoje, ver rating_google_points)
    """
    pgfn = enriquecimentos.get("enrich_pgfn_divida_ativa") or {}
    zona_franca = enriquecimentos.get("enrich_zona_franca")
    cnes = enriquecimentos.get("enrich_cnes") or {}
    receita_federal = enriquecimentos.get("enrich_receita_federal") or {}
    site_firecrawl = enriquecimentos.get("enrich_site_firecrawl") or {}
    whatsapp = enriquecimentos.get("validate_whatsapp") or {}
    email = enriquecimentos.get("enrich_email") or {}
    google_places = enriquecimentos.get("search_google_places") or {}

    tem_site = bool(site_firecrawl.get("scrape_sucesso")) and bool(site_firecrawl.get("site_content_resumo"))

    pontos_por_criterio: dict[str, float] = {
        "divida_ativa_pgfn": pgfn_points(pgfn.get("qtd_inscricoes_ativas", 0)),
        "zona_franca_alc": zona_franca_points(bool(zona_franca)),
        "rqe_especialista": rqe_especialista_points(
            rqe_mencionado=bool(site_firecrawl.get("rqe_mencionado")),
            encontrado_cnes=bool(cnes.get("encontrado_cnes")),
        ),
        "whatsapp_ativo": whatsapp_ativo_points(bool(whatsapp.get("whatsapp_ativo"))),
        "email_validado": email_validado_points(bool(email.get("email_aprovado"))),
        "presenca_digital": presenca_digital_points(
            tem_site=tem_site,
            tem_instagram=bool(site_firecrawl.get("instagram")),
        ),
        "decisor_identificavel": decisor_identificavel_points(receita_federal.get("decisor_nome")),
        "unidade_cnpj_ativo": unidade_cnpj_ativo_points(receita_federal.get("situacao_cadastral")),
        "rating_google": rating_google_points(google_places.get("rating")),
    }

    breakdown = [
        CriterionScoreDetail(
            key=criterion.key,
            label=criterion.label,
            weight=criterion.weight,
            layer=criterion.layer,
            points=pontos_por_criterio[criterion.key],
        )
        for criterion in SCORING_CRITERIA
    ]

    return LeadScoreResult(score_total=sum(d.points for d in breakdown), breakdown=breakdown)

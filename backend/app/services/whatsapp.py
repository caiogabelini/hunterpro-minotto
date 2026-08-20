"""
Validação de WhatsApp ativo via Evolution API.
================================================================================

Endpoint e contrato já validados em produção no projeto HunterPro SR
Contábil — implementado aqui seguindo exatamente esse formato:

  POST {EVOLUTION_URL}/chat/whatsappNumbers/{EVOLUTION_INSTANCE}
  Header: apikey: {EVOLUTION_KEY}
  Body:   {"numbers": ["<número formatado>"]}
  Resposta: lista de objetos, um por número consultado, com pelo menos
            `exists` (bool) e `jid` (WhatsApp ID, presente só se existir).

O QUE ESTE MÓDULO FAZ:
  - `formatar_numero_whatsapp` normaliza o telefone antes de bater na API
    (ver ressalva abaixo sobre a heurística de código do país)
  - `check_whatsapp` consulta a Evolution API pro número já formatado

⚠️ HEURÍSTICA DE CÓDIGO DO PAÍS — POR QUE É POR TAMANHO, NÃO POR PREFIXO:
o pedido original era "adiciona 55 se não tiver", mas checar só se o
número já *começa* com "55" é ambíguo: DDD 55 é do Rio Grande do Sul, um
número local de 11 dígitos como "55991234567" já começa com "55" sem ter
o código do país. `formatar_numero_whatsapp` decide pelo TAMANHO do
número já limpo (só dígitos): 10 ou 11 dígitos = número nacional (DDD +
fixo/celular) sem código do país, sempre prefixa 55; qualquer outro
tamanho é tratado como já tendo (ou nunca vindo a ter) o código do país.
Resultado final tem que ficar em 12-13 dígitos (55 + DDD de 2 + 8 ou 9
dígitos do número) pra ser considerado válido — fora disso, não bate na
API (não faz sentido gastar uma consulta com número claramente errado).

O QUE ESTE MÓDULO **NÃO** FAZ (de propósito):
  - Não calcula pontos de score — devolve só o sinal bruto (`tem_whatsapp`);
    a conversão pro critério `whatsapp_ativo` (rules.py) fica pro
    `compute_lead_score`, ainda não implementado.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

import httpx

from app.core.config import settings

WHATSAPP_NUMBERS_PATH = "/chat/whatsappNumbers/{instance}"
DEFAULT_TIMEOUT = 15.0


@dataclass
class WhatsappCheckResult:
    """Resultado da checagem de um número. `numero_formatado` fica vazio
    e `tem_whatsapp` fica False sem bater na API se `numero_valido` for
    False (número não normalizável pra um formato plausível)."""

    numero_formatado: str
    numero_valido: bool
    tem_whatsapp: bool
    jid: str | None = None


def formatar_numero_whatsapp(telefone: str | None) -> str | None:
    """
    Remove tudo que não é dígito, adiciona o código do país (55) quando o
    número tem cara de nacional (10 ou 11 dígitos — ver ressalva no topo
    do módulo sobre por que é por tamanho, não por prefixo), e valida que
    o resultado final tem 12-13 dígitos. Devolve None se não der pra
    chegar num formato plausível.
    """
    digitos = re.sub(r"\D", "", telefone or "")
    if not digitos:
        return None

    if len(digitos) in (10, 11):
        digitos = f"55{digitos}"

    if len(digitos) not in (12, 13):
        return None

    return digitos


def _get_client(client: httpx.Client | None) -> tuple[httpx.Client, bool]:
    """Mesmo padrão de client injetável usado em app/services/cnes.py."""
    if client is not None:
        return client, False
    return httpx.Client(base_url=settings.EVOLUTION_URL, timeout=DEFAULT_TIMEOUT), True


def check_whatsapp(telefone: str, client: httpx.Client | None = None) -> WhatsappCheckResult:
    """
    Formata o telefone e, se válido, consulta a Evolution API pra saber
    se o número tem WhatsApp ativo. Números que não formatam pra um
    formato plausível não batem na API — devolvem `numero_valido=False`
    direto.
    """
    numero = formatar_numero_whatsapp(telefone)
    if numero is None:
        return WhatsappCheckResult(numero_formatado="", numero_valido=False, tem_whatsapp=False)

    client, owns_client = _get_client(client)

    try:
        resp = client.post(
            WHATSAPP_NUMBERS_PATH.format(instance=settings.EVOLUTION_INSTANCE),
            json={"numbers": [numero]},
            headers={"apikey": settings.EVOLUTION_KEY},
        )
        resp.raise_for_status()
        resultados = resp.json()
        primeiro = resultados[0] if resultados else {}

        return WhatsappCheckResult(
            numero_formatado=numero,
            numero_valido=True,
            tem_whatsapp=bool(primeiro.get("exists")),
            jid=primeiro.get("jid"),
        )
    finally:
        if owns_client:
            client.close()

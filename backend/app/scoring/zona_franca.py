"""
Critério "Área de Livre Comércio / Zona Franca" — lead score Minotto.

Diferente da PGFN e do CNES, este critério NÃO depende de nenhuma base
de dados externa, API ou download. É uma lista fixa de municípios
contemplados por Área de Livre Comércio (ALC), publicada pela Suframa.

Fonte: https://www.gov.br/suframa/pt-br/assuntos/invest-pt/alc
(consultado em 10/08/2026 — revisar periodicamente caso a Suframa
inclua novos municípios).

Uso: comparar o município do lead (já capturado via Receita Federal/
CNPJ no enriquecimento padrão) contra esta lista. Sem custo de API,
sem sincronização externa.

Nota de produto (10/08/2026): é critério de PRIORIZAÇÃO, não de
exclusão — o Douglas foi explícito que não quer restringir a busca
só a essas cidades.

Possível refinamento futuro (não bloqueia o MVP): a Suframa mantém um
cadastro próprio (CADSUF) que identifica quem de fato usufrui dos
benefícios fiscais, não só quem está localizado no município — sinal
mais preciso, mas não investigado ainda (pode exigir credencial).
"""

# (município, UF)
MUNICIPIOS_ALC: set[tuple[str, str]] = {
    ("Boa Vista", "RR"),
    ("Bonfim", "RR"),
    ("Pacaraima", "RR"),
    ("Tabatinga", "AM"),
    ("Macapá", "AP"),
    ("Santana", "AP"),
    ("Guajará-Mirim", "RO"),
    ("Brasiléia", "AC"),
    ("Epitaciolândia", "AC"),
    ("Cruzeiro do Sul", "AC"),
}


def _normalize(text: str) -> str:
    return text.strip().upper()


def is_zona_franca(municipio: str, uf: str) -> bool:
    """Retorna True se o município/UF do lead está numa Área de Livre Comércio."""
    alvo = (_normalize(municipio), _normalize(uf))
    return any((_normalize(m), _normalize(u)) == alvo for m, u in MUNICIPIOS_ALC)

#!/usr/bin/env python3
"""
Dry-run da pré-seleção da busca mensal — HunterPro Minotto Contabilidade.

Roda EXATAMENTE a fase gratuita de `executar_busca_mensal` (semente da
Receita + índice PGFN no Redis + Zona Franca + exclusão de quem já está
em `leads` + corte no top N) e imprime o lote que SERIA processado.

  - NENHUMA chamada paga: não chama `process_lead_pipeline` nem nenhuma
    etapa de enriquecimento (Google Places, Firecrawl, Anthropic,
    Hunter.io, ZeroBounce, Evolution).
  - NADA é gravado: não cria `BuscaLeads`, não toca em `leads`. No
    Postgres a transação é aberta como READ ONLY, então até um bug que
    tentasse escrever falharia em vez de gravar. O Redis só recebe
    GET/MGET.

Usa a mesma função da task (`montar_selecao_da_busca`), não uma cópia da
lógica -- se o simulador diz que não há repetidos, a busca real também
não terá.

Uso (em produção, antes de disparar a busca no painel):
    cd backend
    python scripts/simular_selecao.py

Saída com código 0 se a interseção do lote com `leads` for 0; 1 se
houver interseção (NÃO dispare a busca); 2 se o índice PGFN não estiver
populado (a busca real abortaria do mesmo jeito).

⚠️ A semente lê o universo nacional dos arquivos da Receita -- leva os
mesmos minutos que a fase 1 da busca real.
"""
import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select, text  # noqa: E402

import app.models  # noqa: F401, E402  (registra todos os modelos -- ver app/models/__init__.py)
from app.core.config import settings  # noqa: E402
from app.core.database import SessionLocal  # noqa: E402
from app.models.lead import Lead  # noqa: E402
from app.scoring.pre_selecao import normalizar_cnpj  # noqa: E402
from app.workers import celery_app  # noqa: E402


def _cnpj_com_mascara(digitos: str) -> str:
    if len(digitos) != 14:
        return digitos
    return f"{digitos[:2]}.{digitos[2:5]}.{digitos[5:8]}/{digitos[8:12]}-{digitos[12:]}"


def contar_intersecao_com_leads(db, cnpjs: list[str]) -> list[str]:
    """
    CNPJs do lote que já existem em `leads`, por uma consulta INDEPENDENTE
    da usada na exclusão (`_cnpjs_ja_na_base`): procura cada CNPJ nas duas
    grafias, crua e com máscara, direto no banco. Conferir a exclusão com
    o mesmo código que a fez seria tautológico.
    """
    if not cnpjs:
        return []
    variantes: set[str] = set()
    for cnpj in cnpjs:
        digitos = normalizar_cnpj(cnpj)
        variantes.update({cnpj, digitos, _cnpj_com_mascara(digitos)})
    encontrados = db.execute(select(Lead.cnpj).where(Lead.cnpj.in_(sorted(variantes)))).scalars().all()
    return sorted(encontrados)


def simular(db, redis_client, top_n: int, semente: dict | None = None) -> dict:
    """
    Núcleo testável do dry-run. `semente=None` lê os arquivos da Receita
    de verdade (`gerar_semente_leads`); os testes injetam uma pronta.
    """
    if not celery_app._indice_pgfn_populado(redis_client):
        return {"indice_pgfn_populado": False}

    if semente is None:
        semente = celery_app.gerar_semente_leads()
    estabelecimentos = semente.get("estabelecimentos") or []

    selecao = celery_app.montar_selecao_da_busca(estabelecimentos, redis_client, db, top_n)
    selecionados = selecao["selecionados"]
    return {
        "indice_pgfn_populado": True,
        "status_semente": semente.get("status"),
        "total_universo": len(estabelecimentos),
        "total_candidatos": selecao["total_candidatos"],
        "excluidos_ja_na_base": selecao["excluidos_ja_na_base"],
        "total_candidatos_novos": selecao["total_candidatos_novos"],
        "top_n": top_n,
        "tabela_municipios_encontrada": bool(selecao["municipios_por_codigo"]),
        "selecionados": selecionados,
        "composicao": selecao["composicao"],
        "intersecao_com_leads": contar_intersecao_com_leads(db, [c.get("cnpj") or "" for c in selecionados]),
    }


def _imprimir(r: dict) -> None:
    print("=" * 78)
    print("DRY-RUN DA PRÉ-SELEÇÃO — nada foi gravado, nenhuma API paga foi chamada")
    print("=" * 78)
    print(f"Semente (status):                   {r['status_semente']}")
    print(f"Total de candidatos no universo:    {r['total_candidatos']}")
    print(f"Excluídos por já estarem na base:   {r['excluidos_ja_na_base']}")
    print(f"Candidatos novos:                   {r['total_candidatos_novos']}")
    print(f"Cota (top N):                       {r['top_n']}")
    print(f"Selecionados:                       {len(r['selecionados'])}")
    if not r["tabela_municipios_encontrada"]:
        print("⚠️  Tabela MUNICCSV ausente: Zona Franca NÃO participa do ranking.")
    c = r["composicao"]
    print(
        f"Composição: {c['por_divida_ativa']} por dívida ativa "
        f"({c['inscricoes_pgfn_min']}–{c['inscricoes_pgfn_max']} inscrições), "
        f"{c['para_completar_cota']} para completar cota, {c['com_zona_franca']} em Zona Franca"
    )
    if len(r["selecionados"]) < r["top_n"]:
        print(f"⚠️  Lote incompleto: só {len(r['selecionados'])} candidatos novos para a cota de {r['top_n']}.")
    print("-" * 78)
    print(f"{'#':>3}  {'CNPJ':<14}  {'PGFN':>5}  {'ZF':<3}  {'fase':<15}  razão social")
    for i, cand in enumerate(r["selecionados"], 1):
        print(
            f"{i:>3}  {cand.get('cnpj', ''):<14}  {cand.get('qtd_inscricoes_ativas', 0):>5}  "
            f"{'sim' if cand.get('zona_franca') else '':<3}  {cand.get('fase_selecao', ''):<15}  "
            f"{(cand.get('razao_social') or '')[:40]}"
        )
    print("-" * 78)
    intersecao = r["intersecao_com_leads"]
    print(f"Interseção do lote com a tabela leads: {len(intersecao)}  (deve ser 0)")
    if intersecao:
        print("🔴 CNPJs do lote que JÁ estão na base -- NÃO dispare a busca:")
        for cnpj in intersecao:
            print(f"   {cnpj}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--top-n",
        type=int,
        default=None,
        help="Tamanho do lote. Padrão: o mesmo da busca real (LEADS_POR_BUSCA x LEADS_MARGEM_PRE_SELECAO).",
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    import redis as redis_lib

    top_n = args.top_n if args.top_n is not None else celery_app._top_n_pre_selecao()
    redis_client = redis_lib.from_url(settings.REDIS_URL)
    db = SessionLocal()
    try:
        if db.get_bind().dialect.name == "postgresql":
            # Garantia no banco, não só na disciplina do código.
            db.execute(text("SET TRANSACTION READ ONLY"))
        resultado = simular(db, redis_client, top_n)
    finally:
        db.rollback()
        db.close()

    if not resultado["indice_pgfn_populado"]:
        print(
            "Índice da PGFN nunca foi populado (pgfn:index_updated_at ausente). "
            "Rode refresh_pgfn_index antes -- a busca real abortaria pelo mesmo motivo.",
            file=sys.stderr,
        )
        return 2

    _imprimir(resultado)
    return 1 if resultado["intersecao_com_leads"] else 0


if __name__ == "__main__":
    sys.exit(main())

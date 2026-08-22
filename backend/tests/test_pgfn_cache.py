"""
Testes da camada de cache Redis do índice PGFN.

Usa um fake client em memória (não precisa de Redis real rodando) —
só implementa o subconjunto da API que save_index_to_redis/
lookup_debt_from_redis realmente usam (get, set, pipeline).
"""
from app.services.pgfn import (
    PgfnDebtSummary,
    lookup_debt_from_redis,
    lookup_debts_from_redis,
    save_index_to_redis,
)


class FakeRedisPipeline:
    def __init__(self, store: dict):
        self._store = store
        self._ops: list[tuple] = []

    def set(self, key, value):
        self._ops.append(("set", key, value))
        return self

    def execute(self):
        for op, key, value in self._ops:
            if op == "set":
                self._store[key] = value
        self._ops.clear()


class FakeRedis:
    """Substituto mínimo do cliente redis-py pros testes."""

    def __init__(self):
        self._store: dict[str, str] = {}

    def pipeline(self):
        return FakeRedisPipeline(self._store)

    def get(self, key):
        val = self._store.get(key)
        return val.encode() if val else None  # redis-py real retorna bytes

    def mget(self, keys):
        # redis-py devolve uma lista do MESMO tamanho da entrada, com None
        # nas posições das chaves ausentes -- lookup_debts_from_redis
        # depende disso pra casar cada resultado com seu CNPJ via zip().
        return [self._store[k].encode() if k in self._store else None for k in keys]

    def set(self, key, value):
        self._store[key] = value


def test_save_and_lookup_roundtrip():
    redis_client = FakeRedis()
    index = {
        "11222333000181": PgfnDebtSummary(
            cnpj="11222333000181", qtd_inscricoes_ativas=3, valor_total=1500.0, algum_ajuizado=True
        ),
    }

    saved = save_index_to_redis(index, redis_client)
    assert saved == 1

    summary = lookup_debt_from_redis("11.222.333/0001-81", redis_client)  # com pontuação
    assert summary is not None
    assert summary.qtd_inscricoes_ativas == 3
    assert summary.valor_total == 1500.0
    assert summary.algum_ajuizado is True


def test_lookup_cnpj_not_in_index_returns_none():
    redis_client = FakeRedis()
    save_index_to_redis({}, redis_client)

    summary = lookup_debt_from_redis("99999999000199", redis_client)
    assert summary is None


def test_lookup_invalid_cnpj_returns_none():
    redis_client = FakeRedis()
    summary = lookup_debt_from_redis("cpf-invalido", redis_client)
    assert summary is None


def test_save_index_sets_updated_at_marker():
    redis_client = FakeRedis()
    save_index_to_redis({}, redis_client)
    assert redis_client.get("pgfn:index_updated_at") is not None


# ---------------------------------------------------------------------------
# lookup_debts_from_redis -- versão em LOTE, usada pela pré-seleção
# (app/scoring/pre_selecao.py) pra consultar o universo inteiro da semente
# sem um round-trip por CNPJ.
# ---------------------------------------------------------------------------


def _indice_de_teste() -> dict[str, PgfnDebtSummary]:
    return {
        "11111111000101": PgfnDebtSummary(
            cnpj="11111111000101", qtd_inscricoes_ativas=3, valor_total=15000.0, algum_ajuizado=False
        ),
        "22222222000102": PgfnDebtSummary(
            cnpj="22222222000102", qtd_inscricoes_ativas=8, valor_total=90000.0, algum_ajuizado=True
        ),
    }


def test_lookup_em_lote_devolve_so_os_cnpjs_com_divida():
    redis_client = FakeRedis()
    save_index_to_redis(_indice_de_teste(), redis_client)

    resultado = lookup_debts_from_redis(
        ["11111111000101", "22222222000102", "33333333000103"], redis_client
    )

    # o terceiro não está em dívida ativa -> simplesmente ausente do dict,
    # mesma semântica do None da versão unitária
    assert set(resultado) == {"11111111000101", "22222222000102"}
    assert resultado["11111111000101"].qtd_inscricoes_ativas == 3
    assert resultado["22222222000102"].algum_ajuizado is True


def test_lookup_em_lote_bate_com_a_versao_unitaria():
    redis_client = FakeRedis()
    save_index_to_redis(_indice_de_teste(), redis_client)
    cnpjs = ["11111111000101", "22222222000102", "33333333000103"]

    lote = lookup_debts_from_redis(cnpjs, redis_client)

    for cnpj in cnpjs:
        unitario = lookup_debt_from_redis(cnpj, redis_client)
        if unitario is None:
            assert cnpj not in lote
        else:
            assert lote[cnpj].qtd_inscricoes_ativas == unitario.qtd_inscricoes_ativas
            assert lote[cnpj].valor_total == unitario.valor_total
            assert lote[cnpj].algum_ajuizado == unitario.algum_ajuizado


def test_lookup_em_lote_normaliza_cnpj_e_ignora_invalidos():
    redis_client = FakeRedis()
    save_index_to_redis(_indice_de_teste(), redis_client)

    resultado = lookup_debts_from_redis(
        ["11.111.111/0001-01", "", "nao-e-cnpj", None], redis_client
    )

    assert set(resultado) == {"11111111000101"}


def test_lookup_em_lote_fatia_em_varios_mgets():
    redis_client = FakeRedis()
    save_index_to_redis(_indice_de_teste(), redis_client)

    chamadas = []
    mget_original = redis_client.mget

    def mget_espiao(keys):
        chamadas.append(list(keys))
        return mget_original(keys)

    redis_client.mget = mget_espiao

    cnpjs = [f"{i:014d}" for i in range(10)] + ["11111111000101"]
    resultado = lookup_debts_from_redis(cnpjs, redis_client, tamanho_lote=4)

    # 11 CNPJs em lotes de 4 -> 3 MGETs, nenhum maior que o tamanho do lote
    assert len(chamadas) == 3
    assert all(len(c) <= 4 for c in chamadas)
    assert set(resultado) == {"11111111000101"}


def test_lookup_em_lote_pula_entrada_corrompida_sem_derrubar_o_resto():
    redis_client = FakeRedis()
    save_index_to_redis(_indice_de_teste(), redis_client)
    # simula uma chave com JSON inválido no índice
    redis_client.set("pgfn:cnpj:33333333000103", "{isso nao e json")

    resultado = lookup_debts_from_redis(
        ["11111111000101", "33333333000103", "22222222000102"], redis_client
    )

    assert set(resultado) == {"11111111000101", "22222222000102"}


def test_lookup_em_lote_com_lista_vazia_devolve_dict_vazio():
    assert lookup_debts_from_redis([], FakeRedis()) == {}

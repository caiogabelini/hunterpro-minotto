"""
Testes da camada de cache Redis do índice PGFN.

Usa um fake client em memória (não precisa de Redis real rodando) —
só implementa o subconjunto da API que save_index_to_redis/
lookup_debt_from_redis realmente usam (get, set, pipeline).
"""
from app.services.pgfn import (
    PgfnDebtSummary,
    lookup_debt_from_redis,
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

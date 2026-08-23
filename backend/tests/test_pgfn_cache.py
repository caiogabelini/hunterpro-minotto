"""
Testes da camada de cache Redis do índice PGFN.

Usa um fake client em memória (não precisa de Redis real rodando) —
só implementa o subconjunto da API que save_index_to_redis/
lookup_debt_from_redis realmente usam (get, set, pipeline).
"""
import pytest

from app.services.pgfn import (
    REDIS_KEY_UPDATED_AT,
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


class FakeRedisPipelineEspiao(FakeRedisPipeline):
    """Registra o tamanho de cada lote executado -- é o que permite
    testar a PROPRIEDADE DE MEMÓRIA (o buffer nunca passa do lote), não
    só o resultado final."""

    def __init__(self, store: dict, lotes: list[int]):
        super().__init__(store)
        self._lotes = lotes

    def execute(self):
        self._lotes.append(len(self._ops))
        super().execute()


class FakeRedisEspiao:
    """FakeRedis que expõe `lotes_executados`."""

    def __init__(self):
        self._store: dict[str, str] = {}
        self.lotes_executados: list[int] = []

    def pipeline(self):
        return FakeRedisPipelineEspiao(self._store, self.lotes_executados)

    def get(self, key):
        val = self._store.get(key)
        return val.encode() if val else None

    def mget(self, keys):
        return [self._store[k].encode() if k in self._store else None for k in keys]

    def set(self, key, value):
        self._store[key] = value


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


# ---------------------------------------------------------------------------
# Escrita em lotes (23/08/2026)
# ---------------------------------------------------------------------------
# Medido contra o arquivo real: empilhar os 6.432.808 comandos num único
# pipeline custava 713 MB de buffer, em cima dos ~4 GB do índice --
# levando o processo ao pico de 4,74 GB e ao OOM numa VPS com 4,2 GB
# livres. O `pipeline()` do redis-py só envia no `execute()`.


def _indice(n: int) -> dict:
    return {
        f"{i:014d}": PgfnDebtSummary(
            cnpj=f"{i:014d}", qtd_inscricoes_ativas=i % 7 + 1,
            valor_total=float(i) * 1.5, algum_ajuizado=(i % 2 == 0),
        )
        for i in range(n)
    }


def test_buffer_nunca_passa_do_tamanho_do_lote():
    """A propriedade que de fato importa pra memória -- não o resultado
    final, e sim o pico do buffer durante a escrita."""
    redis_client = FakeRedisEspiao()
    save_index_to_redis(_indice(23), redis_client, tamanho_lote=5)
    assert max(redis_client.lotes_executados) <= 5


def test_todos_os_cnpjs_sao_persistidos_com_batching():
    redis_client = FakeRedisEspiao()
    index = _indice(23)
    assert save_index_to_redis(index, redis_client, tamanho_lote=5) == 23
    for cnpj, resumo in index.items():
        gravado = lookup_debt_from_redis(cnpj, redis_client)
        assert gravado is not None
        assert gravado.qtd_inscricoes_ativas == resumo.qtd_inscricoes_ativas
        assert gravado.valor_total == pytest.approx(resumo.valor_total)
        assert gravado.algum_ajuizado == resumo.algum_ajuizado


def test_resultado_identico_qualquer_que_seja_o_lote():
    """O batching muda só COMO escreve, não O QUE escreve."""
    index = _indice(23)
    store_por_lote = {}
    for lote in (1, 5, 23, 1000):
        redis_client = FakeRedisEspiao()
        save_index_to_redis(index, redis_client, tamanho_lote=lote)
        store_por_lote[lote] = {
            k: v for k, v in redis_client._store.items() if k != REDIS_KEY_UPDATED_AT
        }
    referencia = store_por_lote[1]
    for lote, store in store_por_lote.items():
        assert store == referencia, f"lote {lote} gravou algo diferente"


def test_carimbo_de_atualizacao_gravado_quando_o_total_e_multiplo_exato():
    """Caso de borda: com 10 itens e lote 5, `pendentes` zera no último
    item e o pipeline fica limpo -- o carimbo ainda tem que sair."""
    redis_client = FakeRedisEspiao()
    save_index_to_redis(_indice(10), redis_client, tamanho_lote=5)
    assert redis_client.get(REDIS_KEY_UPDATED_AT) is not None


def test_indice_vazio_ainda_grava_o_carimbo():
    """`_indice_pgfn_populado` (trava de custo em executar_busca_mensal)
    depende desse carimbo existir."""
    redis_client = FakeRedisEspiao()
    assert save_index_to_redis({}, redis_client, tamanho_lote=5) == 0
    assert redis_client.get(REDIS_KEY_UPDATED_AT) is not None


def test_lote_padrao_espelha_o_da_leitura():
    from app.services.pgfn import LOTE_ESCRITA_PADRAO, LOTE_LOOKUP_PADRAO
    assert LOTE_ESCRITA_PADRAO == LOTE_LOOKUP_PADRAO == 5_000


def test_leitura_em_lote_nao_regride_com_o_batching_na_escrita():
    """lookup_debts_from_redis (usado pela pré-seleção sobre o universo
    inteiro) tem que continuar igual."""
    redis_client = FakeRedisEspiao()
    index = _indice(23)
    save_index_to_redis(index, redis_client, tamanho_lote=5)

    encontrados = lookup_debts_from_redis(list(index) + ["99999999999999"], redis_client)
    assert len(encontrados) == 23
    assert "99999999999999" not in encontrados
    for cnpj, resumo in index.items():
        assert encontrados[cnpj].qtd_inscricoes_ativas == resumo.qtd_inscricoes_ativas


def test_summary_nao_tem_mais_o_campo_situacoes():
    """Guarda da remoção: o campo era escrito a cada inscrição e nunca
    lido -- ~724 MB no arquivo real."""
    resumo = PgfnDebtSummary(cnpj="11222333000181", qtd_inscricoes_ativas=1, valor_total=10.0)
    assert not hasattr(resumo, "situacoes")

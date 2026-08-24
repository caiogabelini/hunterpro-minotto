"""
Proteções do login: tempo constante + limite de tentativas (24/08/2026).
================================================================================

Achados da auditoria de segurança:

  1. **Oráculo de timing.** O curto-circuito do `or` fazia e-mail
     inexistente retornar ANTES do bcrypt: 7,7 ms contra 459,4 ms de uma
     senha errada -- 59x. Dava pra enumerar quais e-mails têm cadastro só
     cronometrando, anulando a mensagem de erro genérica que a rota usa
     justamente pra não vazar isso.
  2. **Nenhum rate limiting.** Nem slowapi, nem `limit_req` no nginx, nem
     contagem no código -- senhas podiam ser testadas indefinidamente.
"""
import statistics
import time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.core.database import Base, get_db
from app.core.rate_limit import PREFIXO_CHAVE_LOGIN, get_redis
from app.core.security import hash_password
from app.main import app
from app.models.user import User, UserRole

EMAIL = "real@x.com"
SENHA = "senhaCorreta123"


class FakeRedis:
    """Substituto mínimo do redis-py pro contador de tentativas.

    `expira_em` guarda o TTL aplicado, pra o teste conferir que a janela
    é armada -- sem isso o contador ficaria eterno e o bloqueio nunca
    liberaria."""

    def __init__(self):
        self.store: dict[str, int] = {}
        self.expira_em: dict[str, int] = {}

    def get(self, k):
        v = self.store.get(k)
        return str(v).encode() if v is not None else None

    def delete(self, k):
        self.store.pop(k, None)
        self.expira_em.pop(k, None)

    def pipeline(self):
        return _FakePipeline(self)


class _FakePipeline:
    def __init__(self, redis):
        self._redis = redis
        self._ops = []

    def incr(self, k):
        self._ops.append(("incr", k, None))
        return self

    def expire(self, k, segundos):
        self._ops.append(("expire", k, segundos))
        return self

    def execute(self):
        for op, k, valor in self._ops:
            if op == "incr":
                self._redis.store[k] = self._redis.store.get(k, 0) + 1
            else:
                self._redis.expira_em[k] = valor
        self._ops.clear()


class RedisQuebrado:
    """Simula Redis fora do ar -- tudo levanta."""

    def _falha(self, *a, **kw):
        raise ConnectionError("Redis indisponível")

    get = delete = pipeline = _falha


@pytest.fixture
def db_session():
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    sessao = sessionmaker(bind=engine)()
    sessao.add(User(email=EMAIL, senha_hash=hash_password(SENHA), role=UserRole.ADMIN.value))
    sessao.commit()
    app.dependency_overrides[get_db] = lambda: sessao
    yield sessao
    app.dependency_overrides.clear()


@pytest.fixture
def redis_fake(db_session):
    fake = FakeRedis()
    app.dependency_overrides[get_redis] = lambda: fake
    return fake


@pytest.fixture
def client():
    return TestClient(app)


def _login(client, email, senha):
    return client.post("/api/auth/login", json={"email": email, "senha": senha})


# ---------------------------------------------------------------------------
# 1 -- Tempo de resposta constante
# ---------------------------------------------------------------------------

def test_tempo_de_resposta_nao_revela_se_o_email_existe(client, redis_fake):
    """A regressão central. Limite generoso (5x) de propósito: o objetivo
    é matar a discrepância de ORDEM DE GRANDEZA (59x), não perseguir
    igualdade exata -- isso deixaria o teste instável em CI compartilhada.
    Qualquer volta do curto-circuito reaparece como dezenas de vezes."""
    def medir(email, senha, n=7):
        amostras = []
        for _ in range(n):
            redis_fake.store.clear()  # não deixar o rate limit interferir
            inicio = time.perf_counter()
            _login(client, email, senha)
            amostras.append(time.perf_counter() - inicio)
        return statistics.median(amostras)

    inexistente = medir("naoexiste@x.com", "qualquer")
    senha_errada = medir(EMAIL, "senhaErrada")

    razao = max(inexistente, senha_errada) / min(inexistente, senha_errada)
    assert razao < 5, (
        f"discrepância de {razao:.1f}x entre e-mail inexistente e senha errada "
        "-- o bcrypt precisa rodar nos dois caminhos"
    )


def test_usuario_inativo_tambem_roda_o_hash(client, db_session, redis_fake):
    """Inativo não pode retornar cedo: seria um terceiro tempo, e um
    oráculo pra 'esta conta existe mas está desativada'."""
    db_session.add(User(email="inativo@x.com", senha_hash=hash_password(SENHA),
                        role=UserRole.CLIENT.value, ativo=False))
    db_session.commit()

    def medir(email, senha, n=5):
        amostras = []
        for _ in range(n):
            redis_fake.store.clear()
            inicio = time.perf_counter()
            _login(client, email, senha)
            amostras.append(time.perf_counter() - inicio)
        return statistics.median(amostras)

    inativo = medir("inativo@x.com", SENHA)
    inexistente = medir("naoexiste@x.com", SENHA)
    assert max(inativo, inexistente) / min(inativo, inexistente) < 5


def test_mensagem_continua_generica_nos_tres_casos(client, db_session, redis_fake):
    db_session.add(User(email="inativo@x.com", senha_hash=hash_password(SENHA),
                        role=UserRole.CLIENT.value, ativo=False))
    db_session.commit()

    mensagens = set()
    for email, senha in [("naoexiste@x.com", SENHA), (EMAIL, "errada"), ("inativo@x.com", SENHA)]:
        redis_fake.store.clear()
        r = _login(client, email, senha)
        assert r.status_code == 401
        mensagens.add(r.json()["detail"])
    assert len(mensagens) == 1, "as três falhas têm que devolver a MESMA mensagem"


def test_login_correto_continua_funcionando(client, redis_fake):
    r = _login(client, EMAIL, SENHA)
    assert r.status_code == 200
    assert r.json()["access_token"]


# ---------------------------------------------------------------------------
# 2 -- Rate limiting
# ---------------------------------------------------------------------------

def test_bloqueia_apos_o_limite_de_tentativas(client, redis_fake):
    for i in range(settings.LOGIN_MAX_TENTATIVAS):
        assert _login(client, EMAIL, "errada").status_code == 401, f"tentativa {i+1}"

    r = _login(client, EMAIL, "errada")
    assert r.status_code == 429
    assert "Muitas tentativas" in r.json()["detail"]
    assert str(settings.LOGIN_JANELA_BLOQUEIO_MINUTOS) in r.json()["detail"]


def test_bloqueio_vale_ate_pra_senha_correta(client, redis_fake):
    """Senão bastaria errar 5 vezes e acertar na 6ª -- o bloqueio não
    poderia ser contornado por quem descobriu a senha na força bruta."""
    for _ in range(settings.LOGIN_MAX_TENTATIVAS):
        _login(client, EMAIL, "errada")
    assert _login(client, EMAIL, SENHA).status_code == 429


def test_janela_expira_e_libera(client, redis_fake):
    """A liberação é por TTL no Redis. Simula a expiração apagando a
    chave, que é exatamente o que o Redis faz ao fim da janela."""
    for _ in range(settings.LOGIN_MAX_TENTATIVAS):
        _login(client, EMAIL, "errada")
    assert _login(client, EMAIL, SENHA).status_code == 429

    redis_fake.store.clear()  # TTL venceu
    assert _login(client, EMAIL, SENHA).status_code == 200


def test_ttl_e_armado_a_cada_falha(client, redis_fake):
    """Sem EXPIRE o contador ficaria eterno e o bloqueio nunca liberaria."""
    _login(client, EMAIL, "errada")
    chave = f"{PREFIXO_CHAVE_LOGIN}{EMAIL}"
    assert redis_fake.expira_em[chave] == settings.LOGIN_JANELA_BLOQUEIO_MINUTOS * 60


def test_login_correto_zera_o_contador(client, redis_fake):
    """Quem errou 4 vezes e acertou na 5ª não pode ficar a um erro do
    bloqueio."""
    for _ in range(settings.LOGIN_MAX_TENTATIVAS - 1):
        _login(client, EMAIL, "errada")
    assert _login(client, EMAIL, SENHA).status_code == 200
    assert redis_fake.store == {}


def test_contador_e_por_email_nao_global(client, db_session, redis_fake):
    db_session.add(User(email="outro@x.com", senha_hash=hash_password(SENHA),
                        role=UserRole.CLIENT.value))
    db_session.commit()

    for _ in range(settings.LOGIN_MAX_TENTATIVAS):
        _login(client, EMAIL, "errada")

    assert _login(client, EMAIL, "errada").status_code == 429
    assert _login(client, "outro@x.com", SENHA).status_code == 200


def test_variar_maiusculas_nao_reinicia_a_contagem(client, redis_fake):
    """Sem normalizar, bastaria alternar A@X.com / a@x.com pra ter
    tentativas infinitas."""
    for i in range(settings.LOGIN_MAX_TENTATIVAS):
        email = EMAIL.upper() if i % 2 else EMAIL
        _login(client, email, "errada")
    assert _login(client, "  Real@X.COM  ", "errada").status_code == 429


def test_redis_fora_do_ar_nao_derruba_o_login(client, db_session):
    """FALHA ABERTO de propósito: instabilidade no Redis não pode trancar
    todo mundo pra fora. Ver a ressalva em app/core/rate_limit.py."""
    app.dependency_overrides[get_redis] = lambda: RedisQuebrado()
    assert _login(client, EMAIL, SENHA).status_code == 200
    assert _login(client, EMAIL, "errada").status_code == 401


def test_bloqueio_nao_gasta_bcrypt(client, redis_fake, monkeypatch):
    """O 429 é checado antes de tocar no banco -- não adianta gastar um
    hash numa tentativa que já vai ser recusada."""
    for _ in range(settings.LOGIN_MAX_TENTATIVAS):
        _login(client, EMAIL, "errada")

    from app.api.routes import auth as rota_auth

    def _nao_deveria_chamar(*a, **kw):
        raise AssertionError("verify_password não deve rodar com o e-mail bloqueado")

    monkeypatch.setattr(rota_auth, "verify_password", _nao_deveria_chamar)
    assert _login(client, EMAIL, SENHA).status_code == 429

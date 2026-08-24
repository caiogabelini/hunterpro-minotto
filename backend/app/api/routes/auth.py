"""
Autenticação — login enxuto (e-mail + senha -> JWT).

Sem registro público: usuários são criados por admin via
scripts/create_user.py (decisão consciente, ver README "O que falta" —
só a Minotto e a 4Hands usam o sistema por enquanto).

⚠️ DUAS PROTEÇÕES ADICIONADAS EM 24/08/2026 (auditoria de segurança):
tempo de resposta constante (ver `_autenticar`) e limite de tentativas
(ver `app/core/rate_limit.py`).
"""
import secrets

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.rate_limit import esta_bloqueado, get_redis, limpar_tentativas, registrar_falha
from app.core.security import create_access_token, hash_password, verify_password
from app.models.user import User
from app.schemas.auth import LoginRequest, TokenResponse

router = APIRouter()

# Hash descartável usado quando o e-mail não existe -- ver `_autenticar`.
#
# Gerado no import a partir de uma senha aleatória, em vez de um hash
# fixo escrito à mão: assim ele nasce com EXATAMENTE o mesmo custo (cost
# factor) que `hash_password` está usando agora. Um hash colado no código
# ficaria com o custo da época em que foi colado, e a igualdade de tempo
# -- que é o ponto -- se perderia em silêncio no dia em que o parâmetro
# do bcrypt mudasse.
#
# Custo: um bcrypt (~0,25 s) por processo, uma vez, na subida.
_HASH_DUMMY = hash_password(secrets.token_urlsafe(32))


def _autenticar(db: Session, email: str, senha: str) -> User | None:
    """
    Devolve o usuário se as credenciais conferem, ou None.

    ⚠️ **RODA bcrypt SEMPRE, inclusive quando o e-mail não existe** --
    contra um hash descartável. Isso é o ponto da função, não detalhe de
    implementação.

    A versão anterior era `if usuario is None or not usuario.ativo or not
    verify_password(...)`. O curto-circuito do `or` fazia com que e-mail
    inexistente retornasse ANTES do bcrypt: medido em 24/08/2026,
    **7,7 ms contra 459,4 ms** de uma senha errada -- 59x. Ou seja, dava
    pra enumerar quais e-mails têm cadastro só cronometrando a resposta,
    anulando na prática a mensagem de erro genérica que a rota usa
    justamente pra não vazar isso.

    Também não retorna cedo pra usuário inativo, pelo mesmo motivo.
    """
    usuario = db.execute(select(User).where(User.email == email)).scalar_one_or_none()

    # Um `verify_password` sempre, com o mesmo custo, nos dois caminhos.
    hash_para_conferir = usuario.senha_hash if usuario is not None else _HASH_DUMMY
    senha_confere = verify_password(senha, hash_para_conferir)

    if usuario is None or not usuario.ativo or not senha_confere:
        return None
    return usuario


@router.post("/login", response_model=TokenResponse)
def login(dados: LoginRequest, db: Session = Depends(get_db), redis_client=Depends(get_redis)):
    """
    Valida e-mail + senha contra o banco e devolve um access_token (JWT)
    com `user_id` e `role` no payload.

    401 com a mesma mensagem genérica tanto pra e-mail inexistente
    quanto pra senha errada (e pra usuário inativo) — não vazar quais
    e-mails têm cadastro no sistema. Desde 24/08/2026 o TEMPO de resposta
    também é o mesmo nos três casos (ver `_autenticar`); antes disso a
    mensagem era genérica mas o relógio entregava.

    429 depois de `settings.LOGIN_MAX_TENTATIVAS` falhas seguidas no
    mesmo e-mail dentro da janela (ver app/core/rate_limit.py).

    ⚠️ O bloqueio é checado ANTES de tocar no banco: não adianta gastar
    um bcrypt numa tentativa que já vai ser recusada. Isso não cria um
    novo oráculo -- pra ver o 429 o atacante precisa ter causado ele
    mesmo as falhas naquele e-mail, o que não diz nada sobre o e-mail
    existir ou não.
    """
    if esta_bloqueado(dados.email, redis_client):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                "Muitas tentativas de login. "
                f"Tente novamente em até {settings.LOGIN_JANELA_BLOQUEIO_MINUTOS} minutos."
            ),
        )

    usuario = _autenticar(db, dados.email, dados.senha)

    if usuario is None:
        registrar_falha(dados.email, redis_client)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="E-mail ou senha inválidos")

    limpar_tentativas(dados.email, redis_client)
    token = create_access_token({"user_id": usuario.id, "role": usuario.role})
    return TokenResponse(access_token=token)

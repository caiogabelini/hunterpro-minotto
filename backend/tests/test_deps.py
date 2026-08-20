"""
Testes de app/api/deps.py.

`get_current_user` é testado via rotas HTTP (ver test_auth_routes.py —
mais realista, exercitando o header Authorization de verdade).
`require_admin` é testado direto aqui, sem precisar de rota
administrativa nenhuma pra pendurar nela (nenhuma existe ainda).
"""
import pytest
from fastapi import HTTPException

from app.api.deps import require_admin
from app.models.user import User, UserRole


def _usuario(role: str) -> User:
    return User(id="fake-id", email="x@x.com", senha_hash="hash-qualquer", role=role, ativo=True)


def test_require_admin_deixa_passar_usuario_admin():
    admin = _usuario(UserRole.ADMIN.value)
    assert require_admin(admin) is admin


def test_require_admin_bloqueia_usuario_client():
    cliente = _usuario(UserRole.CLIENT.value)

    with pytest.raises(HTTPException) as exc_info:
        require_admin(cliente)

    assert exc_info.value.status_code == 403

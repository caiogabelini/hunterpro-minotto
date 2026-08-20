#!/usr/bin/env python3
"""
Cria um usuário (normalmente o primeiro admin) no HunterPro Minotto
Contabilidade.

Não existe endpoint de cadastro público — decisão consciente, ver
README "O que falta": só a Minotto e a 4Hands usam o sistema por
enquanto, então o primeiro (e os próximos) usuário(s) são criados por
esse script, rodado manualmente por quem tem acesso ao banco.

Uso:
    cd backend
    python scripts/create_user.py --email douglas@minotto.com.br --senha "uma-senha-forte" --role admin
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from app.core.database import SessionLocal  # noqa: E402
from app.core.security import hash_password  # noqa: E402
from app.models.user import User, UserRole  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--email", required=True)
    parser.add_argument("--senha", required=True, help="Senha em texto puro -- só usada pra gerar o hash, nunca é salva.")
    parser.add_argument("--role", choices=[r.value for r in UserRole], default=UserRole.ADMIN.value)
    args = parser.parse_args()

    db = SessionLocal()
    try:
        existente = db.execute(select(User).where(User.email == args.email)).scalar_one_or_none()
        if existente is not None:
            print(f"Já existe um usuário com o e-mail {args.email!r} (id={existente.id}).", file=sys.stderr)
            sys.exit(1)

        usuario = User(
            email=args.email,
            senha_hash=hash_password(args.senha),
            role=args.role,
            ativo=True,
        )
        db.add(usuario)
        db.commit()
        db.refresh(usuario)

        print(f"Usuário criado: {usuario.email} (id={usuario.id}, role={usuario.role}).")
    finally:
        db.close()


if __name__ == "__main__":
    main()

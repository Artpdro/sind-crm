from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import EmailStr, Field
from sqlalchemy import Connection, text
from sqlalchemy.exc import IntegrityError

from app.db import get_db
from app.modules.audit import service as audit
from app.modules.auth.schemas import Strict
from app.security import sessions
from app.security.passwords import hash_password, validate_password_policy

router = APIRouter(prefix="/api/users", tags=["users"])


def _out(row) -> dict:
    d = dict(row)
    d["is_active"] = bool(d["is_active"])
    d["must_change_password"] = bool(d["must_change_password"])
    return d
Role = Literal["admin", "operador", "visualizador"]


class UserCreate(Strict):
    username: str = Field(pattern=r"^[A-Za-z0-9._-]{3,50}$")
    email: EmailStr | None = None
    full_name: str | None = Field(default=None, max_length=150)
    role: Role
    password: str = Field(min_length=1, max_length=256)


class UserUpdate(Strict):
    role: Role | None = None
    is_active: bool | None = None


@router.get("")
def list_users(admin=Depends(sessions.require_role("admin")), db: Connection = Depends(get_db)):
    rows = db.execute(text(
        "SELECT id, username, email, full_name, role, is_active, must_change_password, last_login_at, created_at "
        "FROM users ORDER BY username")).mappings().all()
    return [_out(r) for r in rows]


@router.post("", status_code=201)
def create_user(body: UserCreate, request: Request, admin=Depends(sessions.require_role("admin")),
                db: Connection = Depends(get_db)):
    err = validate_password_policy(body.password, body.username)
    if err:
        raise HTTPException(status_code=422, detail=err)
    try:
        row = db.execute(
            text("INSERT INTO users (username, email, full_name, role, password_hash, must_change_password) "
                 "VALUES (:u, :e, :f, :r, :h, 1) "
                 "RETURNING id, username, email, full_name, role, is_active, must_change_password, last_login_at, created_at"),
            {"u": body.username, "e": body.email, "f": body.full_name, "r": body.role, "h": hash_password(body.password)},
        ).mappings().one()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Usuário ou e-mail já cadastrado.")
    audit.record(db, request, "usuario_criado", user_id=admin.user_id, username=admin.username,
                 entity_type="user", entity_id=row["id"], details={"role": body.role, "username": body.username})
    db.commit()
    return _out(row)


@router.patch("/{user_id}")
def update_user(user_id: int, body: UserUpdate, request: Request, admin=Depends(sessions.require_role("admin")),
                db: Connection = Depends(get_db)):
    changes = body.model_dump(exclude_unset=True)
    if not changes:
        raise HTTPException(status_code=422, detail="Nada para atualizar.")
    # A transação já é exclusiva (BEGIN IMMEDIATE): duas requisições simultâneas não removem o último admin.
    admins = db.execute(text("SELECT id FROM users WHERE role = 'admin' AND is_active = 1")).scalars().all()
    target = db.execute(text("SELECT id, role, is_active, username FROM users WHERE id = :i"),
                        {"i": user_id}).mappings().first()
    if not target:
        db.rollback()
        raise HTTPException(status_code=404, detail="Usuário não encontrado.")
    new_role = changes.get("role", target["role"])
    new_active = bool(changes.get("is_active", bool(target["is_active"])))
    was_admin = target["id"] in admins
    will_be_admin = new_role == "admin" and new_active
    if was_admin and not will_be_admin and len(admins) <= 1:
        db.rollback()
        raise HTTPException(status_code=409, detail="Não é possível remover o último administrador ativo.")
    db.execute(text("UPDATE users SET role = :r, is_active = :a WHERE id = :i"),
               {"r": new_role, "a": int(new_active), "i": user_id})
    if not new_active or new_role != target["role"]:
        sessions.revoke_all_sessions(db, user_id)  # permissão mudou: sessões antigas caem
    action = "permissao_alterada" if new_role != target["role"] else "usuario_atualizado"
    audit.record(db, request, action, user_id=admin.user_id, username=admin.username, entity_type="user",
                 entity_id=user_id, details={"de": target["role"], "para": new_role, "ativo": new_active})
    db.commit()
    return _out(db.execute(text(
        "SELECT id, username, email, full_name, role, is_active, must_change_password, last_login_at, created_at "
        "FROM users WHERE id = :i"), {"i": user_id}).mappings().one())

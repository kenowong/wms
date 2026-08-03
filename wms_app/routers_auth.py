# -*- coding: utf-8 -*-
"""API路由 - 用户认证与权限管理"""
import hashlib
import secrets
import datetime
from fastapi import APIRouter, HTTPException, Header, Depends
from pydantic import BaseModel
from typing import Optional
from database import get_conn

router = APIRouter()

# ── 简易 Token 存储（内存，重启失效，足够轻量单机使用）──
# token -> {user_id, username, role, display_name, expires_at}
_tokens: dict = {}

TOKEN_EXPIRE_HOURS = 12  # token 有效期

# ══════════════════════════════════════════
# 工具函数
# ══════════════════════════════════════════
def _hash_pwd(pwd: str) -> str:
    return hashlib.sha256(pwd.encode()).hexdigest()

def _new_token(user: dict) -> str:
    token = secrets.token_hex(32)
    expires = datetime.datetime.now() + datetime.timedelta(hours=TOKEN_EXPIRE_HOURS)
    _tokens[token] = {
        "user_id": user["id"],
        "username": user["username"],
        "display_name": user["display_name"] or user["username"],
        "role": user["role"],
        "permissions": user.get("permissions", "") or "",
        "expires_at": expires,
    }
    return token

def get_allowed_modules(token: str):
    """返回该 token 可访问的模块集合；admin 返回 'ALL'；无效 token 返回 None"""
    info = _tokens.get(token)
    if not info:
        return None
    if info.get("role") == "admin":
        return "ALL"
    return set((info.get("permissions") or "").split(","))

def _validate_token(token: str) -> dict:
    """校验 token，返回用户信息，无效则抛出 401"""
    if not token or token not in _tokens:
        raise HTTPException(status_code=401, detail="未登录或 token 已失效，请重新登录")
    info = _tokens[token]
    if datetime.datetime.now() > info["expires_at"]:
        del _tokens[token]
        raise HTTPException(status_code=401, detail="登录已过期，请重新登录")
    return info

def get_current_user(x_token: str = Header(default="")) -> dict:
    """FastAPI 依赖：从 X-Token 请求头获取当前用户"""
    return _validate_token(x_token)

def require_admin(user: dict = Depends(get_current_user)) -> dict:
    """要求管理员角色"""
    if user["role"] != "admin":
        raise HTTPException(status_code=403, detail="需要管理员权限")
    return user

# ══════════════════════════════════════════
# 登录 / 登出
# ══════════════════════════════════════════
class LoginModel(BaseModel):
    username: str
    password: str

@router.post("/auth/login")
def login(body: LoginModel):
    conn = get_conn()
    try:
        pwd_hash = _hash_pwd(body.password)
        row = conn.execute(
            "SELECT id,username,password_hash,display_name,role,status,permissions FROM users WHERE username=?",
            (body.username,)
        ).fetchone()
        if not row:
            raise HTTPException(status_code=401, detail="用户名或密码错误")
        if row["status"] == 0:
            raise HTTPException(status_code=403, detail="账号已被禁用，请联系管理员")
        if row["password_hash"] != pwd_hash:
            raise HTTPException(status_code=401, detail="用户名或密码错误")
        # 更新最后登录时间
        conn.execute(
            "UPDATE users SET last_login=datetime('now','localtime') WHERE id=?",
            (row["id"],)
        )
        conn.commit()
        token = _new_token(dict(row))
        return {
            "token": token,
            "username": row["username"],
            "display_name": row["display_name"] or row["username"],
            "role": row["role"],
            "permissions": row["permissions"] or "",
            "expires_hours": TOKEN_EXPIRE_HOURS,
        }
    finally:
        conn.close()

@router.post("/auth/logout")
def logout(x_token: str = Header(default="")):
    if x_token and x_token in _tokens:
        del _tokens[x_token]
    return {"ok": True}

@router.get("/auth/me")
def me(user: dict = Depends(get_current_user)):
    """验证 token 有效性，返回当前用户信息"""
    return {
        "username": user["username"],
        "display_name": user["display_name"],
        "role": user["role"],
        "permissions": user.get("permissions", "") or "",
    }

# ══════════════════════════════════════════
# 用户管理（管理员专用）
# ══════════════════════════════════════════
ROLE_LABELS = {"admin": "管理员", "operator": "操作员", "viewer": "查看员"}

@router.get("/users")
def list_users(user: dict = Depends(get_current_user)):
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT id,username,display_name,role,status,last_login,created_at,permissions FROM users ORDER BY id"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()

@router.get("/users/options")
def list_user_options(user: dict = Depends(get_current_user)):
    """供业务人员下拉选择使用：返回所有启用用户（含禁用，便于历史单据仍可显示）"""
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT id,username,display_name,status FROM users ORDER BY id"
        ).fetchall()
        return [
            {
                "id": r["id"],
                "username": r["username"],
                "display_name": r["display_name"] or r["username"],
                "status": r["status"],
            }
            for r in rows
        ]
    finally:
        conn.close()

class UserCreateModel(BaseModel):
    username: str
    password: str
    display_name: Optional[str] = ""
    role: Optional[str] = "operator"   # admin/operator/viewer
    status: Optional[int] = 1
    permissions: Optional[str] = ""    # 逗号分隔的模块权限键（admin 忽略，恒为全权限）

@router.post("/users")
def create_user(body: UserCreateModel, admin: dict = Depends(require_admin)):
    if body.role not in ROLE_LABELS:
        raise HTTPException(400, f"角色无效，可选：{'/'.join(ROLE_LABELS.keys())}")
    if len(body.password) < 4:
        raise HTTPException(400, "密码至少4位")
    conn = get_conn()
    try:
        pwd_hash = _hash_pwd(body.password)
        conn.execute(
            "INSERT INTO users(username,password_hash,display_name,role,status,permissions) VALUES(?,?,?,?,?,?)",
            (body.username, pwd_hash, body.display_name or body.username, body.role, body.status, body.permissions or "")
        )
        conn.commit()
        return {"ok": True, "msg": f"用户 {body.username} 创建成功"}
    except Exception as e:
        if "UNIQUE" in str(e):
            raise HTTPException(400, f"用户名 {body.username} 已存在")
        raise HTTPException(500, str(e))
    finally:
        conn.close()

class UserUpdateModel(BaseModel):
    display_name: Optional[str] = None
    role: Optional[str] = None
    status: Optional[int] = None
    password: Optional[str] = None   # 不传则不修改密码
    permissions: Optional[str] = None  # 逗号分隔模块权限键

@router.put("/users/{uid}")
def update_user(uid: int, body: UserUpdateModel, admin: dict = Depends(require_admin)):
    conn = get_conn()
    try:
        row = conn.execute("SELECT id,username FROM users WHERE id=?", (uid,)).fetchone()
        if not row:
            raise HTTPException(404, "用户不存在")
        sets, vals = [], []
        if body.display_name is not None:
            sets.append("display_name=?"); vals.append(body.display_name)
        if body.role is not None:
            if body.role not in ROLE_LABELS:
                raise HTTPException(400, f"角色无效，可选：{'/'.join(ROLE_LABELS.keys())}")
            sets.append("role=?"); vals.append(body.role)
        if body.status is not None:
            # 不允许禁用自己
            if row["username"] == admin["username"] and body.status == 0:
                raise HTTPException(400, "不能禁用自己的账号")
            sets.append("status=?"); vals.append(body.status)
        if body.permissions is not None:
            sets.append("permissions=?"); vals.append(body.permissions or "")
        if body.password is not None:
            if len(body.password) < 4:
                raise HTTPException(400, "密码至少4位")
            sets.append("password_hash=?"); vals.append(_hash_pwd(body.password))
        if not sets:
            return {"ok": True, "msg": "无变更"}
        vals.append(uid)
        conn.execute(f"UPDATE users SET {','.join(sets)} WHERE id=?", vals)
        conn.commit()
        return {"ok": True, "msg": "更新成功"}
    finally:
        conn.close()

@router.delete("/users/{uid}")
def delete_user(uid: int, admin: dict = Depends(require_admin)):
    conn = get_conn()
    try:
        row = conn.execute("SELECT username FROM users WHERE id=?", (uid,)).fetchone()
        if not row:
            raise HTTPException(404, "用户不存在")
        if row["username"] == admin["username"]:
            raise HTTPException(400, "不能删除自己的账号")
        conn.execute("DELETE FROM users WHERE id=?", (uid,))
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()

@router.post("/auth/change_password")
def change_password(
    body: dict,
    user: dict = Depends(get_current_user)
):
    """任何登录用户可修改自己的密码"""
    old_pwd = body.get("old_password", "")
    new_pwd = body.get("new_password", "")
    if len(new_pwd) < 4:
        raise HTTPException(400, "新密码至少4位")
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT password_hash FROM users WHERE id=?", (user["user_id"],)
        ).fetchone()
        if not row or row["password_hash"] != _hash_pwd(old_pwd):
            raise HTTPException(400, "原密码不正确")
        conn.execute(
            "UPDATE users SET password_hash=? WHERE id=?",
            (_hash_pwd(new_pwd), user["user_id"])
        )
        conn.commit()
        return {"ok": True, "msg": "密码修改成功"}
    finally:
        conn.close()

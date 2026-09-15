# ============================================================
# 智选情报官 · 账号服务（M2）
# 存储：复用个人库 SQLite（与 schema_personal.py 同一 data/personal.db）。
#       accounts 表用 IF NOT EXISTS 创建，绝不破坏已造样例数据。
# 定位：本地纯个人使用，非多租户 SaaS。账号仅用于「切换服务视角」
#       （company / personal）与「隔离个人/公司数据」。
# 鉴权：本地应用用 secrets.token_hex 生成 session token，存内存字典
#       （进程级）。够用且零额外依赖；重启即失效，符合「纯个人本地」定位。
# ============================================================

import secrets
import time
from typing import Optional

from fastapi import HTTPException, status
from pydantic import BaseModel

# 复用个人库连接（accounts 表就在 personal.db）
from tools.schema_personal import ensure_tables, get_personal_conn

# 进程内 session 表：token -> {username, role, display_name, login_at}
_SESSIONS: dict[str, dict] = {}

_VALID_ROLES = ("company", "personal")


# ---------------------------------------------------------------------------
# Pydantic 请求/响应模型
# ---------------------------------------------------------------------------
class RegisterReq(BaseModel):
    username: str
    password: str
    role: str = "personal"          # company / personal
    display_name: Optional[str] = None


class LoginReq(BaseModel):
    username: str
    password: str


class AccountResp(BaseModel):
    username: str
    role: str
    display_name: Optional[str] = None


class LoginResp(BaseModel):
    token: str
    account: AccountResp


# ---------------------------------------------------------------------------
# 底层 DB 操作（幂等、不破坏样例）
# ---------------------------------------------------------------------------
def _ensure_accounts_table() -> None:
    """统一走 schema_personal.ensure_tables（幂等，含全部业务表）。"""
    ensure_tables()


def _get_account(username: str) -> Optional[dict]:
    conn = get_personal_conn()
    try:
        cur = conn.execute(
            "SELECT id, username, password_hash, role, display_name "
            "FROM accounts WHERE username = ?",
            (username,),
        )
        row = cur.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def _create_account(username: str, password_hash: str, role: str,
                    display_name: Optional[str]) -> None:
    conn = get_personal_conn()
    try:
        conn.execute(
            "INSERT INTO accounts (username, password_hash, role, display_name) "
            "VALUES (?, ?, ?, ?)",
            (username, password_hash, role, display_name),
        )
        conn.commit()
    finally:
        conn.close()


# 本地单文件应用：明文哈希 + 常量盐。够用；非生产级安全。
_SALT = "zhixuan_local_salt_v1"


def _hash_pwd(pwd: str) -> str:
    import hashlib
    return hashlib.sha256((_SALT + pwd).encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# 业务接口
# ---------------------------------------------------------------------------
def register(req: RegisterReq) -> AccountResp:
    _ensure_accounts_table()
    username = req.username.strip()
    if not username or not req.password:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "用户名与密码不能为空")
    if req.role not in _VALID_ROLES:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"role 必须为 {_VALID_ROLES}",
        )
    if _get_account(username):
        raise HTTPException(status.HTTP_409_CONFLICT, "该用户名已存在")
    _create_account(username, _hash_pwd(req.password), req.role, req.display_name)
    return AccountResp(username=username, role=req.role, display_name=req.display_name)


def login(req: LoginReq) -> LoginResp:
    """登录校验。

    ⚠️ 语义（2026-09-16 用户拍板变更）：**账号不存在 → 404；密码错误 → 401**，两者文案不同。
    代价是理论上可被用来枚举账号（本地单机个人应用，无此风险）；收益是前端能给出
    「该账号不存在，是否去注册？」的引导，而不是把「打错用户名」和「密码错」混为一谈
    —— 之前前端把登录失败一律当成「新用户」自动建号，导致错别字直接建成账号（如「尼姑喵喵」）。
    """
    _ensure_accounts_table()
    acc = _get_account(req.username.strip())
    if not acc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "账号不存在")
    if acc["password_hash"] != _hash_pwd(req.password):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "密码错误")
    token = secrets.token_hex(16)
    _SESSIONS[token] = {
        "account_id": acc["id"],
        "username": acc["username"],
        "role": acc["role"],
        "display_name": acc["display_name"],
        "login_at": time.time(),
    }
    return LoginResp(
        token=token,
        account=AccountResp(
            username=acc["username"],
            role=acc["role"],
            display_name=acc["display_name"],
        ),
    )


def get_session(token: Optional[str]) -> dict:
    """校验 token，返回 session 信息；无效则抛 401。"""
    if not token or token not in _SESSIONS:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "未登录或登录已失效")
    return _SESSIONS[token]


def logout(token: Optional[str]) -> None:
    if token and token in _SESSIONS:
        _SESSIONS.pop(token, None)

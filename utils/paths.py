# -*- coding: utf-8 -*-
"""用户数据的**唯一事实源**（v4.0 大点 04 · 议题一，2026-10-13 用户拍板归口）。

为什么有这个模块
----------------
账号级数据原本散在 7 个根下（`agents_docs/`、`output/`、`pic/`、`rag_knowledge/db/`、
`data/sandbox/`、`data/probe_sets/`、`data/tts_ref/`），每个模块各自拼路径、各自维护一份
"账号目录清单"（实测三份互不一致），清理账号时反复漏项。2026-10-13 用户拍板：

    在项目根新建 `user_data/` 归口全部账号级用户数据（`data/personal.db` 暂留 `data/`）。

之后**谁要用户数据路径都来问这里**，不要在别处再拼一次。历史教训：同一个文件被三处各拼
一遍 → 出现过 `agents_docs/agents_docs/{user}/` 的"写进 A、读的是 B"事故；`tools/chat_images.py`
甚至留过"探测 `utils.paths.pic_dir` 是否存在，探不到就自己拼"的痕迹 —— 本模块就是它当年在找的东西。

目录树
------
    user_data/agents_docs/{账号}/        人格 SOUL / 记忆 MEMORY / 人格库 / skills
    user_data/output/{账号}/             周报、导出稿、测评打分表、`tts/` 音频
    user_data/pic/{账号}/                背景图 + `chat_images/`（HTTP 仍挂在 `/pic`）
    user_data/rag_knowledge/db/{账号}/   知识库向量（chroma / chroma_route / store.json）
    user_data/sandbox/{账号}/            命令执行沙箱（另含匿名兜底 `public/`）
    user_data/probe_sets/{账号}.json     测评题集（裁定 13：产品数据，不入库）
    user_data/tts_ref/{账号}/            音色克隆参考音频

本模块只提供路径，**不创建目录、不写文件**（需要落盘时显式调用 `ensure_dir`）。
"""
from __future__ import annotations

import os
from pathlib import Path

# 项目根 = 本文件的上两级（utils/ → 项目根）
PROJECT_ROOT = Path(__file__).resolve().parent.parent

USER_DATA_ROOT = PROJECT_ROOT / "user_data"

AGENTS_DOCS = USER_DATA_ROOT / "agents_docs"
OUTPUT_DIR = USER_DATA_ROOT / "output"
PIC_DIR = USER_DATA_ROOT / "pic"
RAG_DB = USER_DATA_ROOT / "rag_knowledge" / "db"
SANDBOX_ROOT = USER_DATA_ROOT / "sandbox"
# ★ 06（2026-10-16 定案）：03 测评的**中间产物**（原始记录 probe_run_*.json / 画像快照 profile_backup_*）
#   从沙箱里搬出来，放这里。为什么：沙箱白盒化后那些东西会直接摊在用户眼前（用户从来没听说过它们），
#   而它们只是测评的临时产物。搬出后"沙箱 = Agent 的工作目录"这件事才对用户成立。
PROBE_ARTIFACTS = USER_DATA_ROOT / "probe_artifacts"
PROBE_SETS_DIR = USER_DATA_ROOT / "probe_sets"
TTS_REF_DIR = USER_DATA_ROOT / "tts_ref"

#: 唯一的「账号级目录根」清单 —— 清理 / 统计 / 迁移都读它，**不要在任何地方再造副本**。
ACCOUNT_DIRS = (AGENTS_DOCS, OUTPUT_DIR, PIC_DIR, RAG_DB, SANDBOX_ROOT, TTS_REF_DIR, PROBE_ARTIFACTS)

#: 测评清理（水位线）只允许碰的目录 —— **不含 `TTS_REF_DIR`**（音色参考音频不是测评产物，
#  误删会丢用户素材）、**不含 `RAG_DB`**（向量库不是探针产物）。口径同 `probe_runner.py:400` 的教训。
# 水位线清理要绕开的账号目录（测评产物在里面：删了判分/画像还原就没了）
PROBE_ARTIFACT_DIRS = (AGENTS_DOCS, OUTPUT_DIR, PIC_DIR, PROBE_ARTIFACTS)

#: 账号级**单文件**（相对 `USER_DATA_ROOT` 的路径模板）。
PROBE_SET_TPL = "probe_sets/{username}.json"

MEMORY_NAME = "MEMORY.md"
SOUL_NAME = "SOUL.md"
ANON_USERNAME = "public"          # 匿名兜底沙箱名，与 tools/_runtime/shell_runtime.py 同口径


# --------------------------------------------------------------------------- #
# 基础工具
# --------------------------------------------------------------------------- #
def _name(username) -> str:
    return str(username or "").strip()


def ensure_dir(d) -> Path:
    """确保目录存在并返回它（路径函数保持"纯"，落盘必须显式调用本函数）。"""
    p = Path(d)
    p.mkdir(parents=True, exist_ok=True)
    return p


def migrated() -> bool:
    """新根是否已就位（迁移期自检用；也方便排查"代码找了新路径、文件还在旧路径"）。"""
    return USER_DATA_ROOT.is_dir()


# --------------------------------------------------------------------------- #
# 各存放点的取路径函数
# --------------------------------------------------------------------------- #
def user_data_root() -> Path:
    return USER_DATA_ROOT


def user_dir(username) -> Path:
    """`user_data/agents_docs/{账号}`（人格 / 记忆 / 技能所在）。"""
    return AGENTS_DOCS / _name(username)


def memory_path(username) -> Path:
    return user_dir(username) / MEMORY_NAME


def soul_path(username) -> Path:
    return user_dir(username) / SOUL_NAME


def soul_dir(username) -> Path:
    return user_dir(username) / "SOUL"


def skills_dir(username) -> Path:
    return user_dir(username) / "skills"


def output_dir(username) -> Path:
    """周报 / 导出稿 / 打分表所在账号目录（`tts/` 是它的子目录）。"""
    return OUTPUT_DIR / _name(username)


def pic_dir(account) -> str:
    """`user_data/pic/{账号}`（**返回字符串** —— `tools/chat_images.py` 会探测本函数）。

    account 允许是账号 id 或 username：`tools/chat_images.py` 传的是 id 的字符串形式，
    故这里原样拼接（与旧实现 `pic/{str(account_id)}/` 完全一致，不改变既有落盘位置）。
    """
    return str(PIC_DIR / str(account))


def chat_images_dir(account) -> str:
    return os.path.join(pic_dir(account), "chat_images")


def rag_db_dir(username) -> Path:
    """知识库向量目录（`chroma/`、`chroma_route/`、`store.json` 都在它下面）。"""
    return RAG_DB / _name(username)


def sandbox_dir(username) -> Path:
    return SANDBOX_ROOT / _name(username)


def probe_artifacts_dir(username) -> Path:
    """03 测评中间产物的账号目录（原始记录 / 画像快照）。"""
    return PROBE_ARTIFACTS / _name(username)


def tts_ref_dir(username) -> Path:
    return TTS_REF_DIR / _name(username)


def tts_audio_dir(username) -> Path:
    return output_dir(username) / "tts"


def probe_set_path(username) -> Path:
    return PROBE_SETS_DIR / ("%s.json" % _name(username))


def account_dirs_of(username) -> list:
    """某账号在各存放点的目录（给清理/统计用，**唯一口径**）。"""
    return [
        user_dir(username),
        output_dir(username),
        PIC_DIR / _name(username),
        rag_db_dir(username),
        sandbox_dir(username),
        tts_ref_dir(username),
    ]

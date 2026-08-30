"""语音朗读与自定义音色后端 API（联动 voice_test 小项目，环境完全隔离）。

设计原则（用户明确）：
- 两个项目环境冲突（voice_test: py3.12+torch+qwen_tts；主项目: py3.11+langchain），
  不做代码融合 —— 主项目通过 subprocess 执行 voice_test 的脚本命令。
- 显存限制（RTX 3050 4GB）：模型**只在用户使用时启动**（subprocess 跑脚本），
  用完即关（脚本进程退出即释放显存），不长期常驻。
- pkl 按账号隔离：语音包名加 {username}_ 前缀（voice_test 脚本零改动）。

流程：
- 音色克隆：POST /api/voice/packs（参考音频+台词）→ subprocess build_voice_pack.py
  → voice_packs/{username}_{name}.pkl
- 语音合成：POST /api/tts/speak（文本+选中语音包）→ subprocess synth.py
  → output/{username}/tts/{ts}.wav → 前端 <audio> 播放
"""
import os
import subprocess
import threading
import uuid
from pathlib import Path

from fastapi import HTTPException, UploadFile, File, Form, status
from pydantic import BaseModel

_PROJECT_ROOT = Path(__file__).resolve().parents[1]

# ---- voice_test 项目路径（硬编码约定路径，与本机环境绑定）----
VOICE_TEST_DIR = Path(r"D:\code\aicode\ai智能体开发实战5期\weekend\voice_test")
VOICE_PY = VOICE_TEST_DIR / ".venv" / "Scripts" / "python.exe"
BUILD_SCRIPT = VOICE_TEST_DIR / "build_voice_pack.py"
SYNTH_SCRIPT = VOICE_TEST_DIR / "synth.py"
VOICE_PACKS_DIR = VOICE_TEST_DIR / "voice_packs"

# 参考音频与合成产物目录（主项目内，按账号隔离）
TTS_REF_DIR = _PROJECT_ROOT / "data" / "tts_ref"   # data/tts_ref/{username}/


def _user_tts_out_dir(user: str) -> Path:
    d = _PROJECT_ROOT / "output" / user / "tts"     # output/{username}/tts/
    d.mkdir(parents=True, exist_ok=True)
    return d

_ALLOWED_AUDIO_EXT = {".wav", ".flac", ".ogg", ".mp3"}
_MAX_AUDIO_MB = 200   # 参考音频大小上限（原脚本无此限制，只自动截取前8秒；200MB 仅为防滥用护栏，覆盖长片段音频）
_MAX_REF_TEXT = 60       # 克隆参考台词截断上限（角色长台词会撑大 prompt → 合成极慢+读回）     # 台词文本上限
_MAX_SYNTH_TEXT = 150    # 朗读总文本上限（超出截取前 150 字）。实测：克隆 pkl 的 ref_text 越长
                        # 合成越慢（元流之子角色台词 pkl：每段 9 分钟，5 段 45 分钟必然超时），
                        # 故限制总文本 150 字（2 段，约 15-20 分钟可完成）
_PER_SEGMENT = 100      # 每段合成上限：Qwen3-TTS-12Hz 在 RTX 3050 上单段过长会极慢/卡顿
                        # （实测 100 字≈141s 含模型加载；150 字 10 分钟+ 未完成），
                        # 长文本按 100 字切段 → synth.py --in 批量合成（只加载一次模型）→ wav 拼接

# 全局串行锁：显存 4GB，同一时刻只跑一个 TTS 任务（克隆或合成）
_tts_lock = threading.Lock()

# 异步合成任务表：task_id -> {status: pending|running|done|error, url, error, created_at}
_tts_tasks: dict = {}
_tts_task_lock = threading.Lock()

# subprocess 环境：必须清掉 PYTHONPATH —— Hermes 桌面端注入的 hermes-agent
# venv 路径会让 voice_test 的 numpy/torch 加载崩溃（cp311 .pyd vs py3.12）。
def _clean_env() -> dict:
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    return env


def _require_voice_env():
    if not VOICE_PY.is_file():
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR,
                            f"voice_test venv 未找到: {VOICE_PY}")
    if not BUILD_SCRIPT.is_file() or not SYNTH_SCRIPT.is_file():
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR,
                            "voice_test 脚本缺失 (build_voice_pack.py / synth.py)")


def _username_by_token(token: str) -> str:
    from api.account import get_session
    return get_session(token)["username"]


def _full_pack_name(username: str, name: str) -> str:
    """账号隔离：voice_packs/{username}_{name}.pkl（voice_test 脚本零改动）"""
    safe = "".join(c for c in name if c.isalnum() or c in "_-").strip()
    if not safe:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "语音包名仅支持字母/数字/下划线/横线")
    return f"{username}_{safe}"


def _user_packs(username: str) -> list[dict]:
    """扫描该账号全部 pkl：返回 [{name, full_name, size_mb, mtime, active}]"""
    active = _active_pack_name(username)
    items = []
    if VOICE_PACKS_DIR.is_dir():
        prefix = f"{username}_"
        for f in sorted(VOICE_PACKS_DIR.glob("*.pkl"), key=lambda x: x.stat().st_mtime, reverse=True):
            if not f.name.startswith(prefix):
                continue
            full = f.name[:-4]
            items.append({
                "name": full[len(prefix):],
                "full_name": full,
                "size_mb": round(f.stat().st_size / 1024 / 1024, 2),
                "mtime": f.stat().st_mtime,
                "active": full == active,
            })
    return items


def _active_pack_file(username: str) -> Path:
    d = _PROJECT_ROOT / "agents_docs" / username
    d.mkdir(parents=True, exist_ok=True)
    return d / "ACTIVE_VOICE"


def _active_pack_name(username: str) -> str:
    f = _active_pack_file(username)
    if f.exists():
        name = f.read_text(encoding="utf-8").strip()
        if name:
            return name
    return ""


# ============================ 语音包管理 ============================
def voice_packs_list(token: str) -> dict:
    user = _username_by_token(token)
    try:
        _require_voice_env()
        available = True
    except HTTPException:
        available = False
    return {"packs": _user_packs(user),
            "active": _active_pack_name(user),
            "available": available}


def voice_pack_build(token: str, file: UploadFile, ref_text: str, pack_name: str) -> dict:
    """上传参考音频 + 台词 → subprocess 跑 build_voice_pack.py → pkl"""
    _require_voice_env()
    user = _username_by_token(token)

    # 参数校验
    ref_text = (ref_text or "").strip()
    if not ref_text:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "请填写参考音频对应的台词文本")
    # 参考台词截短（实测角色长台词会撑大克隆 prompt → 合成极慢 + 读回参考文本）
    if len(ref_text) > _MAX_REF_TEXT:
        ref_text = ref_text[:_MAX_REF_TEXT]
    pack_name = (pack_name or "").strip()
    if not pack_name:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "请填写语音包名称")
    full = _full_pack_name(user, pack_name)
    if (VOICE_PACKS_DIR / f"{full}.pkl").exists():
        raise HTTPException(status.HTTP_409_CONFLICT, f"语音包「{pack_name}」已存在")

    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in _ALLOWED_AUDIO_EXT:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            f"仅支持参考音频格式: {sorted(_ALLOWED_AUDIO_EXT)}")
    content = file.file.read()
    if len(content) > _MAX_AUDIO_MB * 1024 * 1024:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"参考音频过大（≤{_MAX_AUDIO_MB}MB）")

    # 存参考音频（主项目 data/tts_ref/{user}/）
    ref_dir = TTS_REF_DIR / user
    ref_dir.mkdir(parents=True, exist_ok=True)
    ref_path = ref_dir / f"{uuid.uuid4().hex[:8]}_{pack_name}{ext}"
    try:
        ref_path.write_bytes(content)
    except Exception as e:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, f"保存参考音频失败: {e}")

    # subprocess 执行克隆（模型仅在本次进程内使用，跑完即释放显存）
    cmd = [str(VOICE_PY), str(BUILD_SCRIPT),
           "--ref", str(ref_path),
           "--ref-text", ref_text,
           "--name", full]
    try:
        with _tts_lock:
            proc = subprocess.run(cmd, capture_output=True, text=True,
                                  encoding="utf-8", errors="replace",
                                  timeout=600, env=_clean_env())
    except subprocess.TimeoutExpired:
        raise HTTPException(status.HTTP_504_GATEWAY_TIMEOUT,
                            "音色克隆超时（>10分钟）。模型首次加载较慢，请重试")
    except Exception as e:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, f"执行克隆脚本失败: {e}")
    finally:
        # 参考音频用后即删（只留 pkl 资产）
        try:
            ref_path.unlink(missing_ok=True)
        except Exception:
            pass

    if proc.returncode != 0:
        tail = (proc.stdout + proc.stderr)[-800:]
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR,
                            f"音色克隆失败（voice_test 输出）:\n{tail}")
    return {"ok": True, "pack": pack_name, "full_name": full}


def voice_pack_select(token: str, pack_name: str) -> dict:
    """选中该账号的某个语音包（记录 ACTIVE_VOICE 文件）"""
    user = _username_by_token(token)
    full = _full_pack_name(user, pack_name)
    if not (VOICE_PACKS_DIR / f"{full}.pkl").exists():
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"语音包「{pack_name}」不存在")
    _active_pack_file(user).write_text(full, encoding="utf-8")
    return {"ok": True, "active": full}


def voice_pack_delete(token: str, pack_name: str) -> dict:
    """删除语音包 pkl（激活中的先清空激活状态）"""
    user = _username_by_token(token)
    full = _full_pack_name(user, pack_name)
    p = VOICE_PACKS_DIR / f"{full}.pkl"
    if not p.exists():
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"语音包「{pack_name}」不存在")
    if _active_pack_name(user) == full:
        _active_pack_file(user).write_text("", encoding="utf-8")
    p.unlink()
    return {"ok": True}


# ============================ 语音合成 ============================
class SpeakReq(BaseModel):
    text: str
    pack_name: str = ""  # 空 = 用当前激活语音包


def _speak_impl(user: str, pack_name: str, text: str) -> dict:
    """实际合成（供后台线程调用）：subprocess 跑 synth.py --in → 分段 wav 拼接 → 返回 URL"""
    _require_voice_env()
    out_dir = _user_tts_out_dir(user)
    import time
    ts = time.strftime("%Y%m%d_%H%M%S")
    wav_path = out_dir / f"{ts}_{uuid.uuid4().hex[:4]}.wav"

    # 长文本分段：每段 ≤100 字，写入 lines.txt，synth.py --in 批量合成（只加载一次模型）
    segments = _split_segments(text, _PER_SEGMENT)
    work_dir = out_dir / f"{wav_path.stem}_seg"
    work_dir.mkdir(parents=True, exist_ok=True)
    lines_file = work_dir / "lines.txt"
    lines_file.write_text("\n".join(segments), encoding="utf-8")

    cmd = [str(VOICE_PY), str(SYNTH_SCRIPT),
           "--pack", pack_name,
           "--in", str(lines_file),
           "--out-dir", str(work_dir)]
    try:
        with _tts_lock:
            proc = subprocess.run(cmd, capture_output=True, text=True,
                                  encoding="utf-8", errors="replace",
                                  timeout=1200, env=_clean_env())
    except subprocess.TimeoutExpired:
        import shutil
        shutil.rmtree(work_dir, ignore_errors=True)
        raise HTTPException(status.HTTP_504_GATEWAY_TIMEOUT,
                            "语音合成超时（>20分钟）。长文本分段合成较慢，请重试")
    except Exception as e:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, f"执行合成脚本失败: {e}")

    if proc.returncode != 0:
        import shutil
        shutil.rmtree(work_dir, ignore_errors=True)
        tail = (proc.stdout + proc.stderr)[-800:]
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR,
                            f"语音合成失败（voice_test 输出）:\n{tail}")

    # 拼接各段 wav（wave 标准库，零依赖；synth.py 输出同采样率/声道/位深）
    try:
        _concat_wavs(sorted(work_dir.glob("*.wav")), wav_path)
    except Exception as e:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, f"拼接音频失败: {e}")
    finally:
        import shutil
        shutil.rmtree(work_dir, ignore_errors=True)

    if not wav_path.is_file():
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "合成脚本未产出音频文件")
    return {"ok": True, "url": f"/api/tts/audio?name={wav_path.name}", "wav": str(wav_path)}


def _resolve_pack(user: str, pack_name: str) -> str:
    """解析语音包全名：前端传显示名转带账号前缀全名；空则用当前激活"""
    pack_name = (pack_name or "").strip()
    if not pack_name:
        pack_name = _active_pack_name(user)
        if not pack_name:
            raise HTTPException(status.HTTP_400_BAD_REQUEST,
                                "尚未选择语音包，请先在「定制助手 → 语音包」创建并选中一个音色")
    else:
        full = _full_pack_name(user, pack_name)
        if not (VOICE_PACKS_DIR / f"{full}.pkl").exists():
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"语音包「{pack_name}」不存在")
        pack_name = full
    return pack_name


def voice_speak(token: str, req: SpeakReq) -> dict:
    """异步合成入口：立即返回 task_id，后台线程执行合成（长任务不被 HTTP 超时掐断）"""
    _require_voice_env()
    user = _username_by_token(token)

    text = (req.text or "").strip()
    if not text:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "没有可朗读的文本")
    # 先清洗 markdown/emoji/表格噪音，再截断
    text = _clean_tts_text(text)
    if not text:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "清洗后没有可朗读的文本（内容全是符号/格式）")
    if len(text) > _MAX_SYNTH_TEXT:
        text = text[:_MAX_SYNTH_TEXT]
    pack_name = _resolve_pack(user, req.pack_name)

    task_id = uuid.uuid4().hex[:12]
    with _tts_task_lock:
        _tts_tasks[task_id] = {"status": "pending", "url": None, "error": None,
                               "created_at": __import__("time").time()}

    def _worker():
        try:
            with _tts_task_lock:
                _tts_tasks[task_id]["status"] = "running"
            res = _speak_impl(user, pack_name, text)
            with _tts_task_lock:
                _tts_tasks[task_id].update({"status": "done", "url": res["url"], "wav": res["wav"]})
        except HTTPException as e:
            with _tts_task_lock:
                _tts_tasks[task_id].update({"status": "error", "error": e.detail})
        except Exception as e:
            with _tts_task_lock:
                _tts_tasks[task_id].update({"status": "error", "error": f"{type(e).__name__}: {e}"})

    threading.Thread(target=_worker, daemon=True).start()
    return {"task_id": task_id}


def voice_speak_status(task_id: str) -> dict:
    """查询合成任务状态：pending|running|done|error"""
    with _tts_task_lock:
        task = _tts_tasks.get(task_id)
        if not task:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "任务不存在或已过期")
        return {"task_id": task_id, "status": task["status"],
                "url": task.get("url"), "error": task.get("error")}

    pack_name = (req.pack_name or "").strip()
    if not pack_name:
        pack_name = _active_pack_name(user)
        if not pack_name:
            raise HTTPException(status.HTTP_400_BAD_REQUEST,
                                "尚未选择语音包，请先在「定制助手 → 语音包」创建并选中一个音色")
    else:
        # 前端传的是显示名，转全名
        full = _full_pack_name(user, pack_name)
        if not (VOICE_PACKS_DIR / f"{full}.pkl").exists():
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"语音包「{pack_name}」不存在")
        pack_name = full

    out_dir = _user_tts_out_dir(user)
    import time
    ts = time.strftime("%Y%m%d_%H%M%S")
    wav_path = out_dir / f"{ts}_{uuid.uuid4().hex[:4]}.wav"

    # 长文本分段：每段 ≤100 字，写入 lines.txt，synth.py --in 批量合成（只加载一次模型）
    segments = _split_segments(text, _PER_SEGMENT)
    work_dir = out_dir / f"{wav_path.stem}_seg"
    work_dir.mkdir(parents=True, exist_ok=True)
    lines_file = work_dir / "lines.txt"
    lines_file.write_text("\n".join(segments), encoding="utf-8")

    cmd = [str(VOICE_PY), str(SYNTH_SCRIPT),
           "--pack", pack_name,
           "--in", str(lines_file),
           "--out-dir", str(work_dir)]
    try:
        with _tts_lock:
            proc = subprocess.run(cmd, capture_output=True, text=True,
                                  encoding="utf-8", errors="replace",
                                  timeout=900, env=_clean_env())
    except subprocess.TimeoutExpired:
        raise HTTPException(status.HTTP_504_GATEWAY_TIMEOUT,
                            "语音合成超时（>15分钟）。长文本分段合成较慢，请重试")
    except Exception as e:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, f"执行合成脚本失败: {e}")

    if proc.returncode != 0:
        import shutil
        shutil.rmtree(work_dir, ignore_errors=True)
        tail = (proc.stdout + proc.stderr)[-800:]
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR,
                            f"语音合成失败（voice_test 输出）:\n{tail}")

    # 拼接各段 wav（wave 标准库，零依赖；synth.py 输出同采样率/声道/位深）
    try:
        _concat_wavs(sorted(work_dir.glob("*.wav")), wav_path)
    except Exception as e:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, f"拼接音频失败: {e}")
    finally:
        import shutil
        shutil.rmtree(work_dir, ignore_errors=True)

    if not wav_path.is_file():
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "合成脚本未产出音频文件")
    return {"ok": True, "url": f"/api/tts/audio?name={wav_path.name}", "wav": str(wav_path)}


def _clean_tts_text(text: str) -> str:
    """朗读前清洗：去 markdown 语法/表格/emoji/HTML/链接，转纯自然语言。

    实测 AI 回复是结构化简报（## 标题、| 表格、**加粗**、🎯 emoji），
    直接喂 TTS 模型会把符号当文本乱读，甚至读回克隆参考台词。
    """
    import re
    # 表格整块识别（按行）：去掉 | 分隔符，单元格空格连接，跳过纯分隔行（|-| --- |）
    _lines = text.split('\n')
    _out = []
    for _ln in _lines:
        if '|' in _ln:
            _cells = [c.strip() for c in _ln.split('|') if c.strip()]
            if _cells and all(re.fullmatch(r'[-:]+', c) for c in _cells):
                continue  # 表格分隔行（|---|---|）直接跳过
            _out.append(' '.join(_cells))
        else:
            _out.append(_ln)
    text = '\n'.join(_out)
    # 残留的孤立竖线（防漏网）
    text = text.replace('|', ' ')
    # 代码围栏 ```...```
    text = re.sub(r'```.*?```', ' ', text, flags=re.S)
    # 行内代码 `x` → x
    text = re.sub(r'`([^`]*)`', r'\1', text)
    # 链接 [t](url) → t
    text = re.sub(r'\[([^\]]*)\]\([^)]*\)', r'\1', text)
    # 标题符 #；列表符 - * +；分隔线 ---
    text = re.sub(r'^#+\s*', '', text, flags=re.M)
    text = re.sub(r'^\s*[-*+]\s+', '', text, flags=re.M)
    text = re.sub(r'^\s*[-_=]{3,}\s*$', '。', text, flags=re.M)
    # 加粗/斜体符号
    text = re.sub(r'\*\*|__|\*(?=[^\s*])|\*(?<=[^\s*])', '', text)
    # emoji/符号/变体选择符/ZWJ
    text = re.sub(r'[\U0001F000-\U0001FAFF\u2600-\u27BF\uFE0F\u200D\u2B00-\u2BFF]', '', text)
    # HTML 标签
    text = re.sub(r'<[^>]+>', '', text)
    # 合并空白；清理多余逗号/句号
    text = re.sub(r'\s+', ' ', text)
    text = re.sub(r'[，,]{2,}', '，', text)
    text = re.sub(r'[。.]{2,}', '。', text)
    return text.strip()


def _split_segments(text: str, per: int) -> list[str]:
    """按标点断句切段，每段不超过 per 字符（避免截断在词语中间影响听感）"""
    if len(text) <= per:
        return [text]
    segs, cur = [], ""
    for ch in text:
        cur += ch
        if ch in "。！？；，、.!?;," and len(cur) >= per * 0.6 or len(cur) >= per:
            segs.append(cur)
            cur = ""
    if cur:
        segs.append(cur)
    return segs


def _concat_wavs(wav_files: list, out_path: Path) -> None:
    """用标准库 wave 拼接同格式 PCM wav（synth.py 输出参数一致）"""
    import wave
    if not wav_files:
        raise ValueError("无分段音频可拼接")
    params = None
    frames_all = b""
    for f in wav_files:
        with wave.open(str(f), "rb") as w:
            p = w.getparams()
            if params is None:
                params = p
            elif (p.nchannels, p.sampwidth, p.framerate) != (params.nchannels, params.sampwidth, params.framerate):
                raise ValueError(f"分段音频参数不一致: {f.name}")
            frames_all += w.readframes(w.getnframes())
    with wave.open(str(out_path), "wb") as w:
        w.setparams(params)
        w.writeframes(frames_all)


def voice_audio(token: str, name: str):
    """返回该账号 tts 目录下的 wav 文件（防目录穿越）"""
    from fastapi.responses import FileResponse
    user = _username_by_token(token)
    base = _user_tts_out_dir(user).resolve()
    name = os.path.basename(name)
    f = (base / name).resolve()
    if base not in f.parents or not f.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "音频不存在")
    return FileResponse(f, media_type="audio/wav", filename=name)

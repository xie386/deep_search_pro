# -*- coding: utf-8 -*-
"""离线单测：语音转文字**客户端**（tools/voice_asr.py ✓ 不联网 ✓ 不起引擎 ✓）

真机那一层由 `tests/live/asr_endpoint_probe.py` 覆盖 ✓（真模型 + 真端点 + 不落盘 ✓）；
这里只钉**纯逻辑**：开关语义 / 状态形状 / 人话错误映射 / 字段透传 ✓。
"""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from tools import voice_asr as va          # noqa: E402


class _FakeResp:
    def __init__(self, status, body):
        self.status_code = status
        self._body = body

    def json(self):
        return self._body


class _FakeClient:
    """假的 httpx.Client（上下文管理器 ✓ 记录请求 ✓）。"""
    def __init__(self, resp=None, raise_exc=None):
        self.resp = resp
        self.raise_exc = raise_exc
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def post(self, url, content=None, headers=None):
        self.calls.append(("POST", url, content, headers))
        if self.raise_exc:
            raise self.raise_exc
        return self.resp

    def get(self, url, timeout=None):
        self.calls.append(("GET", url, None, None))
        if self.raise_exc:
            raise self.raise_exc
        return self.resp


# ---------------------------------------------------------------- 开关
def test_enabled_default_true(monkeypatch):
    monkeypatch.delenv("ASR_ENABLED", raising=False)
    assert va._enabled() is True


@pytest.mark.parametrize("val", ["0", "false", "off", "no"])
def test_enabled_off_by_env(monkeypatch, val):
    monkeypatch.setenv("ASR_ENABLED", val)
    assert va._enabled() is False


def test_transcribe_when_disabled_raises_human_error(monkeypatch):
    monkeypatch.setenv("ASR_ENABLED", "0")
    with pytest.raises(va.VoiceAsrError) as e:
        va.transcribe(b"\x00\x00" * 100)
    assert "未启用" in str(e.value)


# ---------------------------------------------------------------- 状态
def test_status_shape(monkeypatch):
    monkeypatch.setenv("ASR_PORT", "8127")
    monkeypatch.setattr(va, "_health", lambda: None)
    monkeypatch.setattr(va, "_bin", lambda: r"C:\Users\ZQK\.local\bin\asr.exe")
    s = va.status()
    assert s["enabled"] is True and s["engine_running"] is False and s["port"] == 8127
    assert set(["enabled", "engine_running", "model", "lang", "port", "bin_ok", "bin", "error"]) <= set(s)
    assert s["bin_ok"] is True


def test_bin_prefers_env_override(monkeypatch, tmp_path):
    exe = tmp_path / "asr.exe"
    exe.write_bytes(b"MZ")
    monkeypatch.setenv("ASR_SERVE_BIN", str(exe))
    assert va._bin() == str(exe)


# ---------------------------------------------------------------- 转写：错误映射与字段
def test_transcribe_empty_audio(monkeypatch):
    monkeypatch.delenv("ASR_ENABLED", raising=False)
    with pytest.raises(va.VoiceAsrError) as e:
        va.transcribe(b"")
    assert "空音频" in str(e.value)


def test_transcribe_engine_400_becomes_human_error(monkeypatch):
    monkeypatch.delenv("ASR_ENABLED", raising=False)
    monkeypatch.setattr(va, "ensure_engine", lambda force=False: True)
    monkeypatch.setattr(va, "_client", lambda: _FakeClient(_FakeResp(400, {"ok": False, "msg": "说话时间太短（0.2s ✗）"})))
    with pytest.raises(va.VoiceAsrError) as e:
        va.transcribe(b"\x00\x00" * 3200)
    assert "太短" in str(e.value)


def test_transcribe_ok_passes_fields_through(monkeypatch):
    monkeypatch.delenv("ASR_ENABLED", raising=False)
    monkeypatch.setattr(va, "ensure_engine", lambda force=False: True)
    fake = _FakeClient(_FakeResp(200, {"ok": True, "text": " 你好 ", "audio_s": 1.5, "ms": 210,
                                       "model": "sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17"}))
    monkeypatch.setattr(va, "_client", lambda: fake)
    out = va.transcribe(b"\x00\x00" * 100)
    assert out["text"] == " 你好 " and out["ms"] == 210 and out["audio_s"] == 1.5
    assert out["engine"] == "sensevoice" and out["model"].startswith("sherpa-onnx-sense-voice")
    assert fake.calls and fake.calls[0][0] == "POST"          # 只发了一个 POST ✓
    assert "/asr" in fake.calls[0][1]                          # 打到 /asr 端点 ✓
    assert fake.calls[0][3].get("Content-Type") == "application/octet-stream"   # 裸字节 ✓ 不是 multipart ✓


def test_transcribe_engine_unreachable_reports_human_error(monkeypatch):
    monkeypatch.delenv("ASR_ENABLED", raising=False)
    monkeypatch.setattr(va, "ensure_engine", lambda force=False: True)
    monkeypatch.setattr(va, "_client", lambda: _FakeClient(raise_exc=RuntimeError("connect refused")))
    with pytest.raises(va.VoiceAsrError) as e:
        va.transcribe(b"\x00\x00" * 100)
    assert "请求失败" in str(e.value)


def test_ensure_engine_false_when_disabled(monkeypatch):
    monkeypatch.setenv("ASR_ENABLED", "0")
    assert va.ensure_engine() is False


# ---------------------------------------------------------------- 不落盘（源码级约束 ✓）
def test_module_never_writes_audio(monkeypatch):
    """★ 用户拍板：音频转完即删、不落盘 ✓ —— 模块里不得出现写文件的调用 ✓（除日志外 ✓）。"""
    src = open(os.path.join(ROOT, "tools", "voice_asr.py"), encoding="utf-8").read()
    assert "write_bytes" not in src and "read_bytes" not in src, "不得把音频落盘 ✓"
    assert "tempfile" not in src and "NamedTemporary" not in src, "不得用临时文件中转音频 ✓"
    assert '"wb"' not in src and "'wb'" not in src, "不得以二进制写模式开文件 ✓"
    # 唯一允许的落盘是**引擎日志**（纯文本 ✓ 不含音频 ✓）
    log_opens = [ln for ln in src.splitlines() if "open(" in ln and "log" in ln.lower()]
    assert log_opens, "引擎日志应当是本模块唯一的文件写入 ✓"
    assert all('"a"' in ln for ln in log_opens), "日志只允许追加写 ✓"

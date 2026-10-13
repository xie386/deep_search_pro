"""E4 去本机化单测：嵌入/重排路径缺失或不存在时，给中文明确提示（全离线，不加载模型）。"""
import pytest


@pytest.fixture()
def kb(monkeypatch):
    import importlib

    import rag_knowledge.kb_service as kb_service
    importlib.reload(kb_service)
    return kb_service


def test_no_machine_default_in_source(kb):
    """★ 核心判据：模块源码里不再兜底本机绝对路径（防止有人改回去）。"""
    import inspect
    src = inspect.getsource(kb)
    # 只看这些常量的**赋值行**：不许再接本机盘符兜底（注释里提历史不算）
    assigns = [s.strip() for s in src.splitlines()
               if s.strip().startswith(("EMBEDDING_MODEL_PATH", "RERANKER_MODEL_PATH"))]
    assert assigns, "没找到赋值行，测试需同步"
    for s in assigns:
        assert "D:" not in s and "\\LLM\\" not in s, s


def test_unset_env_yields_empty_not_machine_path(kb, monkeypatch):
    """未配置时是空串（由加载处给中文提示），不是悄悄用本机路径。"""
    import importlib
    monkeypatch.delenv("EMBEDDING_MODEL_PATH", raising=False)
    monkeypatch.delenv("RERANKER_MODEL_PATH", raising=False)
    importlib.reload(kb)
    assert kb.EMBEDDING_MODEL_PATH == ""
    assert kb.RERANKER_MODEL_PATH == ""


def test_device_defaults_to_cpu(kb, monkeypatch):
    monkeypatch.delenv("EMBEDDING_DEVICE", raising=False)
    import importlib
    importlib.reload(kb)
    assert kb.DEVICE == "cpu"


def test_require_model_dir_empty_gives_chinese_hint(kb):
    with pytest.raises(RuntimeError) as e:
        kb._require_model_dir("EMBEDDING_MODEL_PATH", "")
    assert "EMBEDDING_MODEL_PATH" in str(e.value) and ".env" in str(e.value)


def test_require_model_dir_missing_path_gives_chinese_hint(kb):
    with pytest.raises(RuntimeError) as e:
        kb._require_model_dir("RERANKER_MODEL_PATH", r"D:/no/such/dir/bge-reranker-base")
    assert "不存在" in str(e.value)


def test_require_model_dir_ok_returns_value(kb, tmp_path):
    assert kb._require_model_dir("X", str(tmp_path)) == str(tmp_path)

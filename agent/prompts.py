# 目标加载yml中的数据，供创建主和子智能体使用
import yaml # yaml配置文件读取
from pathlib import Path

# 定义一个加载函数，配置文件yaml加载成字典
def load_yaml(file_path):
    """
    加载指定位置的yaml配置文件
    :param file_path:  加载的文件的地址
    :return:  返回的加载结果 本质就是字典
    """
    with open(file_path, 'r', encoding='utf-8') as f :
        # safe_load 只会加载，不会触发！
        # load 加载过程中可能无意执行内部的嵌入函数！！ 可能发生注入脚本攻击
        return yaml.safe_load(f)

# 尝试读取主和子智能体的配置文件和数据（供后续使用）
# 项目的根地址
# project_root_path  = Path(__file__).parent.parent
project_root_path  = Path(__file__).parents[1] # prompts -> parents -> [agent , deep_search_pro]
yaml_file_path = project_root_path / "prompt" / "prompts.yml"

prompt_yaml_content = load_yaml(yaml_file_path)


# main_agent_content
main_agent_content = prompt_yaml_content["main_agent"]
# ⚠️ 调试打印已注释（2026-09-22）：import 阶段把整份提示词打到 stdout，
#    ① 每次启动刷一屏无用输出；② 在中文 Windows 上，当 stdout 是文件/管道时按 GBK 编码，
#    提示词里的 emoji（🔍 等）编不出来 → UnicodeEncodeError → **进程 import 阶段直接崩**
#    （桌面版双击崩溃、`uvicorn > server.log`、`dspro list > out.txt` 全都踩这个）。
# ★ 2026-10-13（议题三）：改由设置页的「提示词调试转储」开关控制 —— 打开后写**文件**
#   （`data/audit/prompts_dump.txt`），正好绕开上面那个 GBK 崩溃的坑。
def dump_prompts(extra: str = "") -> str:
    """按设置把提示词转储到 data/audit/prompts_dump.txt（开关关着则什么都不做）。"""
    try:
        from utils import settings as _settings
        if not _settings.get_bool("DS_DEBUG_PROMPTS", False):
            return ""
        import time as _time
        d = Path(__file__).parents[1] / "data" / "audit"
        d.mkdir(parents=True, exist_ok=True)
        p = d / "prompts_dump.txt"
        with open(p, "a", encoding="utf-8") as f:
            f.write("\n===== %s =====\n%s\n" % (_time.strftime("%Y-%m-%d %H:%M:%S"),
                                                  extra or main_agent_content))
        return str(p)
    except Exception:                                              # noqa: BLE001
        return ""
# sub_agents_content
sub_agents_content = prompt_yaml_content["sub_agents"]

# print(sub_agents_content, "\n")

import re
path = r"D:\code\aicode/ai智能体开发实战5期/weekend/deep_search_pro/tests/m3_kb_e2e_test.py"
with open(path, encoding="utf-8") as f:
    t = f.read()
# 直接修 130-132 行中文字面量
old = '                    json={"title": "中文测试", "content": "## 灵康科技\n中文入库测试内容。", "source_kind": "upload"}'
new = "                    json={\"title\": \"\\u4e2d\\u6587\\u6d4b\\u8bd5\", \"content\": \"## \\u7075\\u5eb7\\u79d1\\u6280\\n\\u4e2d\\u6587\\u5165\\u5e93\\u6d4b\\u8bd5\\u5185\\u5bb9\\u3002\", \"source_kind\": \"upload\"}"
if old in t:
    t = t.replace(old, new, 1)
    print("replaced test body")
else:
    print("NOT FOUND")

# check 断言中文文本也转义
for src, dst in [
    ("中文 username 默认无库", "\\u4e2d\\u6587 username \\u9ed8\\u8ba4\\u65e0\\u5e93"),
    ("中文 username 入库 200", "\\u4e2d\\u6587 username \\u5165\\u5e93 200"),
    ("中文 username 检索命中", "\\u4e2d\\u6587 username \\u68c0\\u7d22\\u547d\\u4e2d"),
    ("中文 username 清理", "\\u4e2d\\u6587 username \\u6e05\\u7406"),
]:
    if src in t:
        t = t.replace(src, dst, 1)
        print("replaced:", src)
with open(path, "w", encoding="utf-8", newline="") as f:
    f.write(t)
print("done")

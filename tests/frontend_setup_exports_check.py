# -*- coding: utf-8 -*-
"""前端静态不变量：**模板引用到的标识符，必须出现在 setup() 的 return 里**。

## 这个用例是为什么来的（真实事故，2026-09-16）

用户报障：「知识与技能里配好技能后，聊天框敲 `/` 唤不出技能菜单，完全没反应」。

根因：`static/index.html` 是「in-DOM 模板 + 单个 `setup()` 返回对象」的写法。
`skOnInput` / `skOnKey` / `skillPick` / `skillUnpick` 四个**事件处理函数定义了、却没写进
`return {...}`** → 模板把它们绑成 `undefined`：不报错、不崩、控制台在 prod 下也没有可见警告，
`v-if`/`v-for` 用的**状态**变量都在（界面渲染完全正常），**只有交互静默失效**。

排查过程说明这类缺陷为什么难抓（也是本用例的价值）：
  ① 单测 `m4a_frontend_logic.js` 只抽函数体在 Node 里跑逻辑 → 盖不到「有没有导出」这层缝；
  ② `node --check` / 语法检查全过；
  ③ 真机 DOM 探针里 `_vei` / `__vueParentComponent` 也不可靠（本项目的 Vue 构建两个都取不到，
     阳性对照同样为空）→ 只能靠**静态不变量**这类结构性校验，或者真实点击链路。
  ⇒ 所以固化成用例：**模板引用 ⊆ setup 导出**，一次性覆盖整类问题（不止本次这 4 个）。

运行：
    .venv/Scripts/python.exe tests/frontend_setup_exports_check.py          # 校验（exit 1 = 有缺失）
    .venv/Scripts/python.exe tests/frontend_setup_exports_check.py --fix    # 自动补进 return
"""

import io
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HTML = os.path.join(ROOT, "static", "index.html")

PASS, FAIL = [], []

# 模板里出现但**不需要**由 setup 导出的名字（JS 内建 / 常见单字母 / 字面量）
WHITELIST = {
    "true", "false", "null", "undefined", "this", "$event", "$refs", "Math", "Date", "JSON",
    "Object", "Array", "String", "Number", "Boolean", "parseInt", "parseFloat",
    "encodeURIComponent", "decodeURIComponent", "console", "window", "document",
}
JS_KW = {"true", "false", "null", "undefined", "new", "typeof", "in", "of", "instanceof",
         "return", "if", "else", "void", "delete", "await", "async", "function", "let",
         "const", "var"}
IDENT_RE = re.compile(r"[A-Za-z_$][\w$]*")


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print("%s %s%s" % ("  ✅" if cond else "  ❌", name, (" | " + str(extra)) if extra else ""))


def template_exprs(html: str) -> list[tuple[str, str]]:
    """模板里所有会被 Vue 求值的表达式：{{ }} / v-* / @事件 / :绑定 / 插槽 props。

    只看「<body> 到最后一个 <script>」之间，并剥掉夹在 body 里的 <script> 块
    （否则更早脚本里用模板字符串拼的 HTML 会被当成模板，实测误报 7 个）。
    """
    i = html.find("<body")
    j = html.rfind("<script")
    body = html[i:j] if 0 <= i < j else html
    body = re.sub(r"<script[\s\S]*?</script>", " ", body)
    out: list[tuple[str, str]] = []
    for m in re.finditer(r"\{\{([\s\S]*?)\}\}", body):
        out.append(("mustache", m.group(1)))
    for m in re.finditer(r"(?:^|\s)(v-(?:if|else-if|show|for|model|bind|html|text)|"
                         r"@[\w.-]+|:[\w.-]+)=\"([^\"]*)\"", body):
        out.append((m.group(1), m.group(2)))
    for m in re.finditer(r"(?:^|\s)(?:v-slot[\w:.-]*|#[\w.-]+)=\"([^\"]*)\"", body):
        out.append(("slot", m.group(1)))
    return out


def _pattern_names(pats: str) -> set[str]:
    """解构/元组模式里的名字：`(s, i)` / `{a, b}` / `{a = 1}` / `s`。"""
    names = set()
    for tok in re.split(r"[,\s]+", pats or ""):
        tok = tok.strip().strip("(){}[]")
        if not tok:
            continue
        tok = tok.split("=")[0].split(":")[-1].strip()
        if re.fullmatch(r"[\w$]+", tok):
            names.add(tok)
    return names


def vfor_locals(expr: str) -> set[str]:
    m = re.match(r"\s*([\s\S]*?)\s+(?:in|of)\s+", expr)
    return _pattern_names(m.group(1)) if m else set()


def roots(expr: str) -> set[str]:
    """表达式里的「根」标识符（排除属性名、对象字面量的键、字符串内容）。"""
    cleaned = re.sub(r"'[^']*'|\"[^\"]*\"|`[^`]*`", " ", expr)
    out = set()
    for m in IDENT_RE.finditer(cleaned):
        if m.start() > 0 and cleaned[m.start() - 1] in ".[":
            continue
        if cleaned[m.end():].lstrip().startswith(":") and \
                (cleaned[:m.start()].rstrip().endswith("{") or cleaned[:m.start()].rstrip().endswith(",")):
            continue                                     # 对象字面量的键（:class="{ active: x }"）
        if m.group(0) not in JS_KW:
            out.add(m.group(0))
    return out


def setup_return_names(html: str) -> tuple[set[str], tuple[int, int]]:
    """取最后一个顶层 `return {` 块的键名与其位置。"""
    idx = html.rfind("\n    return {")
    if idx < 0:
        raise SystemExit("找不到 setup 的 return 块——页面结构变了？请检查本用例的解析逻辑")
    start = html.index("{", idx)
    depth, i = 0, start
    while i < len(html):
        if html[i] == "{":
            depth += 1
        elif html[i] == "}":
            depth -= 1
            if depth == 0:
                break
        i += 1
    names = set()
    for line in html[start + 1:i].splitlines():
        for part in line.split("//")[0].split(","):
            k = part.strip().split(":")[0].strip()
            if re.fullmatch(r"[\w$]+", k):
                names.add(k)
    return names, (start, i)


def missing_exports(html: str) -> tuple[dict, set[str], tuple[int, int]]:
    ret, span = setup_return_names(html)
    exprs = template_exprs(html)
    # v-for 声明的名字对同元素及子元素都是局部变量；这里按「全局近似」处理
    # （不做完整作用域解析；漏报风险仅限「v-for 局部变量与 setup 导出同名」，那种情况本就该改名）
    declared = set()
    for kind, expr in exprs:
        if kind == "v-for":
            declared |= vfor_locals(expr)
    missing: dict[str, list[str]] = {}
    for kind, expr in exprs:
        if kind == "slot":
            continue
        if kind == "v-for":
            expr = re.sub(r"^\s*[\s\S]*?\s+(?:in|of)\s+", "", expr)
        for name in roots(expr):
            if name in WHITELIST or name in declared or name in ret:
                continue
            missing.setdefault(name, []).append(kind)
    return missing, ret, span


def main():
    print("=== 前端静态不变量：模板引用 ⊆ setup 导出 ===\n")
    html = io.open(HTML, encoding="utf-8", newline="").read()
    missing, ret, span = missing_exports(html)
    total_refs = len({n for _, e in template_exprs(html) for n in roots(e)})

    print("  setup() 导出 %d 个名字；模板引用 %d 个标识符\n" % (len(ret), total_refs))
    for name, kinds in sorted(missing.items()):
        print("  ❌ %-16s 模板用在: %s" % (name, ", ".join(sorted(set(kinds)))))
    check("模板引用的标识符全部已导出（%d 个）" % total_refs, not missing,
          "缺失: %s" % sorted(missing) if missing else "")
    check("解析到了模板表达式（>0，防止解析器失效导致假通过）", total_refs > 0, total_refs)
    check("解析到了 setup 导出（>100，同上）", len(ret) > 100, len(ret))

    # ------------------------------------------------------------------
    # 不变量 2：go('x') 的目标必须是**真实存在的视图**（2026-09-25 用户报障固化）
    #   阅读器「← 返回」写了 go('report')，而项目里的视图名是复数 reports → 没有任何
    #   v-show 匹配 → 所有视图都被隐藏 → **整页纯色空白**，点任意导航按钮才被拉回来。
    #   这类"死路由"不报错、不崩溃，只是页面空了，纯静态检查就能逮住。
    # ------------------------------------------------------------------
    import re as _re
    views = set(_re.findall(r"v-show=\"view === '([a-z]+)'\"", html))
    goto = {m.group(1) for m in _re.finditer(r"go\('([a-z]+)'\)", html)}
    special = {"shell"}          # go() 内部会把它改写成 skills（见 go() 的分支）
    dead = sorted(t for t in goto if t not in views and t not in special)
    print("\n  真实视图 %d 个：%s" % (len(views), ", ".join(sorted(views))))
    print("  go() 目标 %d 个：%s" % (len(goto), ", ".join(sorted(goto))))
    for t in dead:
        print("  ❌ go('%s') 没有对应的 v-show 视图 → 跳过去会是整页空白" % t)
    check("go() 的目标全部是真实视图（%d 个目标）" % len(goto), not dead, "死路由: %s" % dead if dead else "")
    check("解析到了 go() 目标与视图名（防止解析失效导致假通过）", len(goto) >= 5 and len(views) >= 5,
          "goto=%d views=%d" % (len(goto), len(views)))

    if missing and "--fix" in sys.argv:
        ins = "".join("        %s,\n" % n for n in sorted(missing))
        io.open(HTML, "w", encoding="utf-8", newline="").write(html[:span[1]] + ins + html[span[1]:])
        print("\n  已补进 return：%s" % sorted(missing))

    print("\n" + "=" * 60)
    print("前端 setup 导出不变量：通过 %d，失败 %d" % (len(PASS), len(FAIL)))
    if FAIL:
        print("失败项：")
        for f in FAIL:
            print("  -", f)
        return 1
    print("全部通过 ✅")
    return 0


if __name__ == "__main__":
    sys.exit(main())

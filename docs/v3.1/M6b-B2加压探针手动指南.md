# M6b · B2 加压探针 —— 手动执行指南

> ⚠️ **本指南基于 v1 探针（8 个自造桩工具、`m5bp-*`），自 2026-10-01 起不再适用**，
> 其基线（`tests/fixtures/m5b_pressure_baseline.json`）也已作废删除。
> 现行探针是 **v3 记录器 + 人工评分制度**，跑法见 `docs/v3.1/M6b-探针问题集v2.md` §八。
> 保留本文仅作历史记录。

> 目的：给 **8 工具 × 10 问**的真机路由探针**存一份基线快照**，供后续任何提示词/适配器改动对比 
> 脚本：`tests/live/m5b_pressure_probe.py`（≈ 20~40 分钟真机 要调模型）
> ⚠️ 本指南**照着敲即可**；每条都给"跑完该看到什么"，看到不一样就停下来找我 

---

## 0 · 前置检查（1 分钟）

在**项目根**打开终端（bash / git-bash），逐条跑：

```bash
cd "D:/code/aicode/ai智能体开发实战5期/weekend/deep_search_pro"
unset PYTHONPATH PYTHONIOENCODING # ★ 必做：Hermes 注入的 PYTHONPATH 会污染子进程 
.venv/Scripts/python.exe -c "import sys; print(sys.version)"
```
✅ **期望**：`3.11.15`（若是 3.12 就停下—— venv 被指错了）
✅ **期望**：`unset` 不报错（没设置也不报错）

**不需要起后端服务**—— 这个探针是**自包含**的：它自己建一个**隔离临时账号** 自己装 `.cmd` 桩 跑完自己清理。

**额度提醒**：脚本里**没有** Tavily 调用（烧的是模型额度）；但模型**可能自己补 `internet_search`**（历史实测出现过）→ 若想彻底避免，先确认模型没被配置成自动网搜。

---

## 1 · 先跑自检（几秒 · 不调模型）

```bash
SKIP_LLM=1 .venv/Scripts/python.exe tests/live/m5b_pressure_probe.py
```
✅ **期望**：打印「`[自检] SKIP_LLM=1：只验夹具与被判逻辑（不调模型）`」并**正常结束** 
❌ 若这一步就报错 → **别往下跑** 直接贴给我（说明夹具/环境有问题，不是模型问题）

---

## 2 · 跑全量（20~40 分钟）

```bash
.venv/Scripts/python.exe tests/live/m5b_pressure_probe.py
```
中途会看到：桩目录名、「隔离账号：xxx (id=…) | SKIP_LLM=False」、每题的路由判定行。

☕ 这段时间**不要动项目文件**（尤其别改 `tests/fixtures/router_pool_snapshot.json` 与提示词—— 改了这次基线就白跑）。

跑完最后几行应当包含「**清理临时账号与桩目录**」（脚本自带）。

---

## 3 · 看结果

```bash
.venv/Scripts/python.exe -c "import json;d=json.load(open(r'data/m5b_pressure_result.json',encoding='utf-8'));print(d['ts'],d['user'],len(d['rows']));[print(r) for r in d['rows']]"
```
✅ **期望**：`rows` 有 **10 条**（10 问），每条含问句 / 首调工具 / 是否命中。

**把这段输出整段贴给我**—— 我据此做两件事：① 与历史「标定 10/10」对比（**看首调** 不看自述）② 固化成基线快照。

---

## 4 · 固化基线（我来做，你只需确认文件存在）

基线**必须**放 `tests/fixtures/`—— **绝不能放 `data/`**：
`data/` 被 `.gitignore` 吞掉过，导致复跑时夹具丢失（`m5c_router_bench.py` 的 docstring 里就留着这条事故原话）。

```bash
# PowerShell（原来写的 cp 是 Unix 命令 在 PS 里不可用）：
Copy-Item data\m5b_pressure_result.json tests\fixtures\m5b_pressure_baseline.json -Force
Get-Item tests\fixtures\m5b_pressure_baseline.json | Select-Object Name, Length
# bash / git-bash：
cp data/m5b_pressure_result.json tests/fixtures/m5b_pressure_baseline.json
```

---

## 5 · 核对清理

```powershell
# ★ 2026-09-30 修订：原来这里是一条内联 python（bash 转义）—— 在 PowerShell 里必炸 
# 已收敛成脚本 PowerShell / CMD / bash 跑法完全一样：
.venv/Scripts/python.exe tests/live/m6b_check_leftovers.py
#（如输出有残留且确认都是探针自己的临时物 → 再带 --purge 跑一次）
.venv/Scripts/python.exe tests/live/m6b_check_leftovers.py --purge
```
✅ **期望**：临时账号残留 **0**；`git status` 里**只有** `data/m5b_pressure_result.json`（未跟踪）+ 我们本轮的其他改动 
❌ 若有残留账号/桩目录 → 贴给我（脚本本该自清，残留说明它中途异常退出了）

---

## 6 · 出问题怎么办

| 现象 | 大概率原因 | 处理 |
|---|---|---|
| 第 1 步就失败 | 夹具/环境问题（不是模型） | 停下 贴输出 |
| 中途大量「找不到二进制」 | 桩没装好 / PATH 被覆盖 | 停下 贴输出（脚本本该装桩） |
| 模型**完全不调工具** | 提示词的「信念锚」丢失（历史上拿掉就掉到 5/10） | 跑完照样贴我 这正是基线要发现的事 |
| 卡住不动 >10 分钟 | 某题的模型调用超时 | 可以 Ctrl+C 把已有输出贴我（半份也比我瞎猜强） |

---

## 7 · 这次跑完我会接上什么

1. **B1（方案 A，你已拍板）**：同一探针**连跑 5 轮** → 存**分布**（中位数/极差/标准差）→ 漂移阈值 = **基线中位数 − 2** 
（**不再认单次结果**—— 这条正是为了对症「单次 5/10 曾被误读成能力退化」 那件事）；
2. 把 B2 结果与 B1 分布写进 `docs/v3.1/M6b-测试基线与路由基准方案.md`（走项目流程：争议点 → 你裁决 → 定稿）；
3. S3 的路由基准数字（后台任务跑完即有）与它并列成一张「**当天可验证**」的表。

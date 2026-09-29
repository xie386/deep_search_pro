# -*- coding: utf-8 -*-
"""编译桌面版启动器 `front/desktop/desktop.exe`（GUI 子系统，图标取自 assets/icon.ico）。

用法：
    .venv/Scripts/python.exe front/desktop/build_launcher.py

为什么需要它：`desktop.cmd` 有两个先天限制 —— ① Windows 给 `.cmd` 用的是**按文件类型共享**的
图标（注册表 `.cmd → cmdfile → DefaultIcon`），没法只给某一个 .cmd 单独换；② 双击必然弹一个
控制台黑窗。把启动逻辑编成 exe、图标编进 PE 资源，两个问题一起解决。

编译用 **系统自带的 .NET Framework csc.exe**（Win7+ 都有），不需要额外安装任何东西。
"""
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SRC = HERE / "launcher" / "desktop.cs"
OUT = HERE / "desktop.exe"
ICON = HERE / "assets" / "icon.ico"

CSC_CANDIDATES = [
    r"C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe",
    r"C:\Windows\Microsoft.NET\Framework\v4.0.30319\csc.exe",
]


def find_csc():
    for p in CSC_CANDIDATES:
        if Path(p).is_file():
            return p
    # 兜底：从 %WINDIR% 下找（兼容非 C 盘系统）
    windir = Path(os.environ.get("WINDIR", r"C:\Windows"))
    for sub in ("Microsoft.NET/Framework64", "Microsoft.NET/Framework"):
        for csc in sorted((windir / sub).glob("v*/csc.exe"), reverse=True):
            return str(csc)
    return None


def main():
    if not SRC.is_file():
        print("找不到源码：%s" % SRC)
        return 1
    if not ICON.is_file():
        print("找不到图标：%s（先跑 make_icon.py 生成）" % ICON)
        return 1
    csc = find_csc()
    if not csc:
        print("找不到 csc.exe（需要 .NET Framework 4.x；Windows 自带）")
        return 1
    before = OUT.stat().st_mtime if OUT.is_file() else 0
    cmd = [csc, "/nologo", "/target:winexe",           # winexe = GUI 子系统 → 没有控制台黑窗
           "/win32icon:" + str(ICON),                  # ★ 图标编进 PE 资源 = 文件本身的图标
           "/optimize+", "/out:" + str(OUT), str(SRC)]
    print("[编译] %s" % " ".join('"%s"' % c if " " in c else c for c in cmd[1:]))
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.stdout.strip():
        print(r.stdout.strip())
    if r.stderr.strip():
        print(r.stderr.strip())
    if r.returncode != 0 or not OUT.is_file():
        print("编译失败（退出码 %s）" % r.returncode)
        return 1
    size = OUT.stat().st_size
    print("[完成] %s（%d 字节）%s" % (OUT.name, size,
                                     "已更新" if OUT.stat().st_mtime != before else ""))
    # 校验：PE 子系统必须是 GUI(2)，否则双击还会弹黑窗
    subsystem = _pe_subsystem(OUT)
    print("[校验] PE 子系统 = %s（%s）" % (subsystem, "GUI，无黑窗 ✅" if subsystem == 2 else "⚠️ 不是 GUI！"))
    return 0


def _pe_subsystem(path: Path):
    """读 PE 头里的 Subsystem 字段（2=Windows GUI，3=Console）。"""
    try:
        with open(path, "rb") as f:
            data = f.read(0x400)
        e_lfanew = int.from_bytes(data[0x3C:0x40], "little")
        # PE\0\0 + COFF(20) + OptionalHeader：Subsystem 在 OptionalHeader 偏移 68（PE32+ 也是 68）
        return int.from_bytes(data[e_lfanew + 24 + 68: e_lfanew + 24 + 70], "little")
    except Exception as e:  # noqa: BLE001
        print("（读 PE 头失败：%s）" % e)
        return None


if __name__ == "__main__":
    sys.exit(main())

// 智选情报官 · 桌面版启动器（GUI 子系统：双击不弹黑窗）
//
// 编译方式（用系统自带 .NET 编译器，无需额外装东西）：
//     .venv/Scripts/python.exe static/desktop/build_launcher.py
//
// 它做的事就一件：定位项目 .venv 的 python，用 <项目根> 作为工作目录启动 static/desktop/app.py。
// 参数会原样透传（例：desktop.exe --port 8200 --wait 420），非法参数（含 shell 元字符）会被丢弃。
//
// 为什么不直接用 desktop.cmd：
//   ① cmd.exe 会给 .cmd 的窗口用**自己的**图标（Windows 允许按文件类型设图标，.cmd 全系统共用
//      imageres.dll 里的那个）——没法只给某一个 .cmd 单独换；
//   ② 双击 .cmd 必然弹一个控制台黑窗。
//   本项目把它编译成 exe（图标编进 PE 资源），这两个问题一起解决；.cmd 仍保留作后备。
using System;
using System.Diagnostics;
using System.IO;
using System.Runtime.InteropServices;
using System.Text.RegularExpressions;

class DesktopLauncher
{
    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    private static extern int MessageBoxW(IntPtr hWnd, string text, string caption, uint type);

    private const uint MB_ICONERROR = 0x00000010;

    static void Main(string[] args)
    {
        try
        {
            string here = AppDomain.CurrentDomain.BaseDirectory.TrimEnd('\\');      // ...\static\desktop
            string root = Path.GetFullPath(Path.Combine(Path.Combine(here, ".."), ".."));
            string py = Path.Combine(root, Path.Combine(".venv", Path.Combine("Scripts", "python.exe")));
            string app = Path.Combine(here, "app.py");

            if (!File.Exists(app))
            {
                Fail("找不到 app.py：\n" + app);
                return;
            }
            if (!File.Exists(py))
            {
                Fail("找不到项目 venv 的 python：\n" + py +
                     "\n\n请先按 README 第 2 步创建 .venv，或改用 desktop.cmd（它会显示详细错误）。");
                return;
            }

            // 只透传"安全参数"，避免把 shell 元字符带进命令行
            string extra = "";
            if (args != null)
            {
                var safe = new Regex(@"^[A-Za-z0-9\-_.:=/\\]+$");
                var keep = new System.Collections.Generic.List<string>();
                foreach (string a in args)
                {
                    if (safe.IsMatch(a)) keep.Add(a);
                }
                if (keep.Count > 0) extra = " " + string.Join(" ", keep.ToArray());
            }

            var psi = new ProcessStartInfo(py, "\"" + app + "\"" + extra);
            psi.WorkingDirectory = root;
            psi.UseShellExecute = false;
            psi.CreateNoWindow = true;                                  // 无控制台窗口
            // 与 desktop.cmd 保持一致：强制 UTF-8（否则输出重定向时按 GBK 编码，emoji 会把后端 import 打崩）
            psi.EnvironmentVariables["PYTHONUTF8"] = "1";
            psi.EnvironmentVariables["PYTHONIOENCODING"] = "utf-8";
            // 清掉代理（本机系统代理会拦 localhost）与可能污染的 PYTHONPATH
            psi.EnvironmentVariables.Remove("HTTP_PROXY");
            psi.EnvironmentVariables.Remove("HTTPS_PROXY");
            psi.EnvironmentVariables.Remove("ALL_PROXY");
            psi.EnvironmentVariables.Remove("http_proxy");
            psi.EnvironmentVariables.Remove("https_proxy");
            psi.EnvironmentVariables.Remove("all_proxy");
            psi.EnvironmentVariables.Remove("PYTHONPATH");
            psi.EnvironmentVariables["NO_PROXY"] = "127.0.0.1,localhost";

            Process.Start(psi);
            // 启动器立刻退出：app.py 是独立进程，会继续跑（窗口 + 托盘由它负责）
        }
        catch (Exception ex)
        {
            Fail("启动失败：\n" + ex.Message);
        }
    }

    static void Fail(string msg)
    {
        // GUI 子系统没有控制台，错误只能用弹窗（并记一份日志，便于事后排查）
        try
        {
            string here = AppDomain.CurrentDomain.BaseDirectory;
            File.AppendAllText(Path.Combine(here, "launcher.log"),
                DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss ") + msg.Replace("\n", " | ") + Environment.NewLine);
        }
        catch (Exception) { }
        MessageBoxW(IntPtr.Zero, msg, "智选情报官 · 桌面版", MB_ICONERROR);
    }
}

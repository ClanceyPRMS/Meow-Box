"""喵盒系统托盘图标 —— 纯 ctypes 调 Win32 Shell_NotifyIcon，零第三方依赖。

    pythonw meowbox.py tray      # 推荐：没有控制台窗口
    python  meowbox.py tray      # 会带一个控制台

托盘右键菜单里能：启动/停止全部服务、打开站点、打开目录、重新生成配置、
同步 hosts（会弹 UAC）、开关开机自启、退出。
"""
from __future__ import annotations

import ctypes
import os
import threading
import time
from ctypes import wintypes
from pathlib import Path

from . import config as cfgmod
from . import hosts as hostsmod
from . import runtime
from . import services as svcmod
from . import util, vhost

if os.name != "nt":  # pragma: no cover
    raise RuntimeError("喵盒托盘只支持 Windows")

user32 = ctypes.WinDLL("user32", use_last_error=True)
shell32 = ctypes.WinDLL("shell32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

# ---------------------------------------------------------------- 常量
WM_TRAYICON = 0x8000 + 1          # WM_APP + 1
WM_COMMAND = 0x0111
WM_DESTROY = 0x0002
WM_NULL = 0x0000
WM_LBUTTONUP = 0x0202
WM_LBUTTONDBLCLK = 0x0203
WM_RBUTTONUP = 0x0205
WM_CONTEXTMENU = 0x007B
WM_QUIT = 0x0012

NIM_ADD, NIM_MODIFY, NIM_DELETE = 0, 1, 2
NIF_MESSAGE, NIF_ICON, NIF_TIP = 0x01, 0x02, 0x04
IMAGE_ICON = 1
LR_LOADFROMFILE = 0x0010
SM_CXSMICON, SM_CYSMICON = 49, 50

MF_STRING, MF_SEPARATOR, MF_GRAYED, MF_CHECKED = 0x0000, 0x0800, 0x0001, 0x0008
TPM_RIGHTBUTTON, TPM_RETURNCMD, TPM_NONOTIFY = 0x0002, 0x0100, 0x0080
MB_OK, MB_ICONERROR, MB_ICONINFORMATION = 0x0, 0x10, 0x40
MB_YESNO, MB_ICONQUESTION, IDYES = 0x4, 0x20, 6
PM_REMOVE = 0x0001
ERROR_ALREADY_EXISTS = 183

# 菜单命令 ID
ID_START, ID_STOP, ID_RESTART = 1, 2, 3
ID_OPEN_WWW, ID_OPEN_ROOT, ID_RENDER, ID_HOSTS = 10, 11, 12, 13
ID_SSL = 14
ID_FREE_PORT = 15
ID_SWITCH_WEB = 16
ID_MAIL = 17
ID_PANEL, ID_AUTOSTART, ID_REFRESH, ID_QUIT = 20, 21, 22, 99
ID_SITE_BASE = 100

TITLE = "喵盒 Meow Box"
CLASS_NAME = "MeowBoxTrayWindow"
MUTEX_NAME = "MeowBoxTray_SingleInstance"

# ---------------------------------------------------------------- 类型
LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT,
                             wintypes.WPARAM, wintypes.LPARAM)


class GUID(ctypes.Structure):
    _fields_ = [("Data1", ctypes.c_ulong), ("Data2", ctypes.c_ushort),
                ("Data3", ctypes.c_ushort), ("Data4", ctypes.c_ubyte * 8)]


class WNDCLASSW(ctypes.Structure):
    _fields_ = [
        ("style", wintypes.UINT),
        ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE),
        ("hIcon", wintypes.HICON),
        ("hCursor", wintypes.HCURSOR),
        ("hbrBackground", wintypes.HBRUSH),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
    ]


class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("hWnd", wintypes.HWND),
        ("uID", wintypes.UINT),
        ("uFlags", wintypes.UINT),
        ("uCallbackMessage", wintypes.UINT),
        ("hIcon", wintypes.HICON),
        ("szTip", wintypes.WCHAR * 128),
        ("dwState", wintypes.DWORD),
        ("dwStateMask", wintypes.DWORD),
        ("szInfo", wintypes.WCHAR * 256),
        ("uVersion", wintypes.UINT),
        ("szInfoTitle", wintypes.WCHAR * 64),
        ("dwInfoFlags", wintypes.DWORD),
        ("guidItem", GUID),
        ("hBalloonIcon", wintypes.HICON),
    ]


# 先把签名声明清楚，免得 ctypes 猜错（尤其是宽字符串参数）
user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
user32.CreateWindowExW.argtypes = [
    wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID,
]
user32.CreateWindowExW.restype = wintypes.HWND
user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT,
                                  wintypes.WPARAM, wintypes.LPARAM]
user32.DefWindowProcW.restype = LRESULT
user32.LoadImageW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR, wintypes.UINT,
                              ctypes.c_int, ctypes.c_int, wintypes.UINT]
user32.LoadImageW.restype = wintypes.HANDLE
user32.AppendMenuW.argtypes = [wintypes.HMENU, wintypes.UINT,
                               ctypes.c_size_t, wintypes.LPCWSTR]
user32.MessageBoxW.argtypes = [wintypes.HWND, wintypes.LPCWSTR,
                               wintypes.LPCWSTR, wintypes.UINT]
user32.MessageBoxW.restype = ctypes.c_int
shell32.Shell_NotifyIconW.argtypes = [wintypes.DWORD,
                                      ctypes.POINTER(NOTIFYICONDATAW)]
shell32.Shell_NotifyIconW.restype = wintypes.BOOL
shell32.ShellExecuteW.argtypes = [wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR,
                                  wintypes.LPCWSTR, wintypes.LPCWSTR, ctypes.c_int]
kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL,
                                  wintypes.LPCWSTR]
kernel32.CreateMutexW.restype = wintypes.HANDLE
kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
kernel32.GetModuleHandleW.restype = wintypes.HMODULE

# 返回句柄/指针的函数一定要写 restype，不然 64 位的句柄会被截成 32 位
user32.CreatePopupMenu.restype = wintypes.HMENU
user32.DestroyMenu.argtypes = [wintypes.HMENU]
user32.TrackPopupMenu.argtypes = [wintypes.HMENU, wintypes.UINT, ctypes.c_int,
                                  ctypes.c_int, ctypes.c_int, wintypes.HWND,
                                  wintypes.LPVOID]
user32.TrackPopupMenu.restype = ctypes.c_int
user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT,
                                wintypes.WPARAM, wintypes.LPARAM]
user32.PostQuitMessage.argtypes = [ctypes.c_int]
user32.DestroyWindow.argtypes = [wintypes.HWND]
user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
user32.GetSystemMetrics.argtypes = [ctypes.c_int]
user32.GetSystemMetrics.restype = ctypes.c_int
user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND,
                               wintypes.UINT, wintypes.UINT]
user32.GetMessageW.restype = ctypes.c_int
user32.PeekMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND,
                                wintypes.UINT, wintypes.UINT, wintypes.UINT]
user32.PeekMessageW.restype = wintypes.BOOL
user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
user32.DispatchMessageW.restype = LRESULT
shell32.ShellExecuteW.restype = wintypes.HINSTANCE

_mutex_handle = None


def acquire_single_instance() -> bool:
    """已经有喵盒托盘在跑了就返回 False。"""
    global _mutex_handle
    _mutex_handle = kernel32.CreateMutexW(None, False, MUTEX_NAME)
    if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
        return False
    return True


def startup_dir() -> Path:
    return Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"


def startup_link() -> Path:
    return startup_dir() / "喵盒 Meow Box.vbs"


def autostart_enabled() -> bool:
    return startup_link().is_file()


def set_autostart(enabled: bool) -> None:
    link = startup_link()
    if not enabled:
        link.unlink(missing_ok=True)
        return
    link.parent.mkdir(parents=True, exist_ok=True)
    # 打包版：直接跑 meowbox-tray.exe；源码版：pythonw meowbox.py tray
    argv = runtime.launch_argv("tray")
    run = " ".join(f'"{a}"' for a in argv)
    vbs = (
        "' Meow Box autostart (hidden window)\n"
        "' 必须存成 UTF-16LE，WSH 不认 UTF-8 的 BOM。\n"
        f'CreateObject("WScript.Shell").Run "{run}", 0, False\n'
    )
    link.write_text(vbs, encoding="utf-16")


# ---------------------------------------------------------------- 托盘
class TrayApp:
    def __init__(self, cfg: dict, bins: dict, icon_path) -> None:
        self.cfg = cfg
        self.bins = bins
        self.icon_path = Path(icon_path)
        self.services = svcmod.build(cfg, bins)
        self._hwnd = None
        self._hicon = None
        self._wndproc_ref = WNDPROC(self._wndproc)
        self._added = False

    # ---------- 小工具 ----------
    @property
    def lay(self):
        return cfgmod.layout(self.cfg)

    def _site_names(self):
        return vhost.scan(self.lay["www"])

    def _sites(self):
        tld = self.cfg["sites"]["tld"]
        return [(n, f"http://{vhost.hostname_for(n, tld)}") for n in self._site_names()]

    def _status_text(self) -> str:
        parts = []
        for name, state in svcmod.status(self.services):
            dot = {"运行中": "●", "被外部占用": "◐"}.get(state, "○")
            parts.append(f"{dot} {svcmod.pretty(name)}")
        return "    ".join(parts) or "（没有服务）"

    def _tip(self) -> str:
        parts = []
        for name, state in svcmod.status(self.services):
            short = {"运行中": "开", "被外部占用": "外部占用"}.get(state, "关")
            parts.append(f"{name}:{short}")
        return (f"喵盒 Meow Box  [{' '.join(parts)}]")[:127]

    def _blocked_ports(self) -> list[int]:
        """被「不是喵盒启动的」进程占着的端口（自己的服务不算）。"""
        return svcmod.blocked_ports(self.services)

    def _error(self, text: str) -> None:
        user32.MessageBoxW(None, runtime.localize(text), TITLE, MB_OK | MB_ICONERROR)

    def _info(self, text: str) -> None:
        user32.MessageBoxW(None, runtime.localize(text), TITLE, MB_OK | MB_ICONINFORMATION)

    def _confirm(self, text: str) -> bool:
        return user32.MessageBoxW(None, runtime.localize(text), TITLE,
                                  MB_YESNO | MB_ICONQUESTION) == IDYES

    # ---------- 窗口 / 图标 ----------
    def _create_window(self) -> None:
        hinst = kernel32.GetModuleHandleW(None)
        wc = WNDCLASSW()
        wc.lpfnWndProc = self._wndproc_ref
        wc.hInstance = hinst
        wc.lpszClassName = CLASS_NAME
        if not user32.RegisterClassW(ctypes.byref(wc)):
            err = ctypes.get_last_error()
            if err not in (0, 1410):        # 1410 = 类已注册（重复运行）
                raise OSError(f"RegisterClassW 失败，错误码 {err}")
        self._hwnd = user32.CreateWindowExW(
            0, CLASS_NAME, TITLE, 0, 0, 0, 0, 0, None, None, hinst, None)
        if not self._hwnd:
            raise OSError(f"CreateWindowExW 失败，错误码 {ctypes.get_last_error()}")

        if self.icon_path.is_file():
            cx = user32.GetSystemMetrics(SM_CXSMICON)
            cy = user32.GetSystemMetrics(SM_CYSMICON)
            self._hicon = user32.LoadImageW(None, str(self.icon_path), IMAGE_ICON,
                                            cx, cy, LR_LOADFROMFILE)

    def _nid(self) -> NOTIFYICONDATAW:
        nid = NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        nid.hWnd = self._hwnd
        nid.uID = 1
        nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
        nid.uCallbackMessage = WM_TRAYICON
        nid.hIcon = self._hicon
        nid.szTip = self._tip()
        return nid

    def _add_icon(self) -> bool:
        nid = self._nid()
        self._added = bool(shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid)))
        return self._added

    def _refresh_tip(self) -> None:
        if not (self._hwnd and self._added):
            return
        nid = self._nid()
        shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(nid))

    def _remove_icon(self) -> None:
        if self._hwnd and self._added:
            nid = self._nid()
            shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(nid))
            self._added = False
        if self._hwnd:
            user32.DestroyWindow(self._hwnd)
            self._hwnd = None

    # ---------- 菜单 ----------
    def _show_menu(self) -> None:
        hmenu = user32.CreatePopupMenu()
        try:
            user32.AppendMenuW(hmenu, MF_STRING | MF_GRAYED, 0, TITLE)
            user32.AppendMenuW(hmenu, MF_SEPARATOR, 0, None)
            user32.AppendMenuW(hmenu, MF_STRING, ID_START, "启动全部服务")
            user32.AppendMenuW(hmenu, MF_STRING, ID_STOP, "停止全部服务")
            user32.AppendMenuW(hmenu, MF_STRING, ID_RESTART, "重启服务")
            other = "apache" if svcmod.server_name(self.cfg) == "nginx" else "nginx"
            user32.AppendMenuW(hmenu, MF_STRING, ID_SWITCH_WEB,
                               f"Web 服务器换成 {svcmod.pretty(other)}")
            user32.AppendMenuW(hmenu, MF_SEPARATOR, 0, None)
            user32.AppendMenuW(hmenu, MF_STRING | MF_GRAYED, 0, self._status_text())

            sites = self._sites()
            if sites:
                user32.AppendMenuW(hmenu, MF_SEPARATOR, 0, None)
                for index, (_name, url) in enumerate(sites):
                    user32.AppendMenuW(hmenu, MF_STRING, ID_SITE_BASE + index, f"打开  {url}")

            user32.AppendMenuW(hmenu, MF_SEPARATOR, 0, None)
            user32.AppendMenuW(hmenu, MF_STRING, ID_OPEN_WWW, "打开网站目录")
            user32.AppendMenuW(hmenu, MF_STRING, ID_OPEN_ROOT, "打开喵盒文件夹")
            user32.AppendMenuW(hmenu, MF_STRING, ID_PANEL, "打开控制面板…")
            user32.AppendMenuW(hmenu, MF_STRING, ID_MAIL, "打开邮件收件箱（Mailpit）")
            user32.AppendMenuW(hmenu, MF_SEPARATOR, 0, None)
            user32.AppendMenuW(hmenu, MF_STRING, ID_RENDER, "重新生成配置")
            user32.AppendMenuW(hmenu, MF_STRING, ID_HOSTS, "同步 hosts（需要管理员）")
            user32.AppendMenuW(hmenu, MF_STRING, ID_SSL, "生成 SSL 证书（自签 HTTPS）")
            blocked = self._blocked_ports()
            if blocked:
                ports = "/".join(str(p) for p in blocked)
                user32.AppendMenuW(hmenu, MF_STRING, ID_FREE_PORT,
                                   f"释放被占端口（{ports}）")
            user32.AppendMenuW(hmenu, MF_STRING, ID_REFRESH, "刷新状态")
            flag = MF_STRING | (MF_CHECKED if autostart_enabled() else 0)
            user32.AppendMenuW(hmenu, flag, ID_AUTOSTART, "开机自动启动")
            user32.AppendMenuW(hmenu, MF_SEPARATOR, 0, None)
            user32.AppendMenuW(hmenu, MF_STRING, ID_QUIT, "退出喵盒")

            pt = wintypes.POINT()
            user32.GetCursorPos(ctypes.byref(pt))
            # TrackPopupMenu 的老规矩：必须先 SetForegroundWindow，完了再补一条 WM_NULL
            user32.SetForegroundWindow(self._hwnd)
            cmd = user32.TrackPopupMenu(
                hmenu, TPM_RIGHTBUTTON | TPM_RETURNCMD | TPM_NONOTIFY,
                pt.x, pt.y, 0, self._hwnd, None)
            user32.PostMessageW(self._hwnd, WM_NULL, 0, 0)
        finally:
            user32.DestroyMenu(hmenu)

        if not cmd:
            return
        if ID_SITE_BASE <= cmd < ID_SITE_BASE + 1000:
            sites = self._sites()
            idx = cmd - ID_SITE_BASE
            if 0 <= idx < len(sites):
                util.open_path(sites[idx][1])
            return
        self._on_command(cmd)

    # ---------- 动作 ----------
    def _run_async(self, label, fn) -> None:
        """启动服务可能要好几秒，别把托盘的消息循环卡死。"""
        def worker():
            try:
                fn()
            except Exception as exc:            # noqa: BLE001 —— 兜住所有，弹给用户看
                self._error(f"{label}失败：\n\n{exc}")
            finally:
                self._refresh_tip()
        threading.Thread(target=worker, daemon=True).start()

    def _do_start(self) -> None:
        rows = svcmod.start(self.services, ["all"])
        bad = [(n, e) for n, s, e in rows if s == "失败"]
        if bad:
            raise RuntimeError("\n".join(f"{n}: {e}" for n, e in bad))

    def _do_stop(self) -> None:
        rows = svcmod.stop(self.services, ["all"])
        bad = [(n, e) for n, s, e in rows if s == "失败"]
        if bad:
            raise RuntimeError("\n".join(f"{n}: {e}" for n, e in bad))

    def _do_restart(self) -> None:
        self._do_stop()
        time.sleep(0.5)
        self._do_start()

    def _do_render(self) -> None:
        server = svcmod.web(self.services)
        if server is None:
            raise RuntimeError("配置里没认出 Web 服务器")
        label = svcmod.pretty(server.name)
        if not server.available:
            raise RuntimeError(f"没找到 {label}，先跑 init 或检查 provider 设置")
        server.render()
        rc, out, err = server.test()
        if rc != 0:
            raise RuntimeError(f"{label} 配置有语法错误：\n\n" + (out + err).strip()[-1500:])
        self._info(f"配置已重新生成，{label} 语法自检通过 ✅")

    def _do_switch_web(self) -> None:
        """Apache ↔ Nginx 切换（会先停掉正在跑的那个）。"""
        current = svcmod.server_name(self.cfg)
        target = "apache" if current == "nginx" else "nginx"
        info = svcmod.switch_web(self.cfg, self.bins, target)
        self.services = svcmod.build(self.cfg, self.bins)
        if not info["changed"]:
            self._info(f"已经是 {svcmod.pretty(target)} 了")
            return
        tail = f"\n（已先停掉 {svcmod.pretty(info['from'])}）" if info["stopped"] else ""
        self._info(f"Web 服务器已换成 {svcmod.pretty(target)}{tail}\n\n"
                   f"配置：{info['conf']}\n"
                   f"启动：{svcmod.pretty(target)}")

    def _do_mail(self) -> None:
        """打开 Mailpit 收件箱；没在跑就先拉起来。"""
        mail = self.services.get("mailpit")
        if mail is None or not getattr(mail, "available", False):
            raise RuntimeError("没找到 Mailpit\n\n先装一个： python meowbox.py pkg install mailpit")
        if not mail.running():
            mail.start()
        util.open_path(mail.ui_url())

    def _do_hosts(self) -> None:
        names = [vhost.hostname_for(n, self.cfg["sites"]["tld"]) for n in self._site_names()]
        result = hostsmod.sync(names, apply=True)
        if result.get("error"):
            raise PermissionError(result["error"])
        if result["applied"]:
            self._info("hosts 已更新 ✅\n\n" + "\n".join(f"127.0.0.1  {h}" for h in names))
        else:
            self._info("hosts 已经是最新的，不用改")

    def _hosts_with_elevation(self) -> None:
        """没管理员权限时，弹 UAC 用一个提权的控制台去写 hosts。"""
        if util.is_admin():
            self._run_async("同步 hosts ", self._do_hosts)
            return
        if not self._confirm("改 hosts 需要管理员权限。\n\n要现在弹出 UAC 提权去写吗？"):
            return
        argv = runtime.console_argv("hosts", "--apply")
        rc = shell32.ShellExecuteW(None, "runas", argv[0],
                                   runtime.quote_args(argv[1:]),
                                   str(self.lay["root"]), 1)
        if rc <= 32:
            self._error(f"提权失败（ShellExecute 返回 {rc}）。\n\n"
                        f"可以手动用管理员终端跑： {runtime.localize('python meowbox.py hosts --apply')}")

    def _do_ssl(self) -> None:
        from . import ssl as sslmod
        info = sslmod.provision(self.cfg, self.bins, force=False, do_trust=True)
        server = svcmod.web(self.services)
        if server is not None and server.available:
            label = svcmod.pretty(server.name)
            was_running = server.running()
            server.render()
            rc, out, err = server.test()
            if rc != 0:
                sslmod.disable(self.cfg)
                server.render()
                raise RuntimeError(f"{label} 语法检查没过，已把 SSL 关回去：\n"
                                   + (out + err).strip()[-800:])
            if was_running:
                server.stop()
                server.start()
        lines = [f"https://{h}" for h in info["hosts"]]
        tail = ("\n\n根证书已装进系统信任库，浏览器不会报警告。"
                if info["trusted"] else
                "\n\n⚠️ 根证书没装进信任库，浏览器还会警告。")
        self._info("HTTPS 已就绪\n\n" + "\n".join(lines) + tail)

    def _do_free_ports(self) -> None:
        messages = []
        for port in self._blocked_ports():
            ok, message = svcmod.free_port(self.cfg, self.services, port)
            messages.append(("" if ok else "失败：") + message)
        self._info("\n".join(messages) if messages else "所有端口都空着")

    def _on_command(self, cmd: int) -> None:
        if cmd == ID_START:
            self._run_async("启动服务 ", self._do_start)
        elif cmd == ID_STOP:
            self._run_async("停止服务 ", self._do_stop)
        elif cmd == ID_RESTART:
            self._run_async("重启服务 ", self._do_restart)
        elif cmd == ID_SWITCH_WEB:
            self._run_async("切换 Web 服务器 ", self._do_switch_web)
        elif cmd == ID_MAIL:
            self._run_async("打开邮件收件箱 ", self._do_mail)
        elif cmd == ID_RENDER:
            self._run_async("重新生成配置 ", self._do_render)
        elif cmd == ID_HOSTS:
            self._hosts_with_elevation()
        elif cmd == ID_SSL:
            self._run_async("生成 SSL 证书 ", self._do_ssl)
        elif cmd == ID_FREE_PORT:
            self._run_async("释放被占端口 ", self._do_free_ports)
        elif cmd == ID_OPEN_WWW:
            util.open_path(self.lay["www"])
        elif cmd == ID_OPEN_ROOT:
            util.open_path(self.lay["root"])
        elif cmd == ID_PANEL:
            util.popen(runtime.launch_argv("gui"), cwd=self.lay["root"])
        elif cmd == ID_REFRESH:
            self._refresh_tip()
        elif cmd == ID_AUTOSTART:
            if autostart_enabled():
                set_autostart(False)
                self._info("已关闭开机自启")
            else:
                set_autostart(True)
                self._info("已开启开机自启\n\n（在「启动」文件夹放了一个隐藏启动脚本）")
        elif cmd == ID_QUIT:
            self._remove_icon()
            user32.PostQuitMessage(0)

    # ---------- 消息 ----------
    def _wndproc(self, hwnd, msg, wparam, lparam):
        try:
            if msg == WM_TRAYICON:
                if lparam in (WM_RBUTTONUP, WM_CONTEXTMENU):
                    self._show_menu()
                elif lparam == WM_LBUTTONDBLCLK:
                    sites = self._sites()
                    util.open_path(sites[0][1] if sites else f"http://localhost:{self.cfg['web']['http_port']}/")
                return 0
            if msg == WM_COMMAND:
                self._on_command(wparam & 0xFFFF)
                return 0
            if msg == WM_DESTROY:
                user32.PostQuitMessage(0)
                return 0
        except Exception as exc:                # noqa: BLE001
            print(f"[喵盒托盘] 处理消息出错: {exc}", flush=True)
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    # ---------- 跑 ----------
    def run(self, timeout: float | None = None) -> int:
        self._create_window()
        ok = self._add_icon()
        print(f"喵盒托盘已启动（Shell_NotifyIcon -> {ok}，图标：{self.icon_path}）", flush=True)
        try:
            if timeout is not None:              # 自检模式：泵一会儿消息就退
                deadline = time.time() + timeout
                msg = wintypes.MSG()
                while time.time() < deadline:
                    while user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, PM_REMOVE):
                        if msg.message == WM_QUIT:
                            return 0
                        user32.TranslateMessage(ctypes.byref(msg))
                        user32.DispatchMessageW(ctypes.byref(msg))
                    time.sleep(0.03)
                return 0 if ok else 1

            msg = wintypes.MSG()
            while True:
                ret = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
                if ret in (0, -1):
                    break
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
        finally:
            self._remove_icon()
        return 0


def find_icon(root) -> Path:
    """托盘图标：根目录里有就用（可以自己换），没有就退回包里那份。"""
    return runtime.assets_dir(root) / "meowbox.ico"


def run_tray(root, timeout: float | None = None) -> int:
    if not acquire_single_instance():
        print("喵盒托盘已经在跑了（没重复开）")
        return 0
    cfg = cfgmod.load(root)
    bins = cfgmod.discover(cfg)
    icon = find_icon(root)
    if not icon.is_file():
        from . import icon as iconmod
        icon = Path(root) / "assets" / "meowbox.ico"
        iconmod.write_ico(icon)
    app = TrayApp(cfg, bins, icon)
    return app.run(timeout=timeout)


if __name__ == "__main__":
    import sys
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else runtime.app_root()
    raise SystemExit(run_tray(root, timeout=3.0))

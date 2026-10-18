"""服务注册表：把各个组件统一成一张表，方便一键启停。

喵盒同一时间只跑**一个** Web 服务器（Apache 和 Nginx 都想要 80 端口），
所以这里按配置里的 `web.server` 决定把谁放进表里，另一个根本不构造。
想换服务器就调 `switch_web()`：它会先停下正在跑的那个，再改配置、重渲染。
"""
from __future__ import annotations

from . import config as cfgmod
from . import util
from .apache import Apache
from .mailpit import Mailpit
from .mysql import MySQL
from .nginx import Nginx

#: 能当 Web 服务器的组件名
WEB_SERVERS = ("apache", "nginx")

#: 界面上显示的名字
PRETTY = {"apache": "Apache", "nginx": "Nginx", "mysql": "MySQL", "mailpit": "Mailpit"}


def server_name(cfg: dict) -> str:
    """当前配置要用的 Web 服务器（认不出来就当 Apache）。"""
    web = cfg.get("web") or {}
    name = (web.get("server") or "apache").strip().lower()
    return name if name in WEB_SERVERS else "apache"


def web_servers(cfg: dict, bins: dict) -> dict:
    """两个 Web 服务器都建出来（切换、查状态时用）。"""
    return {"apache": Apache(cfg, bins), "nginx": Nginx(cfg, bins)}


def build(cfg: dict, bins: dict) -> dict:
    """返回 {名字: 服务对象} —— 当前选中的 Web 服务器 + MySQL +（可选的）Mailpit。"""
    services: dict = {}
    name = server_name(cfg)
    services[name] = web_servers(cfg, bins)[name]
    if (cfg.get("mysql") or {}).get("enabled", True):
        services["mysql"] = MySQL(cfg, bins)
    if (cfg.get("mailpit") or {}).get("enabled", True):
        services["mailpit"] = Mailpit(cfg, bins)
    return services


def web(services: dict):
    """从服务表里把当前的 Web 服务器对象挑出来（没有就 None）。"""
    for name in WEB_SERVERS:
        if name in services:
            return services[name]
    return None


def pretty(name: str) -> str:
    return PRETTY.get(name, name)


def switch_web(cfg: dict, bins: dict, name: str) -> dict:
    """把 Web 服务器换成 apache / nginx。

    步骤：① 停掉正在跑的那个（别抢端口）② 改配置和 autostart
    ③ 渲染新服务器配置 ④ 语法自检；自检不过就原样退回去。
    """
    name = (name or "").strip().lower()
    if name not in WEB_SERVERS:
        raise ValueError(f"只认 apache / nginx，不认 {name!r}")
    current = server_name(cfg)
    if name == current:
        return {"from": current, "to": name, "changed": False, "stopped": None}

    targets = web_servers(cfg, bins)
    new = targets[name]
    if not new.available:
        package = "Apache-2.4.66" if name == "apache" else "Nginx-1.29.5"
        raise RuntimeError(
            f"没找到 {pretty(name)} 的程序目录。\n"
            f"要么把 meowbox.config.json 的 provider 指到装了它的目录，"
            f"要么 python meowbox.py pkg install {package}")

    old = targets[current]
    stopped = False
    if old.running():
        old.stop()
        stopped = True
    elif old.orphan():
        raise RuntimeError(
            f"{pretty(current)} 还在跑，但不是喵盒启动的（管理员权限那种），普通权限停不掉。\n"
            f"先 python meowbox.py freeport {old.ports[0]}，或者手动关掉它。")

    cfg["web"]["server"] = name
    rest = [x for x in (cfg.get("autostart") or []) if x not in WEB_SERVERS]
    cfg["autostart"] = [name] + rest
    cfgmod.save(cfg)

    new.render()
    rc, out, err = new.test()
    if rc != 0:
        # 语法不过 —— 把配置退回去，别留一个起不来的环境
        cfg["web"]["server"] = current
        cfg["autostart"] = [current] + rest
        cfgmod.save(cfg)
        old.render()
        raise RuntimeError(
            f"{pretty(name)} 的配置语法检查没过，已经退回 {pretty(current)}：\n"
            + (out + err).strip())

    return {"from": current, "to": name, "changed": True, "stopped": stopped,
            "conf": new.conf, "test": (rc, out, err)}


def _resolve(services: dict, names):
    if not names:
        return list(services)
    out = []
    for name in names:
        if name == "all":
            out.extend(services)
        elif name == "web":
            # 「web」是个别名：当前的 Web 服务器叫啥都行
            out.extend(n for n in services if n in WEB_SERVERS)
        else:
            out.append(name)
    # 去重且保序
    seen, ordered = set(), []
    for n in out:
        if n not in seen:
            seen.add(n)
            ordered.append(n)
    return ordered


def service_ports(services: dict) -> list[int]:
    """喵盒这些服务会用到的端口。"""
    ports: list[int] = []
    for svc in services.values():
        found = list(getattr(svc, "ports", []) or [])
        if not found and hasattr(svc, "port"):
            found = [svc.port]
        ports.extend(found)
    return ports


def blocked_ports(services: dict) -> list[int]:
    """被「不是喵盒启动的」进程占着的端口。

    判据不能只看 pid：`mysqld.exe` 在 Windows 上会**派生子进程**去监听，
    真正占着 3306 的是子进程，跟喵盒记的父进程 pid 对不上。
    所以逻辑是：只要这个服务在喵盒这边是"跑着"的，它该占的端口就算它的；
    只有**服务没跑、端口却被占着**，才算被外部占用。
    """
    blocked: list[int] = []
    for name, svc in services.items():
        runner = getattr(svc, "running", None)
        if runner is None or runner():
            continue
        ports = list(getattr(svc, "ports", [])) or ([svc.port] if hasattr(svc, "port") else [])
        for port in ports:
            if util.port_in_use(port):
                blocked.append(port)
    return blocked


def free_port(cfg: dict, services: dict, port: int) -> tuple[bool, str]:
    """释放一个端口。

    两类进程要区别对待：
    * mysqld —— **先走 mysqladmin 优雅关闭**，taskkill /F 会损坏 InnoDB；
    * nginx  —— 先 `nginx -s quit` 让它把手上的请求做完，实在不走再硬砍。
    """
    holder = util.port_holder(port)
    if not holder:
        return True, f"端口 {port} 本来就空着"
    pid, name = holder
    low = name.lower()

    if low.startswith("mysqld"):
        mysql = services.get("mysql")
        if mysql is not None and getattr(mysql, "available", False):
            if mysql.shutdown_external(pid):
                return True, f"已让占着 {port} 的 {name} (pid {pid}) 正常关闭"
        return False, (f"{name} (pid {pid}) 是数据库进程，硬杀会损坏 InnoDB 数据，"
                       "喵盒不会这么干。\n"
                       "请用 mysqladmin shutdown，或者从 Laragon / 喵盒正常停止它。")

    if low.startswith("nginx"):
        nginx = services.get("nginx") or Nginx(cfg, cfgmod.discover(cfg))
        if getattr(nginx, "available", False) and nginx.shutdown_external(pid):
            return True, f"已让占着 {port} 的 {name} (pid {pid}) 优雅退出"

    return util.free_port(port)


def start(services: dict, names=None) -> list[tuple[str, str, str]]:
    results = []
    for name in _resolve(services, names):
        svc = services.get(name)
        if svc is None:
            results.append((name, "跳过", "没有这个服务"))
            continue
        if not getattr(svc, "available", True):
            results.append((name, "跳过", "没找到程序目录"))
            continue
        try:
            pid = svc.start()
            results.append((name, "已启动", f"pid {pid}"))
        except Exception as exc:  # 端口冲突、配置错误等
            results.append((name, "失败", str(exc)))
    return results


def stop(services: dict, names=None) -> list[tuple[str, str, str]]:
    results = []
    for name in _resolve(services, names):
        svc = services.get(name)
        if svc is None:
            results.append((name, "跳过", "没有这个服务"))
            continue
        try:
            okay = svc.stop()
            results.append((name, "已停止", "" if okay else "本来就没在跑"))
        except Exception as exc:
            results.append((name, "失败", str(exc)))
    return results


def status(services: dict) -> list[tuple[str, str]]:
    return [(name, svc.status()) for name, svc in services.items()]

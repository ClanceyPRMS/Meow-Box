# 参考：命令一览 / 目录结构 / 还没做的事

> 这一篇从 [README](../README.md) 里搬出来的长文。它没被删，只是不该挤在首页。

## 命令一览

| 命令 | 作用 |
|---|---|
| `init` | 初始化：建目录、写 `meowbox.config.json` |
| `info` | 显示配置 + 探测到的组件路径 |
| `list` | 列出 `www/` 里的站点 |
| `site add/remove <名字>` | 新建 / 删除站点 |
| `tld [后缀]` | 查看 / 修改站点域名后缀（**默认 `.meow`**）|
| `web [apache\|nginx]` | 查看 / 切换 Web 服务器 |
| `mail [status\|open\|clear]` | Mailpit：看状态 / 打开收件箱 / 清空信箱 |
| `render` | 重新生成所有配置文件（Nginx 会自动 reload） |
| `check` | 让当前 Web 服务器做语法自检（`httpd -t` / `nginx -t`） |
| `hosts [--apply]` | 预览 / 写入 hosts 条目（`--apply` 时顺带把当前后缀和每个站点域名加进代理绕过列表） |
| `ssl [on\|check\|list\|renew\|trust\|untrust\|off]` | 自签 HTTPS 证书（一键）/ `check` = 六层体检 |
| `logs [clean] [--dry-run]` | 看日志占用 / 清理太旧（7 天）、太大（2MB）的 |
| `tray [--selftest]` | 启动系统托盘图标 |
| `gui` | 打开图形控制面板 |
| `shortcut` | 生成双击启动器（`.vbs`）|
| `autostart [on\|off]` | 开机自启开关（「启动」文件夹里的 `.vbs`，不进注册表） |
| `portable [--fix]` | 便携检查 / 把配置改成相对路径 |
| `migrate [--yes]` | 从 Laragon 迁站点和数据库（默认只预览；`--tld auto` 保持原后缀）|
| `vendor [--yes]` | 彻底自带：组件复制进 `bin/`，`provider` 设成 null |
| `start/stop [apache\|nginx\|mysql\|mailpit\|all\|web]` | 服务启停（`web` = 当前那个）|
| `status` | 服务状态 + 端口占用 |
| `restart [apache\|nginx\|mysql\|mailpit\|all]` | 重启服务 |
| `freeport <端口>` | 把占着端口的进程关掉（必要时弹 UAC）|
| `db init` | 初始化 MySQL 数据目录（root 无密码） |
| `pkg list / install <名字>` | 看清单 / 下载软件包（清单里写了 `sha256=` 就强制校验，见下）|
| `php list` | 装了哪些 PHP 版本、默认是哪个、各占哪个 FastCGI 端口 |
| `php install [8.5]` | 从 php.net 下载并安装一个 PHP（x64 线程安全包）|
| `php use <版本>` | 换默认 PHP 版本 |
| `php site <站点> [版本\|default]` | 看 / 改单个站点用哪个 PHP |
| `php ini / open [版本]` | 打开 php.ini / PHP 目录 |
| `php restart` | 只重启 php-cgi 进程池（站点 504 卡住时用，不动 Web 服务器） |
| `php myadmin` | 安装（第一次）并打开 phpMyAdmin |

### 软件包的完整性校验（`packages.conf`）

一行一个 `名字=地址`，地址后面可以再空一格写 `sha256=`：

```text
php=https://windows.php.net/downloads/releases/php-8.4.26-Win32-vs17-x64.zip sha256=6f1c5e…
```

- **写了哈希** → 下载完先比对，对不上就**删文件并报错**，绝不往下解压；
  缓存命中也校验（缓存里可能是上次下坏的半截文件）。
- **没写** → 照常装，但会把实际哈希打印出来，复制进清单就锁上了。
- `pkg list` 输出里带 🔒 的就是已经锁住的。

为什么值得锁：`bin/` 里落地的程序会被 Apache / PHP 直接执行，中间没人看过内容。
上游镜像被污染、DNS 被劫持、缓存里的残文件 —— 这三种只有哈希能发现。

## 目录结构

```
D:\meowbox\
├─ meowbox.py              命令行入口（源码模式）
├─ pyproject.toml          项目清单（名字/版本/Python 要求/开发依赖）
├─ uv.lock                 依赖配方卡（精确版本，别手改）
├─ .python-version         开发用的 Python 版本（3.14）
├─ .venv\                  uv 建的虚拟环境（不进版本库）
├─ tests\                  单元测试（pytest；纯逻辑，两个平台都能跑）
├─ docs\                   长文：Linux / 功能详解 / 参考 / 跨系统开发 / 设计笔记
├─ .github\workflows\      CI（push/PR 跑测试；手动触发打 Windows 包）
├─ SECURITY.md             安全问题怎么报（别开公开 Issue）
├─ CONTRIBUTING.md         改代码 / 提 PR 的硬规矩
├─ CHANGELOG.md            更新日志
├─ LICENSE                 MIT
├─ meowbox.exe             ┐
├─ meowbox-tray.exe        │ 打包版三个入口（双击就用，不用装 Python）
├─ meowbox-panel.exe       ┘
├─ _internal\              打包版的依赖 + 内置 templates/assets（`build_exe.py` 生成）
├─ build_exe.py            一键打包（PyInstaller 外壳：打包 → 放进根目录 → 自检）
├─ meowbox.spec            PyInstaller 配方（三个 exe 共用一份 _internal）
├─ packaging\              三个 exe 的入口脚本（entry_cli / entry_tray / entry_panel）
├─ meowbox.config.json     配置（JSON，人肉可读）
├─ packages.conf           软件下载清单
├─ meow\                   代码
│   ├─ config.py           配置 + 二进制探测
│   ├─ runtime.py          运行方式门面（源码 / exe 的路径、拉起、输出编码都在这）
│   ├─ util.py             跑命令 / 看端口 / 判权限
│   ├─ hosts.py            hosts 成对标记读写
│   ├─ vhost.py            扫描 www/ → 生成虚拟主机（Apache / Nginx 各一套）
│   ├─ php.py              PHP 多版本：扫描 / 下载安装 / 写 php.ini / php-cgi 进程池
│   ├─ download.py         下载解压（纯标准库，冻结成 exe 也不缺工具）
│   ├─ doctor.py           HTTPS 体检（六层排查 + 记录到 logs/https-check.log）
│   ├─ logs.py             日志滚动 / 统计 / 清理
│   ├─ apache.py           Apache 管家
│   ├─ nginx.py            Nginx 管家（顺带看管 php-cgi）
│   ├─ mailpit.py          Mailpit 管家（本地假邮箱）
│   ├─ mysql.py            MySQL 管家
│   ├─ services.py         服务注册表 + Web 服务器切换
│   ├─ packages.py         下载解压
│   ├─ portable.py         便携检查（有没有写死的路径）
│   ├─ vendor.py           彻底自带（组件复制进 bin/ + 改 php.ini）
│   ├─ launcher.py         双击启动器（.vbs）
│   ├─ tray.py             系统托盘（纯 ctypes Win32）
│   ├─ gui.py              控制面板（tkinter，菜单栏 = 全功能）
│   └─ icon.py             自己画猫爪图标（纯 Python 生成 .ico/.png）
├─ assets\                 图标 + 截图
├─ templates\              配置模板（@名字@ 占位符）
├─ etc\                    生成的配置（不要手改）
├─ logs\                   日志（+ https-check.log 体检记录）
├─ run\                    PID 文件
├─ data\                   MySQL 数据目录
├─ dist\ build\            打包中间产物（可以随时删，不影响运行）
└─ www\                    你的网站
```

## 还没做（TODO）

- [x] Nginx 支持（`meow/nginx.py` + `web` 命令 + php-cgi FastCGI）
- [x] Mailpit 邮件捕获（`meow/mailpit.py` + `mail` 命令）
- [x] 便携化：配置里不写死路径，`root` 是 `"."`，网站目录是相对 `www`
- [x] 图形界面覆盖全部功能（菜单栏：文件 / 服务 / 站点 / 工具）
- [x] 一键把 Laragon 的站点/数据库迁过来（`meow/migrate.py` + `migrate` 命令）
- [x] 彻底自带：组件复制/下载进 `bin/`，`provider` 设为 null（`meow/vendor.py`）
- [x] 打包成 exe（`build_exe.py` + `meowbox.spec`）：三个入口共用一份 `_internal/`，
      双击不弹黑框、机器上不用装 Python；运行差异全收在 `meow/runtime.py`
- [x] HTTPS 六层体检 + 一键修复（`meow/doctor.py`、面板「体检」按钮、`ssl check`）
- [x] 日志：体检记录自动滚动 + `logs clean`（太旧/太大）+ 面板启动 20MB 提醒
      + 每周日 Hermes cron 自动清一次
- [x] PHP 多版本：`bin/php/` 放几个就是几个，站点级切换 + `php install` 从官方源下载
      + PHP 管理菜单（换默认、开 php.ini、phpMyAdmin）
- [x] 面板不再卡：状态查询改走 Win32 API（`GetExtendedTcpTable` /
      `QueryFullProcessImageNameW`），采集丢后台线程
- [x] 开面板时自动带起托盘（`ui.tray_with_panel`，可在文件菜单里关）
- [ ] 站点级 rewrite 配置（现在只能在 `auto.*.conf` 里手加，重渲就没了）

> **明确不做**：装成 Windows 服务。喵盒的定位就是"绿色便携" —— 不注册服务、
> 不写注册表、不碰系统目录，关掉就走人，复制走就能用。
>
> 同样的理由，**定时清理不用 Windows 计划任务**（它两样都沾），而是挂在 Hermes
> 自己的 cron 上：删掉那个任务就等于没发生过。

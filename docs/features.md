# 功能详解：图形界面 / HTTPS / Web 服务器 / PHP / Mailpit / 迁移 / 打包

> 这一篇从 [README](../README.md) 里搬出来的长文。它没被删，只是不该挤在首页。

## 图形界面

```bash
python meowbox.py shortcut     # 生成两个双击启动器（.vbs，不弹黑框）
python meowbox.py tray         # 启动系统托盘
python meowbox.py gui          # 打开控制面板窗口
python meowbox.py autostart on # 开机自启
```

- **系统托盘**（`tray`）：右键猫爪图标 → 启动/停止全部服务、打开站点、
  打开目录、打开控制面板、重新生成配置、同步 hosts（会自动弹 UAC 提权）、
  开机自启开关、退出。纯 `ctypes` 调 Win32 `Shell_NotifyIcon`，
  **没装任何第三方库**。双击图标 = 打开第一个站点。
- **控制面板**（`gui`）：`tkinter` 写的窗口 —— 服务状态（绿色圆点）、
  站点列表（双击打开）、域名后缀、按钮式操作、日志。
- 双击 `meowbox-tray.vbs` / `meowbox-panel.vbs` 就能启动。
- 图标是自己画的：`python -m meow.icon` 会重新生成
  `assets/meowbox.ico`（多尺寸）和预览图，不用 Pillow。

## HTTPS（自签证书）

```bash
python meowbox.py ssl          # 一键：建 CA → 签证书 → 开 HTTPS → 装信任库
python meowbox.py ssl check    # 体检：一层层查为什么没有小绿锁（缺哪层说哪层）
python meowbox.py ssl list     # 看证书和信任状态
python meowbox.py ssl renew    # 重新签发全部
python meowbox.py ssl trust    # 只把根证书装进信任库
python meowbox.py ssl untrust  # 从信任库移除
python meowbox.py ssl off      # 关掉 HTTPS（证书都保留）
```

### 小绿锁不亮？点「体检」

面板里 HTTPS 状态旁边有个 **体检** 按钮（菜单「工具 → HTTPS 证书 → HTTPS 体检」也有）。
它按真实的故障顺序一层层查，而不是让你自己猜：

| 层 | 查什么 | 典型症状 |
|---|---|---|
| 1 开关 | `web.ssl` 开着吗、端口几号 | 根本没开 HTTPS |
| 2 根证书 | CA 在不在、**装进信任库了吗** | 能开 https 但浏览器红锁 |
| 3 站点证书 | `.crt/.key` 在不在、链验得过吗、SAN 有域名吗、到期没 | 换过后缀/换过 CA 后证书对不上 |
| 4 SSL 虚拟主机 | `sites-enabled/auto.<域名>-ssl.conf` 生成了吗 | **换过 TLD 最容易漏**：证书还停在旧后缀 |
| 5 端口 | 443 是谁在听、是不是当前 Web 服务器 | 被别的程序占了 |
| 6 **真握手** | 带 SNI 真握一次，把服务器递过来的证书 CN 跟域名比 | 抓到「443 上其实是明文 HTTP」这种光看配置看不出来的 |

第 6 层是关键：如果 443 落脚到普通 HTTP 站点（没有 SSL 虚拟主机），
TLS 客户端收到的是 HTTP 明文响应，OpenSSL 报 `packet length too long`，
curl 报 `SEC_E_INVALID_TOKEN` —— 体检会直接说「拿到的是明文 HTTP 响应」，
而不是让你去怀疑证书或 DLL。

发现问题时窗口底下有 **一键修复**（重签全部证书 → 重生成配置 → 重启），
点一下就走完整条链路，然后再点「重新体检」确认全绿。

装完 `https://blog.meow` 就是**小绿锁**。原理（跟 mkcert 一样）：

1. 生成一张只属于这台机器、10 年有效的根证书（CA）
2. 用它给每个站点签**带 SAN** 的证书（现代浏览器只看 SAN，不看 CN）
3. 把根证书装进**当前用户**的信任库（`certutil -addstore -user Root`，**不需要管理员**）
4. 打开 `web.ssl` → Apache 监听 443，并为每个站点生成 SSL 虚拟主机

证书都在 `etc/ssl/`：`meowbox-ca.crt` 是根证书，`sites/<域名>.crt|.key` 是站点证书。
不想要了就 `ssl untrust` + `ssl off`，根证书随时能删。

> Firefox 默认会读 Windows 信任库；如果它自己建了库，去
> `about:config` 把 `security.enterprise_roots.enabled` 设成 `true`。

## Web 服务器：Apache 还是 Nginx

喵盒**同一时间只跑一个** Web 服务器（两个都想占 80 端口），随时能换：

```bash
python meowbox.py web            # 看现在用的是哪个
python meowbox.py web nginx      # 换成 Nginx（会先停掉正在跑的 Apache）
python meowbox.py web apache     # 换回来
python meowbox.py start          # 之后 start/stop/status 管的都是它
```

换的时候会自动：停掉另一个 → 改 `web.server` 和 `autostart` → 渲染新配置 →
语法自检。**自检不过就原样退回**，不会给你留一个起不来的环境。

两个服务器的行为基本一致：站点 `<名字>.meow`、HTTPS、日志都照旧。
唯一要注意的是 **`.htaccess` 只有 Apache 认** —— 用 Nginx 时把重写规则写成
`try_files` / `location`（喵盒的模板里已经带了 `try_files`）。

**Nginx 的 PHP 是怎么跑的**：Nginx 自己不解释 PHP，喵盒会另外拉起 `php-cgi.exe -b 127.0.0.1:9000`（FastCGI）。
`start nginx` 会把这一对一起起来，`stop` 一起收掉；端口嫌挤就在配置里改
`php.fastcgi_port`。改完配置不用重启：`render` 会自动 `nginx -s reload` 热加载，
连接都不带断的。

装了多个 PHP 版本时，**每个版本各起一个 php-cgi**（端口见 `php list`），
站点用它自己指定那个版本 —— 详见下一节。

## PHP：多版本 + 站点级切换

`bin/php/` 下面放几个版本就是几个版本，喵盒自己扫，不用登记：

```bash
python meowbox.py php list               # 装了哪些、默认是哪个、各占哪个端口
python meowbox.py php install 8.5        # 从 php.net 下 Windows x64 线程安全包
python meowbox.py php use 8.5            # 换默认版本（没单独指定的站点都跟着它）
python meowbox.py php site blog 8.5      # 只让 blog 用 8.5
python meowbox.py php site blog default  # 让它跟回默认版本
python meowbox.py php ini                # 打开默认版本的 php.ini
python meowbox.py php myadmin            # 装 / 打开 phpMyAdmin
```

配置里就两行：

```json
"php":   { "fastcgi_port": 9000, "version": "8.3" },
"sites": { "php": { "blog": "8.5" } }
```

**两个版本怎么同时跑**：

* **Apache**：mod_php 一个进程只能挂一个 PHP，所以**默认版本走 mod_php**，
  其他版本各起一个 `php-cgi.exe -b 127.0.0.1:900x`，站点 vhost 里
  `SetHandler "proxy:fcgi://127.0.0.1:900x/"` 把 `.php` 转过去。
* **Nginx**：本来就不跑 PHP，所有站点都走 php-cgi，一个版本一个端口。

端口分配：默认版本永远拿 `php.fastcgi_port`（9000），其余按版本号升序拿
9001、9002…… 只要不改默认版本，端口就不会跳。换默认版本或装新版本之后
`restart` 一下（面板里换个版本会自动重启）。

**每个版本默认起 8 个 php-cgi worker**（`php.cgi_children`）。这一步不能省：
`php-cgi -b` 默认是单进程顺序处理，一个慢请求会把整个端口堵死 —— 实测一次卡住
会让同一个 PHP 版本下的所有站点连续 504 六十秒。真卡住了用 `php restart`
（面板 PHP 菜单里也有），只换进程池，不动 Apache/Nginx。

**装 PHP 必须用线程安全（TS）的官方包** —— mod_php 只能挂 TS。`php install`
自动挑那个（文件名里没有 `-nts-`），装完顺手生成 php.ini：`extension_dir`
指到自己的 `ext/`、session 落到 `run/php-session`、`sendmail_path` 直接接到
Mailpit，并按 `ext/` 里实际存在的 DLL 开启 curl / gd / intl / zip / opcache
等 20 个常用扩展。缓存包在 `cache/php/`（约 36MB），删了也不影响已装的。

**phpMyAdmin**：`php myadmin` 会从官方源下载最新版装到 `www/phpmyadmin`
（于是它就是一个普通站点 `https://phpmyadmin.meow`），配好 root 免密登录 ——
本地开发省得每次输密码。不想要了就跟删站点一样删掉。

## Mailpit：本地假邮箱

写代码时最烦的两件事：测「注册验证码」和「订单通知」邮件，结果真发出去了。
Mailpit 把站点发的邮件**全部截下来**，一封不往外发，网页里想看哪封看哪封。

```bash
python meowbox.py start mailpit     # 起来（.exe 在 laragon 的 bin/mailpit 里）
python meowbox.py mail              # 看状态：几封信、几个未读、SMTP 端口
python meowbox.py mail open         # 打开收件箱（没起来会自动先拉起）
python meowbox.py mail clear        # 清空信箱（SQLite 库直接删，要先停服务）
python meowbox.py stop mailpit
```

| 用途 | 地址 |
|---|---|
| 网页收件箱（UI + JSON API） | <http://127.0.0.1:8025> |
| SMTP（让站点往这儿发） | `127.0.0.1:1025`（可改，见下面第 145 行） |
| 数据文件 | `data/mailpit/mailpit.db` |

> ⚠️ 本机现在用的是 **2525**：Windows 有个系统连接（`svchost` 往外连 Azure）
> 把 1025 当成了本地端口，bind 会直接 `WSAEACCES`。改法就是
> `meowbox.config.json` 的 `mailpit.smtp_port` 改成 2525，再把 `php.ini` 的
> `sendmail_path` 补上 `-S 127.0.0.1:2525`（PHP 的 `mail()` 才跟得上）。

站点里怎么配（按你用的框架挑一行）：

```php
// PHP 原生
ini_set('SMTP', '127.0.0.1'); ini_set('smtp_port', 1025);

// Laravel（.env）
MAIL_MAILER=smtp
MAIL_HOST=127.0.0.1
MAIL_PORT=1025
MAIL_USERNAME=null
MAIL_PASSWORD=null
MAIL_ENCRYPTION=null
```

- 端口都写在 `meowbox.config.json` 的 `mailpit` 段里（`smtp_port` / `http_port` /
  `max_messages`），被别的程序占了会直接告诉你占用者是谁。
- `max_messages` 默认 500 封，超了自动丢最旧的 —— 本地调试够用，也不会撑爆磁盘。
- 想关掉它：把 `mailpit.enabled` 改成 `false`，喵盒就不再管它（`status` 里也不会出现）。
- 图形界面里点「邮件收件箱」，托盘菜单里也有同一项，都是「没起来就先拉起来再打开」。

## 从 Laragon 迁移（一条命令）

```bash
python meowbox.py migrate                        # 先看要搬什么（一个字节都不动）
python meowbox.py migrate --yes --tld auto       # 真搬，域名后缀跟 Laragon 保持一致
python meowbox.py migrate --yes --sites-only     # 只搬站点
python meowbox.py migrate --yes --db-only --db verinawiki
```

它做两件事：

1. **站点**：把 `D:\laragon\www\<站点>` 整个复制到 `www\` 下。
   目标已存在默认跳过；加 `--overwrite` 才会覆盖（而且**先把旧的挪进 `_backup\`**）。
2. **数据库**：**不直接拷 `data/mysql-8.4`** —— 那里面是 InnoDB 的脏页、binlog、
   权限表和系统库，直接搬十次有九次起不来。正确姿势是：

   ```
   ① 把 Laragon 的 data 目录整份复制到 run/migrate/laragon-datadir（原件一个字节不动）
   ② 用 Laragon 的 mysqld 在这个副本上临时起一个实例（默认端口 3307）
   ③ mysqldump 每个库 → run/migrate/<库名>.sql
   ④ 导进喵盒自己的 MySQL（3306）
   ⑤ 关掉临时实例，删掉副本
   ```

   中途任何一步失败，Laragon 的原始数据都还是原样 —— 这就是为什么宁肯多花几十秒复制。

**域名后缀这件事**：Laragon 常用 `.loc`，喵盒默认 `.meow`。站点里往往写死了网址
（比如 MediaWiki 的 `$wgServer = "http://verinawiki.loc"`），所以迁移时建议：

```bash
python meowbox.py migrate --yes --tld auto   # 后缀变成 .loc，旧网址继续有效
```

想改用 `.meow` 也行，但要在站点配置里把旧域名改过来（不改的话页面里的链接会跳回 `.loc`）。

迁完记得**同步 hosts**（要管理员）：GUI 里点「同步 hosts」会弹 UAC，
命令行用管理员终端跑 `python meowbox.py hosts --apply`。
之后 `python meowbox.py ssl` 会给新后缀重新签证书（旧的那种是 `.meow` 的，对不上）。

## 彻底自带（不再依赖 Laragon）

```bash
python meowbox.py vendor            # 先看要复制什么、占多少
python meowbox.py vendor --yes      # 真搬：复制进 bin/ → 改 php.ini → provider 设成 null
python meowbox.py vendor --yes --download   # provider 里没有的组件就按 packages.conf 下载
```

搬完之后长这样：

```
D:\meowbox\bin\apache\httpd-2.4.68-...\bin\httpd.exe
D:\meowbox\bin\php\php-8.3.33-...\php.exe
D:\meowbox\bin\mysql\mysql-8.4.3-...\bin\mysqld.exe
D:\meowbox\bin\nginx\nginx-1.30.4\nginx.exe
D:\meowbox\bin\mailpit\1.22.3\mailpit.exe
D:\meowbox\bin\openssl\openssl.exe
```

`meowbox.config.json` 里 `provider` 变成 `null`，之后喵盒只认自己的 `bin/`。

**几个必须做的细节**（`vendor` 自动处理）：

1. **改 `php.ini`**：Laragon 的 php.ini 里写死了六处 Laragon 路径 ——
   `extension_dir`、`error_log`、`session.save_path`、`include_path`、
   `sendmail_path`、`curl.cainfo`。不改的话 Laragon 一删 PHP 立刻废。
   改完 `sendmail_path` 指向自己的 Mailpit，PHP 的 `mail()` 直接进本地信箱。
2. **复制一份能用的 openssl**（签 HTTPS 要它；Apache 自带的那个是坏的）。
3. **两个 Web 服务器的配置都要重渲染** —— 不然另一份里还留着旧路径。
4. 复制完会**重启服务**并用新程序验证（`httpd -v` / `php -v` / `mysqld --version` /
   `nginx -v` / `mailpit version` / `openssl version`），全部 ✅ 才算成功。

验证过的结果（2026-10）：

```
✅ Apache   Apache/2.4.68 (Win64)
✅ PHP      PHP 8.3.33 (cli)
✅ MySQL    8.4.3 for Win64
✅ Nginx    nginx/1.30.4
✅ Mailpit  v1.22.3
✅ openssl  OpenSSL 3.6.0

provider = null
扫了 16 个配置文件：没有任何指向喵盒外面的路径
站点 verinawiki.loc / linuxxh.loc 仍 200，wiki 页数、论坛版块数都没变
```

**后悔药**：把 `meowbox.config.json` 的 `provider` 改回 `"D:/laragon"` 就退回借用模式
（`bin/` 里的副本留着也不碍事）。

> **删 Laragon 之前注意**：如果某个程序的工作目录正好是 `D:\laragon`，Windows 会锁住
> 那个文件夹（改名/删除都会「拒绝访问」）。关掉那个程序（或者把它换个目录启动）再删。

## 便携：整个文件夹搬走就能跑

喵盒**不是**装出来的程序，而是"一个文件夹 + 一个 Python"。所以：

- **不装 Windows 服务**（`start`/`stop` 就是普通进程，关机就没了，干净）；
- **不写注册表**（唯一例外：自签 HTTPS 的根证书装进**当前用户**证书库，
  `python meowbox.py ssl untrust` 就能撤掉）；
- **配置里不写死路径**：`meowbox.config.json` 里 `root` 是 `"."`，
  真正的根目录 = **配置文件所在的文件夹**，网站目录是相对的 `www`；
- **不会污染 provider**：日志、临时文件、pid、数据库全在自己目录里。

想搬到别的盘（或者拷给另一台机器）：

```bash
python meowbox.py portable          # 先检查：有没有写死的路径
python meowbox.py portable --fix    # 有的话一键改写成相对路径
python meowbox.py shortcut          # 重新生成双击启动器（.vbs 里存的是绝对路径）
python meowbox.py autostart on      # 如果要用开机自启，搬完重开一次
```

然后就整个文件夹复制走 —— 在新位置直接 `python meowbox.py start` 就行。

搬家的几个须知：

| 东西 | 会不会跟着走 | 说明 |
|---|---|---|
| 站点、配置、日志、MySQL 数据 | ✅ 会 | 全在文件夹里 |
| `provider`（默认 `D:/laragon`） | ⚠️ 看情况 | 它指向**外部**的 Laragon；换机器要么改这一行，要么 `pkg install` 装到自己 `bin/` |
| 双击启动器 `.vbs` | ✅ 会 | 路径运行时现取，不用改（除非解释器变了，重跑 `shortcut`） |
| 开机自启 | ❌ 不会 | 「启动」文件夹里的 `.vbs` 记的是旧路径，搬家后重跑 `autostart on` |

> 想彻底自带？把 provider 换成 `null`，然后 `python meowbox.py pkg install`
> 把 Apache/Nginx/PHP/MySQL/Mailpit 全下到自己的 `bin/` 里 ——
> 这时候整个文件夹可以塞进 U 盘带走。

## 打包成 exe（不用装 Python）

不想让喵盒依赖机器上的 Python，就把它打成 exe。一次打包，三个入口：

```bash
uv run python build_exe.py            # 打包，产物在 dist/MeowBox/
uv run python build_exe.py --install  # 打完自动放进喵盒根目录
uv run python build_exe.py --verify   # 打完跑一遍 info/status 自检
```

> 用的是 `uv.lock` 里锁死的那套环境（PyInstaller 6.22.3 / Python 3.14）。
> **别用别的解释器打包** —— 精简版 Python 缺 `ctypes.wintypes.HCURSOR`，
> 打出来的托盘 exe 会一启动就 `AttributeError`；PyInstaller 大版本变了，
> 打出来的包也会跟上次不一样。这两类漂移都很难查，锁住最省心。

| exe | 干什么 | 子系统 |
|---|---|---|
| `meowbox.exe` | 命令行，参数跟 `python meowbox.py` 完全一样 | 控制台 |
| `meowbox-tray.exe` | 系统托盘（右键菜单） | 窗口（双击不弹黑框） |
| `meowbox-panel.exe` | 图形控制面板 | 窗口（双击不弹黑框） |

三个 exe 共用一份 `_internal/`（PyInstaller 的 onedir 模式，约 30MB）——
启动快，也不需要每次解压到临时目录。用 `--install` 之后根目录长这样：

```
D:/meowbox/
├── meowbox.exe  meowbox-tray.exe  meowbox-panel.exe
├── _internal/          ← 依赖 + 内置的 templates/ 和 assets/
├── meowbox.config.json  www/  bin/  etc/  logs/  data/  run/
└── meowbox.py           ← 源码还留着，开发时照常用
```

**根目录怎么认**：源码模式 = `meowbox.py` 所在的文件夹；exe 模式 = **exe 所在
的文件夹**（`sys.executable`）。配置里存的永远是相对路径，所以 exe 放在哪个盘、
整个文件夹搬到哪台机器都能跑（`meowbox.exe portable` 会逐条核对）。

打包之后的行为差异只有三处，全在 `meow/runtime.py` 里：

- `templates/`、`assets/` 打进了 `_internal/`；**根目录里有就用根目录那份**
  （所以你随时可以改模板、换图标），没有才退回包里那份；
- 托盘/面板互相拉起时，直接启动对应的 exe，不再拼 `pythonw meowbox.py`；
- 窗口版没有控制台，`print()` 自动改写到 `logs/meowbox-tray.log` /
  `logs/meowbox-panel.log`；输出被重定向时按 UTF-8 走（否则 `🐾` 会崩）。

> 打包用的是**系统装的** `C:\Python314`，不是 Hermes 自带的解释器 ——
> 那个 3.11 在 `%LOCALAPPDATA%\hermes\tools\` 下，升级就失效，而且
> `ctypes.wintypes` 缺 `HCURSOR`，托盘会当场炸。

## 图形界面：不用敲命令也能全管

`pythonw meowbox.py gui`（或者双击 `meowbox-panel.vbs`）。菜单栏分成
**文件 / 服务 / 站点 / PHP / 工具 / 帮助**，命令行有的功能这里基本都有：

| 想做的事 | 菜单位置 |
|---|---|
| 启动 / 停止 / 重启全部 | 服务菜单，或窗口上的三个按钮 |
| 切换 Apache ↔ Nginx | 服务菜单 / 窗口上的「换成 Nginx」按钮 |
| 初始化 MySQL 数据目录 | 服务 → 初始化 MySQL 数据目录… |
| 释放被占端口 | 服务菜单 / 按钮 |
| 新建 / 删除 / 打开站点 | 站点菜单 / 站点列表下面的三个按钮（双击列表也能打开） |
| 同步 hosts | 站点菜单 / 按钮（顺带写代理绕过列表） |
| 打开 / 编辑 hosts 文件 | 站点 → 打开 hosts 文件（编辑）…（自动用管理员记事本打开） |
| 改域名后缀 | 站点 → 修改域名后缀…（也可以在窗口里直接改） |
| 重新生成配置（含 `-t` 自检） | 文件菜单 / 按钮 |
| HTTPS：启用 / 重签 / 看状态 / 信任 / 取消信任 / 关闭 | 工具 → HTTPS 证书 ▸ |
| 打开邮箱、看有几封信、清空信箱 | 工具菜单 / 窗口右下「邮件收件箱」 |
| 安装软件包（packages.conf） | 工具 → 安装软件包… |
| PHP：换默认版本 / 装新版本 / 给选中站点换版本 / 开 php.ini / 开 phpMyAdmin | PHP 菜单（版本前面 ● 是默认） |
| 从 Laragon 迁移站点 / 数据库 | 工具 → 从 Laragon 迁移站点和数据库… |
| 生成双击启动器、开机自启、便携检查 | 文件菜单 |
| 拉起系统托盘 | 工具 → 启动系统托盘（**开面板时会自动带起来**，见下） |
| 看日志在哪 | 工具 → 打开日志文件夹 |

第一次跑 `gui` 而**还没有配置**时，它会自己按默认值初始化一遍，
不会让你回命令行去 `init`。托盘（`meowbox-tray.vbs`）那边也有常用项：
启动/停止/重启、切换 Web 服务器、打开站点、邮件收件箱、同步 hosts、退出。

**面板和托盘是一对**：面板一启动就会顺手把托盘拉起来（托盘的图标在
Win11 的溢出区里），这样关掉面板之后还能从托盘里启停服务、开站点。
不想要就取消「文件 → 开面板时顺便带起托盘」那个勾（存在配置的 `ui.tray_with_panel`）。
托盘自己是单实例的，重复拉起会自动退出，所以不会出现两个图标。

## 日志：自动记账 + 定期收拾

`logs/` 里东西会越攒越多（Apache 的 access/error、每个站点的 `site-*.log`、
MySQL/Mailpit 的 console、`https-check.log`…）。喵盒分两类对待：

| 类别 | 例子 | 谁来收拾 |
|---|---|---|
| 喵盒自己写的 | `https-check.log`、`vendor-run.log` | **按大小滚动**（体检记录 512KB × 3 份），永远长不大 |
| 服务器自己写的 | `apache-*.log`、`site-*.log`、`mysql-console.log` | 由你（或定时）清：**7 天没动就删，单文件超 2MB 就截断** |

```bash
python meowbox.py logs                      # 谁最占地方、要不要清
python meowbox.py logs clean --dry-run      # 先看会清哪些（不动手）
python meowbox.py logs clean                # 真清
python meowbox.py logs clean --days 3 --max-mb 5    # 换阈值
```

面板里是 **工具 → 日志占用…** 和 **工具 → 清理日志…**（后者先弹预览，你点
「是」才动手）。

**两条讲究**：

1. **服务正在写的日志不截断** —— Windows 上把别的进程开着的文件清零，那进程
   还按旧偏移写，文件前段会冒出一大片 NUL。所以 `apache-*.log` 这类在
   Apache 跑着的时候会被"跳过"，并告诉你「先停服务」。
2. **`https-check.log` 不参与清理** —— 它自己滚动，留着当"什么时候全绿过"的
   证据链。每次体检都会记一条：

```
[2026-10-19 01:21:34] 全绿 ✅  —  apache:443
    ✅ HTTPS 开关：已打开（apache，端口 443）
    ✅ linuxxh.meow 真握手：成功（TLSv1.3 / TLS_AES_256_GCM_SHA384），服务器给的是 linuxxh.meow 的证书 ✅
```

哪天锁突然变红，往上翻就能看到"上次全绿是什么时候、那之后哪一层变了"。

### 自动的那一份

- **每周日 09:00**：Hermes 的定时任务（`喵盒日志清理（每周日）`，`no_agent` 模式，
  跑 `scripts/meowbox_logs_clean.py` → `meowbox.exe logs clean`）自动清一次。
  **有东西被清 / 有大文件清不掉时才发消息**，没事就一声不吭。
- **面板启动时**：日志总量超过 **20MB** 会在日志栏提一句（只提醒，不自动删）。

> 为什么不用 Windows 计划任务？喵盒的底线是「不装服务、不写注册表」，
> 计划任务两样都沾。Hermes 的 cron 是它自己的调度器，删掉就等于没发生过。

# 设计上的几个小心思（也是踩过的坑）

> 这一篇从 [README](../README.md) 里搬出来的长文。它没被删，只是不该挤在首页。

## 设计上的几个小心思

- **模板用 `@名字@` 而不是 `{}`**：Apache/Nginx 配置里全是 `{}` 和 `%{Referer}i`，
  用 `str.format` 会被当占位符炸掉。
- **hosts 成对标记**：删站点/清空时只动喵盒那一块，你手写的条目一个字不动。
- **先自检再启动**：`start` 之前会跑 `httpd -t`，配置有错直接中止，
  而不是让服务半死不活。
- **不影响 Laragon**：喵盒的配置、日志、数据全在自己目录里。
  但 **两个不能同时开**（会抢 80 / 3306 端口）——喵盒启动前会检测并提示。
- **站点域名随你定**：`www/` 里一个文件夹 = 一个站点，域名 = `<文件夹名>.<后缀>`。
  后缀默认 `.meow`，随时可改：`python meowbox.py tld meow` / `tld test` / `tld local`。
  改完会**自动重渲染配置**并清掉旧后缀的片段，hosts 整块重写。
- **小心别的软件抢 80 端口**：实测见过 `Steam++.Accelerator.exe`（Steam 加速器）
  占着 80，导致 Apache 起不来、浏览器还返回莫名其妙的 500。
  喵盒启动时会直接告诉你「端口 80 已被占用：xxx.exe (pid 1234)」。
- **「被外部占用」这个状态**：喵盒判断服务在不在跑，靠的是自己写的 pid 文件。
  如果有人**用管理员权限**启动过喵盒的 Apache/MySQL（比如在管理员终端里跑
  `python meowbox.py start`），那个进程就不在喵盒的 pid 文件里 ——
  界面会显示「被外部占用」（橙色 ◐），站点其实还活着、端口也还占着。
  一条命令收回来：`python meowbox.py freeport 80`（会弹 UAC）。
- **数据库进程绝不硬杀**：`freeport` 遇到 `mysqld` 会走 `mysqladmin shutdown`
  优雅关闭；硬砍 `taskkill /F` 是会把 InnoDB 弄坏的。
- **Nginx 的粗活不许落进 provider**：Windows 版 Nginx 默认把 `temp/`、`logs/`
  写在自己安装目录里。喵盒用 `-p` 把 prefix 指到喵盒根目录，并且把
  `client_body_temp_path` 这五个 temp 路径全部显式指到 `run/nginx-temp/`。
- **Nginx 的 pid 是 master 的，占端口的是 worker**：`netstat` 看到的
  `nginx.exe` pid 和 pid 文件里那个不一样，别拿 pid 去对端口 —— 只要
  「喵盒记录的服务是跑着的」，它该占的端口就算它的（跟 mysqld 一个道理）。
- **php-cgi 是第三个进程**：Nginx 不解释 PHP，得另起 `php-cgi.exe -b 127.0.0.1:9000`。
  `start nginx` 起两个、`stop` 停两个，日志在 `logs/php-cgi-console.log`。
- **Nginx 停止要「先礼后兵」**：`-s quit` 是优雅退出，但还有 keepalive 连接
  挂着时会等几秒；所以等 3 秒不走就 `-s stop`，最后才 `taskkill`。
  反过来说，改配置不用停服务 —— `nginx -s reload` 就行，连接都不掉。
- **Mailpit 的数据就一个文件**：`data/mailpit/mailpit.db`（SQLite）。备份/搬走
  拖这一个文件就够；`mail clear` 就是把它（连同 `-wal` / `-shm`）删掉，
  所以**必须先停服务**，不然删不干净。
- **Mailpit 只绑 127.0.0.1**：`--smtp 127.0.0.1:1025 --listen 127.0.0.1:8025`。
  默认它绑的是 `[::]`（所有网卡），那样 Windows 防火墙会弹窗、隔壁机器也能
  往你信箱里塞信 —— 本地开发没必要。
- **根目录不认配置里写的值**：`load()` 一律用"配置文件所在目录"当根目录，
  所以 `root` 写 `"."` 是安全的，文件夹搬走立刻生效，不用改任何东西。
- **`save()` 会自动相对化**：存盘时把根目录内的路径改写成相对路径
  （`D:/meowbox/www` → `www`），否则搬一次家就炸一次。
- **GUI 的菜单栏而不是一堆按钮**：功能越加越多，按钮会挤出窗口；
  常用动作留按钮，其余进 文件/服务/站点/工具 菜单。
  加新功能时记得两条路都接上（`meowbox.py` 的命令 + `gui.py` 的菜单项）。

- **GUI 里别用 emoji**：Tk 在 Windows 上把 emoji 渲染成**单色轮廓**，
  🟢 和 ⚪ 长得一模一样。状态点要用 `tk.Label` + `fg=` 上色，
  标题的小图标用 `tk.PhotoImage` 加载我们自己生成的 PNG。
- **启动器用系统 Python**：`shortcut` / `autostart` 优先挑
  `C:\Python314\pythonw.exe` 这类系统安装的解释器，
  不用 Hermes 自带的那个（`%LOCALAPPDATA%\hermes\tools\` 下面，会随升级失效）。
- **`.vbs` 必须存成 UTF-16LE**：WSH 会把 UTF-8 的 BOM 当成非法字符，
  双击直接报「无效字符」。启动器里的路径是运行时从 `WScript.ScriptFullName`
  取的，所以整个 `meowbox` 文件夹搬到别的盘也不会坏。
  （自己改坏了就跑 `python meowbox.py shortcut` 重新生成。）

### 打包成 exe：一个「路标」，三处差异

打包之后程序**不知道自己在哪**：`Path(__file__).parent.parent` 会指向包内部
（`_internal/`），拿它当根目录，配置、`www/`、`bin/` 全找错地方。所以这一轮把
**所有"两种运行方式不一样"的东西收进一个门面 `meow/runtime.py`**，别处一律不许
再自己算路径：

| 函数 | 管什么 |
|---|---|
| `app_root()` | 根目录：exe 模式 = **exe 所在的文件夹**；源码模式 = `meowbox.py` 所在文件夹 |
| `templates_dir()` / `assets_dir()` | 只读资源：**根目录里有就用根目录那份**（你能改模板、换图标），没有才退回包里那份 |
| `launch_argv()` / `console_argv()` | 再起一个自己：**只有 `gui` / `tray` 走窗口版 exe**，其它子命令一律 `meowbox.exe`（控制台版，带输出、能被 UAC 提权）；源码模式才拼 `pythonw meowbox.py` |
| `localize()` | 提示文案：exe 模式下 `python meowbox.py xxx` 自动换成 `meowbox.exe xxx` |
| `prepare_stdio()` | 窗口版没有控制台 → `print()` 会炸；被重定向时按 UTF-8 走（否则 `🐾` 直接 `UnicodeEncodeError`） |

因为只认这一个门面，`meowbox.exe ssl check` 和 `python meowbox.py ssl check`
行为、输出、退出码完全一致 —— 这也是为什么每加一个功能，两条路都自动能用。

其他几条打包上的讲究：

- **三个 exe 共用一个 `_internal/`**（onedir，不是 onefile）：启动快，也不用每次
  解压几十 MB（onefile 那样干容易被杀软盯上）。代价是 PyInstaller 的 `COLLECT`
  会因为三份分析结果撞重复目标路径报错 —— spec 里自己按 key 去重再塞进去。
- **懒加载模块要主动收**：`from meow import gui` 写在函数体里，静态分析看不见，
  漏了就是「exe 能开、一点菜单就 ImportError」→ `collect_submodules("meow")`。
- **装包要"原子换"**：早期是删旧 `_internal` 再拷新的，一旦被运行中的 exe 锁住
  （Windows `WinError 5` 删不掉 `VCRUNTIME140.dll`），就留下一个**半截的包**，
  根目录的 exe 当场变砖（真踩过）。现在先检查有没有喵盒 exe 在跑，再
  `复制成 _internal.new → 老包改名 .old → 新的换上去 → 尽力删旧的`。
- **老包改名要带时间戳**：固定叫 `_internal.old` 时，只要上一次的旧包因为被锁
  没删掉（杀软 / 还在跑的 DLL，`rmtree(ignore_errors=True)` 会悄悄失败），
  下一次 `rename` 就撞名 `WinError 183`，装到一半中断 —— 偏偏这时候**根目录的
  exe 已经是新的、`_internal` 还是旧的**，两边对不上。现在叫
  `_internal.old-20261031-174635`，并且每次 install 开头先扫一遍残留。

### 提权写 hosts：ShellExecute 说成功，不等于写进去了

打包版点「同步 hosts」的链路是这样的：面板没有管理员权限 → `ShellExecuteW("runas")`
弹 UAC → 一个**提权的进程**去写文件。这里两个坑都实测踩过：

1. **提权起的是哪个 exe**：`launch_argv()` 早期只给 `gui` / `tray` 指定了窗口版
   exe，其它子命令退化成「当前 exe + 子命令」，冻结版里于是拿到
   `meowbox-panel.exe` —— 而面板入口把自己的子命令补在最前面，
   `meowbox-panel.exe hosts --apply` 实际变成 `gui hosts --apply`，
   argparse 报 `unrecognized arguments: hosts --apply` 当场退出。
   用户看到的就是：**UAC 弹了、点了「是」、窗口一闪、hosts 一个字没改**。
   现在 `launch_argv()` 里只有 gui / tray 走窗口版，其余一律控制台版
   `meowbox.exe`（它就是要输出、要提权的那类活儿）。
2. **ShellExecute 的返回值什么都证明不了**：它只能说「UAC 弹窗发出去了」。
   所以写完之后一律用 `hosts.wait_until()` 回头再看一眼文件 —— 条目真的变成
   目标域名才报「已写好」；还是老样子就明说「提权那步没写成功（UAC 点了否？）」，
   并把当前条目 / 目标条目一起列出来。面板和托盘都走这一条。
   换过后缀（`.meow → .sew`）时这一条尤其重要：`hosts` 是浏览器认不认识新域名的
   唯一依据，静默失败会让人以为「网站坏了」。

### HTTPS 体检：按故障顺序查，最后一层必须"真握手"

看配置能查出 90% 的问题，但**有一种病看配置文件永远看不出来**：证书、vhost
文件、端口全都在，443 上却落脚在普通 HTTP 站点 —— 客户端发 ClientHello，
服务器拿明文 HTTP 回它，OpenSSL 报 `packet length too long`、curl 报
`SEC_E_INVALID_TOKEN`。这就是换过 TLD 之后"证书还停在旧后缀，于是 vhost 没生成"
的经典现场。

所以体检的**第六层是真握手**：用 Python 的 `ssl` 带 SNI 真连一次，把服务器递
过来的证书落盘解析，跟期望域名比对。它能同时表达三种结论：

1. 握手成功 + 证书就是本站的 → ✅ 小绿锁链路通；
2. 握手成功但证书是**别的站**的 → SNI 没命中，说明这个站的 SSL vhost 不在；
3. 拿回来的**是明文** → 443 上压根没有 SSL 站点。

顺序也是修复顺序 —— **开关 → 根证书/信任库 → 站点证书（链/SAN/到期）→ SSL
vhost → 端口 → 真握手**。按这个顺序修，修哪层都不会白做；反过来，先换 DLL、
先改防火墙，就是浪费时间（这轮真的差点冤枉了 OpenSSL 的 DLL）。

配套两件：

- **体检一次、记账一次**：结果写进 `logs/https-check.log`（带时间戳，自己滚动），
  锁哪天变红，往上翻就知道"上次全绿是什么时候、那之后哪一层变了"；
- **能修的就给按钮**：`fixable` 为真时，报告窗口底部出现「一键修复」
  （重签全部证书 → 重生成配置 → 重启），修完点「重新体检」验收。

### 日志：分两类，一刀切会出事

`logs/` 里的东西来源完全不同，处理方式也必须不同：

| 谁写的 | 怎么管 | 为什么 |
|---|---|---|
| 喵盒自己（体检记录、vendor 日志） | **按大小滚动**（512KB，留 `.1` `.2`） | 有诊断价值、但绝不许无限长 |
| 服务器自己（`apache-*.log`、`site-*.log`、`*-console.log`） | 只在"太大(2MB)/太旧(7天)"时删或截断 | 是别人的日志，喵盒只是代为收拾 |

两条硬规矩：

- **服务正在写的日志不截断**：Windows 上把别人开着的文件清零，对方还按旧偏移
  往下写，文件前段会冒出一大片 NUL。所以 `clean()` 接收"在跑的服务前缀"
  （`apache` / `nginx` / `site-` / `mysql` / `mailpit`），命中的归到"跳过"，
  并明确告诉你「先停服务」—— 宁可不清，也不弄脏日志。
- **体检记录不参与清理**：它自己会滚动，留着当证据链。
- **自动化的那份放在 Hermes cron**（`no_agent`，不叫模型、不花 token），
  有东西清才播报，没事就静默；面板启动只做 20MB 的提醒，**从不擅自动手删**。
  不用 Windows 计划任务，是因为它既装服务又写注册表，和喵盒的底线冲突。

### PHP 多版本：一个 Apache 怎么同时跑两个 PHP

mod_php 是**进程级**的：一个 httpd 只能挂一个 PHP。所以「站点级版本」只能靠
FastCGI —— 默认版本继续走 mod_php（快、省事），其他版本另起一个
`php-cgi.exe -b 127.0.0.1:900x`，站点 vhost 里把 `.php` 转过去。

两个 Windows 专属的坑，都实测踩过：

1. **`SetHandler "proxy:fcgi://127.0.0.1:9001"` 结尾那个 `/` 不能省。**
   少了它，Apache 会把请求路径直接拼上去 —— Windows 路径以盘符开头，拼出来
   成了 `fcgi://127.0.0.1:9001D:/meowbox/...`，于是它去解析「主机 9001d」，
   报 `DNS lookup failure for: 127.0.0.1:9001d`。
2. **光有 SetHandler 还不够。** mod_proxy_fcgi 送出去的 `SCRIPT_FILENAME`
   是 `/D:/...`（带前导斜杠），Windows 上不算合法绝对路径，php-cgi 只回一句
   `No input file specified`。必须再补一条
   `ProxyFCGISetEnvIf "true" SCRIPT_FILENAME "%{reqenv:DOCUMENT_ROOT}%{reqenv:SCRIPT_NAME}"`
   把路径自己拼对。
   （社区里的 `#` 结尾、`ProxyPassMatch` 等偏方在这个版本上都不稳，别用。）

顺带一个好处：这种写法下 php-cgi 的进程池是**按需**起的 —— 没有站点用 8.5，
9001 上就不会有进程。

### pid 文件里的号，可能已经是别人了

Windows 会复用进程号。Mailpit 崩掉以后，`run/mailpit.pid` 里的 2292 可能被
系统发给 `WUDFHost.exe` —— 如果只看「这个 pid 存在吗」，就会以为 Mailpit 还
活着，然后去 taskkill 一个系统进程，失败了还报「多半是管理员权限，普通权限
杀不掉」，把人吓得以为权限出了毛病。

所以 `util.pid_is(pid, names)` 现在同时核对**进程名**：pid 在、名字也对，才
算还活着。Apache / Nginx / MySQL / Mailpit / php-cgi 全都换成了它。

### 面板为什么以前会「未响应」：查询不许起子进程，也不许占主线程

现象是面板开着，隔一会儿窗口就白一下、点不动；有的机器干脆被 Windows 标成
「未响应」。原因跟业务代码没关系，全在**查询方式**上：

- 面板每 2.5 秒刷一次状态，走的是 `services.status()` + `blocked_ports()` +
  每个端口一次 `port_holder()`；
- 这三样最后都落到 `tasklist` / `netstat` 上，而 `tasklist` 在装了 380 个进程
  的机器上**一次要 0.9 秒**（`netstat` 只要 0.09 秒，锅都在 `tasklist`）；
- 一轮下来 **1.08 + 1.05 + 0.91 ≈ 3 秒**，而且全跑在 Tk 的主线程里 ——
  也就是窗口有 3 秒完全不处理消息，接着 2.5 秒后又来一轮。

改了两处，各管一半：

1. **查询换 API**（`meow/util.py`）：进程名走 `OpenProcess` +
   `QueryFullProcessImageNameW`，监听端口走 `GetExtendedTcpTable`
   （`TCP_TABLE_OWNER_PID_LISTENER`）—— 都是微秒级，`tasklist` / `netstat`
   只留作回退（非 Windows、或 API 调用失败时）。实测 `status()` 从 1.083 秒
   降到 0.0012 秒，`listeners()` 从 0.95 秒降到 0.002 秒。
   `image_name()` 特意分三种返回值：名字 / `""`（这个 pid 上没人）/
   `None`（查不了）。分不清「没人」和「查不了」，进程一退就会每轮回退到那个
   0.9 秒的 `tasklist`，等于白改。
2. **采集丢后台**（`meow/gui.py`）：`_poll_status()` 起线程采集，结果塞回
   `queue`，`_drain()` 在主线程里画。就算哪天某个 API 又慢下来，卡住的也是
   后台线程，窗口照样能拖能点。

顺带两个小东西：日志框只留最近 600 行（`Text` 控件攒到几万行，滚动都发涩）；
HTTPS 那行状态里的 `certutil` 结果缓存 20 秒（它一次 0.2 秒，每次操作都重算
没必要），信任库一变更就作废。

**托盘跟面板一起起来**：以前只有「工具 → 启动系统托盘」一个手动项，于是
关掉面板就一个能点的地方都没有。现在面板启动 400 毫秒后自己拉一次托盘，
判断依据是 `tray.is_running()` —— 它用 `OpenMutexW` 查那个单实例互斥量，
**只查不占**（`CreateMutexW` 顺手就把名字创建了，检查完面板会以为托盘一直在跑）。
不想要这个行为，就取消「文件 → 开面板时顺便带起托盘」。

### 端口不是"想用就能用"

这轮遇到两个都不是喵盒的锅、但表现一模一样（服务起不来、站点 500）的情况：

- **Windows/Hyper-V 的保留端口段会吃掉 3306**：`netsh int ipv4 show
  excludedportrange protocol=tcp` 一查，`3230-3329` 正好把 3306 圈在里面，
  mysqld 报「Bind on TCP/IP port: 以一种访问权限不允许的方式…」然后退出。
  `net stop winnat && net start winnat`（管理员）能重新分配，端口就回来了。
- **一条出站连接也能占住端口**：`svchost` 往外连 Azure 时把 **1025** 当成本地
  端口，Mailpit 去 bind 就 `WSAEACCES`。这种只能换端口（本机改成 2525），
  并且记得改 `php.ini` 的 `sendmail_path`（补 `-S 127.0.0.1:2525`），
  否则 PHP 的 `mail()` 还往 1025 发。

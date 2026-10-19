# 更新日志

喵盒的所有重要变化都记在这里。

格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [未发布]

### 计划中

- 站点级 rewrite 配置（现在要在 `auto.*.conf` 里手加，重新渲染就没了）

## [0.2.0] - 2026-10-19

「PHP 版本自由」版。原来整个喵盒只有一个 PHP，现在一个站点一个版本。

### 新增

#### PHP 多版本（`meow/php.py`）

- `bin/php/` 下面放几个版本就是几个版本，启动时自动扫描（带缓存，面板刷新不再狂起子进程）
- **站点级切换**：`sites.php` 里 `{"blog": "8.5"}`，没写就跟着默认版本
- Apache：默认版本继续走 mod_php，其他版本各自一个 `php-cgi.exe` + `SetHandler proxy:fcgi://…`
- Nginx：本来就走 php-cgi，现在**一个版本一个端口**（默认版本拿 9000，其余 9001、9002…）
- php-cgi 进程池按需启动：没有站点用 8.5，就不会多一个进程
- `php list / install <版本> / use <版本> / site <站点> [版本] / ini / open / myadmin`
- `php install` 从 **windows.php.net** 拿官方 x64 **线程安全（TS）** 包（mod_php 只能挂 TS），
  自动挑该系列最新补丁，解压进 `bin/php/`，并生成 php.ini：
  `extension_dir`、session 目录、`sendmail_path`（接到 Mailpit）、20 个常用扩展
- 面板新增 **PHP 菜单**：默认版本打 ●，点一下就换；还能装新版本、给选中站点换版本、
  打开 php.ini / PHP 目录、打开 phpMyAdmin

#### phpMyAdmin

- `php myadmin` 第一次会从官方源下载最新版装到 `www/phpmyadmin`，
  于是它就是一个普通站点（`https://phpmyadmin.meow`），配好 root 免密、只监听本机

#### 站点默认打开 https

- 站点列表、双击打开、托盘菜单统一走 `vhost.site_url()`：**签过证书就用 https**，
  没签过（或 SSL 关着）才退回 http —— 三处各写一套的时代结束了

#### 下载不再依赖系统工具（`meow/download.py`）

- `pkg install` 和 `php install` 改用标准库 `urllib` + `zipfile` / `tarfile`：
  exe 发出去以后 PATH 里没有 `unzip`，以前会当场哑掉；SSL 出错时自动退回系统 `curl`

### 修复

- **面板里站点一直显示 `http://`**：即使证书早就签好了，双击还是打开 http。现在按证书实际情况显示
- **pid 文件里的号可能已经被系统发给别人**：Mailpit 崩了以后 `run/mailpit.pid` 里的
  2292 变成 `WUDFHost.exe`，喵盒以为它还活着，去 taskkill 一个系统进程，失败后还报
  「多半是管理员权限」。新增 `util.pid_is(pid, names)`：**pid 在、进程名也对**才算活着
  （Apache / Nginx / MySQL / Mailpit / php-cgi 全部换过来）
- `packages.conf` 里 PHP 那行原来写的是 **NTS** 包 —— mod_php 根本挂不上，改成 TS
- 删站点时顺手把 `sites.php` 里那个站点的版本记录一起清掉（不然会留垃圾键）
- `discover()` 探测到的 PHP 路径现在跟「默认版本」一致（以前按目录名倒序瞎挑一个）
- 顺带把 `vhost.py` 里三个 `FALLBACK_*` 模板的占位符从 `{}` 改成 `@名字@`
  （以前模板文件丢了会渲染出一堆字面量 `{port}`）
- **`restart` 会把 Mailpit 顺手关掉再不拉起来**：以前重启后按 `autostart`
  列表启动，而 Mailpit 不在里面 → 重启一次信箱就没了。现在只重启「重启前
  在跑的」那些服务（命令行和面板的「重启」都改了）

## [0.1.0] - 2026-10-19

第一个发布版。喵盒从「Laragon 的替代品」变成「能自己站住的东西」。

### 新增

#### 配置生成

- 把模板渲染成真的 `httpd.conf` / `nginx.conf` / 虚拟主机 / PHP 挂载配置
- 渲染完自动跑 `httpd -t` / `nginx -t` 语法自检，配置有错就不启动 —— 不让服务半死不活
- 站点 = `www/<文件夹名>`，域名 = `<文件夹名>.meow`；后缀可用 `tld` 命令改
- 换后缀时自动清掉旧后缀的 vhost 片段，不留残余

#### 进程看管

- 启动 / 停止 / 重启 / 状态，PID 记在 `run/`
- 优雅停止：Apache 走 `httpd -k shutdown`、MySQL 走 `mysqladmin shutdown`、Nginx 走 `-s quit`
- **绝不硬杀 mysqld** —— `taskkill /F` 会把 InnoDB 弄坏
- 端口被占时报出占用者（进程名 + PID），必要时弹 UAC 提权释放

#### hosts 维护

- 用成对标记 `# >>> meowbox` / `# <<< meowbox` 圈住自己的条目
- 删站点只动这一块，用户手写的条目一个字不碰
- 没管理员权限时明确报错，不静默失败

#### 双 Web 服务器

- Apache ↔ Nginx 随时切换，同一时间只跑一个
- Nginx 靠 `php-cgi -b 127.0.0.1:9000` 跑 PHP（FastCGI），喵盒把它当第三个进程看管
- 改配置后 `nginx -s reload` 热加载，连接不断

#### HTTPS

- `ssl` 一条命令：建 CA → 签证书 → 装进当前用户信任库 → 开 443
- 自签证书带 SAN（只写 CN 现代浏览器不认）
- `ssl check` 六层体检：开关 → 根证书 → 站点证书 → SSL vhost → 端口 → **真握手**
- 最后一层带 SNI 真连一次，能抓出「给的是别的站的证书」和「443 上其实是明文 HTTP」

#### 软件管理

- `packages.conf` 清单 + `pkg install` 从官方源下载解压
- `vendor` 把组件复制进自己的 `bin/`、`provider` 设 `null` —— 彻底不依赖 Laragon
- 复制 PHP 后自动改 `php.ini` 里六处写死的路径，否则 Laragon 一删 PHP 就没扩展

#### 从 Laragon 迁移

- 站点整目录复制，目标已存在默认跳过
- 数据库**绝不直接拷 datadir**：复制到临时副本 → 用 Laragon 的 mysqld 在另一端口起实例 → `mysqldump` → 导进喵盒 → 关实例删副本。原件一个字节不变
- `--tld auto` 保住 `.loc` 后缀，否则 MediaWiki 的 `$wgServer` 会对不上

#### Mailpit 本地假邮箱

- SMTP 收信 + 网页看信（默认 2525 / 8025）
- `php.ini` 的 `sendmail_path` 指向它，PHP 的 `mail()` 直达
- 一律绑 `127.0.0.1`，不惊动防火墙、隔壁机器也塞不进来

#### 图形界面

- **系统托盘**：纯 `ctypes` 调 Win32 `Shell_NotifyIcon`，没装任何第三方库；右键菜单每次现扫 `www/`
- **控制面板**：`tkinter`，菜单栏分 文件 / 服务 / 站点 / 工具 / 帮助
- **双击启动器**：`.vbs`（必须存成 UTF-16LE，WSH 不认 UTF-8 的 BOM）
- **开机自启**：只是「启动」文件夹里一个 `.vbs`，不写注册表
- 图标是纯 Python 画的猫爪（4 倍超采样抗锯齿），生成多尺寸 `.ico`

#### 日志

- 喵盒自己写的（体检记录、vendor 日志）按大小滚动
- 服务器写的（`apache-*` / `site-*` / `*-console`）只清超过 7 天 / 2MB 的
- **服务在跑时绝不截断日志**（Windows 会把文件前段写成 NUL 空洞）
- `https-check.log` 不参与清理 —— 留着当证据链

#### 打包与发布

- `build_exe.py` + `meowbox.spec`：一次产出三个 exe（命令行 / 托盘 / 面板），共用一份 `_internal/`
- 「冻结后行为不同」的东西全收在 `meow/runtime.py` 一个门面里
- 窗口版没有控制台时 `print()` 自动改写到日志文件，不会一 print 就崩
- GitHub Release 提供免安装 zip（16.1 MB，解压即用，机器上不用装 Python）

#### 开发环境

- 运行时**零第三方依赖**：托盘是 `ctypes`、面板是 `tkinter`、图标纯 Python 画
- 用 [uv](https://docs.astral.sh/uv/) 管环境：`pyproject.toml` + `uv.lock` + `.python-version`
- Python 钉在 **3.14**（精简解释器缺 `ctypes.wintypes.HCURSOR`，托盘会当场 `AttributeError`）
- PyInstaller 钉在 **6.22.3**（大版本一变，onedir 结构和 hook 行为就跟着变）

#### 便携

- 不装 Windows 服务、不写注册表（唯一例外：SSL 根证书装进当前用户证书库，`ssl untrust` 可撤）
- 配置里 `root` 永远是 `"."`，真正的根目录 = 配置文件所在目录 —— 文件夹搬到哪个盘都能跑

### 修复

- **托盘在未初始化时直接报错退出**：双击 `meowbox-tray.exe` 的用户不会开命令行，
  被一句「先运行 meowbox.exe init」顶回去只能一脸懵。现在抽了 `_ensure_config()`，
  托盘和面板共用：没配置就先按默认值初始化（有 Laragon 就借它的组件），然后照常启动
- **`meowbox.exe --help > NUL` 崩溃**：Windows 的 `NUL` 设备 `isatty()` 竟然返回 `True`，
  原来的判断把它当成真控制台、留着 GBK 编码，写 `🐾` 就 `UnicodeEncodeError`。
  改用 Win32 `GetConsoleMode` 判断「真的是控制台窗口吗」，NUL 会被正确识别成非控制台并改成 UTF-8。
  真控制台行为不变（走 `WriteConsoleW`，截图验证过）

### 已知限制

- **只支持 Windows**：`ctypes` 调 Win32、`tkinter` 面板
- **PHP 只有一个版本**：多版本切换在计划里
- **站点级 rewrite 要手改**：写进 `auto.*.conf`，重新渲染就没了
- **喵盒和 Laragon 不能同时跑**：抢 80 / 3306 端口

[未发布]: https://github.com/ClanceyPRMS/Meow-Box/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/ClanceyPRMS/Meow-Box/releases/tag/v0.2.0
[0.1.0]: https://github.com/ClanceyPRMS/Meow-Box/releases/tag/v0.1.0

# 在 Linux 上用喵盒（Arch 已验证）

> 这一篇从 [README](../README.md) 里搬出来的长文。它没被删，只是不该挤在首页。

## Linux（Arch 已验证）

0.3.0 起同一套代码也能在 Linux 上跑。平台差异全在 `meow/plat.py` 一个文件里，
Windows 的行为没变。

### 装组件

```bash
sudo pacman -S --needed apache php php-cgi mariadb nginx nss libcap tk
# Debian/Ubuntu：sudo apt install apache2 php php-cgi mariadb-server nginx libnss3-tools libcap2-bin python3-tk
```

> **`tk` 是给控制面板的**：Arch 的 `python` 包不自带 Tk 图形库，少了它会报
> `libtk8.6.so: cannot open shared object file`。服务本身不需要它，命令行全都能用。

**为什么不自己下二进制**：`bin/` 里那套是 Windows 版（`.exe`），而且 Linux 上的
`.so` 得跟系统的 glibc / 依赖库对上；发行版的包还给安全更新。喵盒的活是
「生成配置 + 看管进程 + 维护 hosts」，程序本身交给系统。
（`mailpit` 是例外——官方仓库没有，`python meowbox.py pkg install mailpit` 自己下静态二进制。）

### 第一次跑

```bash
python meowbox.py --root ~/meowbox init      # provider 留空 = 自动探测系统包
python meowbox.py --root ~/meowbox info      # 看探测到的是什么
python meowbox.py --root ~/meowbox start all
```

> `--root` 是**数据目录**（配置、`www/`、`logs/`、`data/` 都在这儿）。
> 代码可以在别处（比如一个 NTFS 盘上），数据目录建议放在 Linux 自己的分区上。

### 端口：默认 8080 / 8443

Linux 上低于 1024 的端口只有 root 能绑。喵盒的底线是「服务不用 root 跑」，
所以新装实例默认用 8080 / 8443。真想用 80 / 443：

```bash
python meowbox.py cap on      # setcap 给 httpd/nginx 一个 CAP_NET_BIND_SERVICE
python meowbox.py tld meow    # 然后把 web.http_port 改回 80、https_port 改 443 再 render
```

`pacman -Syu` 换掉二进制之后这个能力会丢（xattr 跟着文件走），升级完再跑一次。

### hosts

Linux 的 `/etc/hosts` 是 root 的。`hosts --apply` 会调 `pkexec` 弹认证框；
桌面上没有 polkit agent 时它会挂住（喵盒 45 秒后放弃），并把那条命令直接打给你：

```bash
sudo cp /tmp/meowbox-hosts.new /etc/hosts
```

### HTTPS 的信任库

Windows 装进「当前用户的证书库」；Linux 上是 **NSS**：
`~/.pki/nssdb`（Chromium/Chrome）+ 每个 Firefox profile 的 `cert9.db`，
另外往 p11-kit 的用户级 anchors 放一份给 curl / GTK。都不需要 root。

### 没有的东西

- **托盘**：Win32 `Shell_NotifyIcon` 专用。Linux 上用面板，或者
  `python meowbox.py shortcut` 生成 `.desktop` 拖进应用菜单。
- **`migrate` / `vendor`**：Laragon 专用，Linux 上会说明并退出。
- **mod_php**：发行版的 `php` 包不带（Arch 的 `libphp.so` 在 AUR 的
  `php-apache` 里），所以 Apache 也全员走 `php-cgi` + `mod_proxy_fcgi`。

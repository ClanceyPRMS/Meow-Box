# 同一个目录，两个系统轮着用

> 这一篇从 [README](../README.md) 里搬出来的长文。它没被删，只是不该挤在首页。

## 跨系统开发：同一个目录两个系统轮着用

我们的实际用法是**源码放在那块 NTFS 共享盘上**（`D:\meowbox` ↔
`/run/media/…/meowbox`），两个系统各用各的数据目录。这样最大的好处是改代码
不用来回拷；代价是有几个坑，下面都实测过。

### 实测：ntfs3 比想象中好，但也没好到能忘了它

| 检查项 | 结果 |
|---|---|
| Unix 权限位 | ✅ 能读能设（`chmod 755` 真的生效，存在 NTFS ACL 里） |
| 符号链接 | ✅ 能建、能进 git（mode `120000`） |
| 大小写 | ⚠️ Linux 侧**敏感**，Windows 侧**不敏感** ← 唯一真正不一致的地方 |
| 时间戳 | ✅ nanosecond 精度 |
| `git fsck` | ✅ 通过 |

所以「NTFS 上不能放代码」是过时的说法。要小心的只有四件事：

1. **别共用 `data/mysql`**。Windows 那边是 MySQL 8.x（数据字典是 `mysql.ibd`），
   Linux 这边是 MariaDB（`mysql/` 目录）。格式不兼容 ——
   轻则起不来，重则 **MariaDB 尝试初始化把系统表覆盖掉**。
   0.3.0 起 `db init` / `start mysql` 会先看 `foreign_datadir()`，
   认出「这是别的数据库建的」就直接拒绝，并给出换目录的建议。
2. **别在 Linux 上 `--root` 指到 Windows 那份**。
   `init` 现在发现已经有 `meowbox.config.json` 就**不覆盖**了，
   要重来必须显式 `--force`（以前会默默把端口、站点 PHP 版本全换掉）。
3. **换行符**。`.gitattributes` 是 `* text=auto`，仓库统一存 LF；
   仓库里另外设了 `core.autocrlf=false` + `core.eol=lf`，
   让两个系统的工作区字节完全一致（不然会出现「整文件都是改动」的假 diff）。
4. **`__pycache__`** 两边会互相覆盖、互相失效 —— 只是重编译，不影响正确性。
   真要清爽就把 Python 缓存指到别处（`PYTHONPYCACHEPREFIX`）。

### 版本控制

`D:\meowbox\.git` 是**两个系统共用**的仓库（`core.fileMode=false`，
不然 NTFS 的 ACL 变化会被当成「文件改了」）。历史里有两条线：

```
3d7b0c4  0.3.0：支持 Arch Linux（同一套代码双平台）
1ef7d77  0.2.5（从 10-31 的备份倒推的基线）
```

运行时数据（`bin/ www/ data/ etc/ logs/ run/ cache/ _internal*`）全在
`.gitignore` 里，仓库只跟踪源码，所以 `.git` 一直很小（几百 KB）。

> **建议**：`git` 历史在 NTFS 上，万一那块盘出事就一起没了。
> 定期 `git push` 到 GitHub（或者拷一份 `.git` 到 ext4）比较稳。

### 数据目录放哪

| 东西 | 放哪 | 为什么 |
|---|---|---|
| 源码 | NTFS（两边共用） | 改一次两边都生效，不用拷 |
| Windows 数据 | `D:\meowbox` | 那边本来就是整份目录 |
| Linux 数据 | Linux 原生分区（`~/meowbox`，ext4/btrfs） | 数据库文件不该跨系统轮着开 |

`python meowbox.py --root ~/meowbox info` 会在数据目录位于 NTFS / exFAT 这类
共享盘上时给一行提示。`--root` 是数据目录，跟代码在哪无关。

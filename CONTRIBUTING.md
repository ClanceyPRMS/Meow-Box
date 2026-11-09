# 参与喵盒 / Contributing to Meow Box

先说一句：喵盒是**一个人在自己两台机器上写出来的东西**（Windows + Arch Linux），
所以它有很多「作者自己的习惯」。下面这些不是官僚流程，是**避免让我返工**的说明。

## 你能帮上忙的地方

| 想做什么 | 怎么做 |
|---|---|
| 报 bug | 开 Issue，带上 `meowbox.py info` 的输出 + 复现步骤 + `logs/` 里相关的日志 |
| 报安全问题 | **别开公开 Issue**，走 [SECURITY.md](SECURITY.md) |
| 提新功能 | 先开 Issue 说清楚「你现在是怎么绕过去的」。喵盒的定位是「不造轮子」，很多需求其实用 `.htaccess` 就够了 |
| 改代码 | 见下面的「硬规矩」，然后开 PR |
| 翻译 / 写文档 | 非常欢迎 —— README 目前只有中文，英文版是明确的待办 |

## 跑起来

```bash
git clone https://github.com/ClanceyPRMS/Meow-Box.git
cd Meow-Box

# Windows：有 Laragon 就借它的二进制
python meowbox.py init --provider "D:/laragon"
# 没有（或 Linux）：不要 provider，改用发行版的包
python meowbox.py init --provider ""
```

Linux（Arch 已验证）还需要系统包和 Tk 库，见 README 的「Linux」一节。
喵盒**本体零第三方依赖** —— 这是它的卖点，别往 `dependencies` 里加东西。

## 跑测试（改完必跑）

```bash
uv run python -m pytest        # 或者 pip install pytest && python -m pytest
```

测试只碰**纯逻辑**：模板渲染、hosts 拼接、配置相对化、清单解析、版本号一致性。
不启任何服务，所以在 Linux 上也能跑 —— 改 `hosts.py` / `config.py` / `vhost.py`
不需要切回 Windows 就能验证。

改完再补两条人工冒烟（测试盖不住的）：

```bash
python meowbox.py render              # 内部会跑 httpd -t / nginx -t
grep -n '@' etc/apache2/httpd.conf    # 占位符不能有残留
```

## 硬规矩（PR 会按这几条看）

1. **平台差异只许出现在 `meow/plat.py`**。业务模块里不许写 `os.name == "nt"`、
   不许自己拼 `bin/httpd.exe` —— 一律用 `plat.IS_WINDOWS` / `plat.as_paths()`。
2. **Windows 的行为一个字都不许变**。加 Linux 分支时最容易犯的错是「对称着
   两边都写一条」：典型是 MPM 和 `DefaultRuntimeDir` —— Linux 要，Windows 写了
   就是「找不到指定的模块」。判断依据是**本地有没有那个文件**，不是对称直觉。
3. **模板占位符用 `@名字@`，不用 `str.format`**。Apache 配置里全是 `{}` 和
   `%{...}i`，format 会炸。`render_template` 只替换一遍，Python 里拼好的片段
   要自己把路径插好。
4. **改 hosts 只动 `# >>> meowbox` 和 `# <<< meowbox` 之间**。用户手写的行
   一个字都不能碰，`desired_text()` 必须可逆（`tests/test_hosts.py` 在守这条）。
5. **文档跟着代码走**：加了功能，README 四处一起改 —— `## 命令一览`、
   `## 目录结构`、`## 设计上的几个小心思`、`## 还没做（TODO）`。
   只改代码不写文档的 PR 会被要求补上（这是项目最珍视的部分）。
6. **别破坏便携**：不装 Windows 服务、不写注册表（自签 CA 装进当前用户证书库
   是唯一例外，且可撤销）。配置里根目录永远是 `.`。
7. **动数据要能回头**：任何搬数据库、改 TLD、批量替换站点地址的改动，
   先备份、先 `--dry-run`、验收标准写进 PR 说明。

## 代码风格

- 只写标准库（`urllib` / `zipfile` / `ctypes` / `tkinter`），不引第三方运行时依赖。
- 类型标注和中文注释尽量写：注释解释**为什么**，不解释「这行在做什么」。
  踩过的坑值得写成注释或测试，它们是真的会再犯。
- 提交信息用 `<类型>: <中文一句话>`，类型沿用现在的习惯：
  `feat:` / `fix:` / `docs:` / `chore:` / `refactor:`。
  一个提交只做一件事 —— 便于 `git bisect`。
- 换行：仓库统一 LF（`core.autocrlf=false`、`core.eol=lf`），别把 CRLF 提交上来。

## 提交 PR 前对一遍

- [ ] `python -m pytest` 全绿
- [ ] `python -m compileall -q meow meowbox.py build_exe.py` 通过
- [ ] 在**另一个平台**上跑过一次（跨平台改动必须两边都真跑，单平台跑绿照样在对面炸）
- [ ] README / CHANGELOG 更新了
- [ ] 没有把 `bin/`、`www/`、`data/`、`meowbox.config.json`、exe 提交上来

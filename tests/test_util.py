"""模板渲染：喵盒所有配置都是「@名字@」占位符渲染出来的，这里钉住它的约定。

为什么值得单独测：`render_template` 用的是 `str.replace` 而不是 `str.format`，
是因为 Apache/Nginx 配置里全是 `{ }` 和 `%{...}i`，format 会当成占位符炸掉。
这个决定一旦被改回去，`httpd -t` 不一定会报错（它不打开 TypesConfig 指的
文件），要等服务真启动才在 logs/apache-error.log 里炸 —— 所以用测试兜住。
"""
from __future__ import annotations

from meow import util


def test_replaces_placeholders():
    assert util.render_template("@a@-@b@", a=1, b="x") == "1-x"


def test_leaves_apache_and_nginx_syntax_alone():
    """Apache 的 `%{...}` 和 Nginx 的 `$uri` 一个字符都不能动。"""
    text = (
        "RewriteCond %{HTTP_HOST} ^(.*)$ [NC]\n"
        "Header set X-Frame-Options {deny}\n"
        "try_files $uri $uri/ /index.php?$query_string;\n"
    )
    assert util.render_template(text, a="A") == text


def test_keeps_placeholders_it_was_not_given():
    """没传值的占位符必须原样留着 —— 渲染完要靠 `grep '@'` 查漏。

    这是踩过的坑：`render_template` 只替换一遍，所以在 Python 里拼好的
    `SSL_MODULES=` 这类片段里写 `@MODULES_DIR@` 的话，它会原样落到配置文件里。
    如果这里改成「不认识就替换成空串」，那个 bug 就再也看不出来了。
    """
    assert util.render_template("@known@ @missing@", known="k") == "k @missing@"


def test_values_are_not_format_strings():
    """值里的 `%`、`\\`、`{}` 都不解释（旧实现用 str.format 就在这崩）。"""
    assert util.render_template("@p@", p=r"C:\web\%{x}{y}") == r"C:\web\%{x}{y}"


def test_fwd_turns_backslashes_into_slashes():
    assert util.fwd(r"C:\meowbox\www") == "C:/meowbox/www"
    assert util.fwd("/home/x/www") == "/home/x/www"

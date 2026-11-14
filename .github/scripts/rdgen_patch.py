#!/usr/bin/env python3
"""rdgen 源码补丁工具。

用语义匹配替代「按行号 git apply」和「按行区间 sed」，
避免 rustdesk 上游代码一变动就把源码改坏（例如把函数体切断导致 unexpected closing delimiter）。
用法:
    python rdgen_patch.py xoffline          # 离线状态显示红色 X 而不是橙圈
    python rdgen_patch.py no-update-notif   # 关闭新版本升级提示 / 客户端更新检查
    python rdgen_patch.py links             # 把源码里所有 rustdesk.com 链接换成自定义官网/下载链接
    python rdgen_patch.py brand             # sciter 客户端：去掉「由 xxx 提供支持」并在原位居中放自定义图标
    python rdgen_patch.py no-install-tip    # sciter 客户端：去掉「你正在运行未安装版本…」粉色提示
    python rdgen_patch.py copy-credentials  # sciter 客户端：左栏下方加「复制 ID 和密码」按钮
    python rdgen_patch.py flutter-copy-credentials  # flutter 客户端：同上（只改 dart）
两者都幂等，重复执行不会破坏文件。
"""
import os
import re
import sys

# Windows 的 Python 控制台默认是 cp1252，打印中文日志会抛 UnicodeEncodeError
# 并让脚本中途退出（补丁没打上、构建却仍显示 success）。强制改成 UTF-8。
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

def read(path: str) -> str:
    """读入并统一成 LF。

    Windows runner 上 actions/checkout 拉下来的源码是 CRLF，锚点里写的 "\n"
    会因为多出来的 "\r" 匹配不上（曾经导致 ui.rs / inline-sciter.py 的补丁静默跳过）。
    """
    with open(path, "r", encoding="utf-8", newline="") as f:
        return f.read().replace("\r\n", "\n").replace("\r", "\n")

def write(path: str, content: str) -> None:
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(content)

def patch_peer_card() -> None:
    """getOnline() 里的离线橙圈 -> 红色 X 图标。"""
    path = "flutter/lib/common/widgets/peer_card.dart"
    if not os.path.exists(path):
        print("[error] 找不到 %s" % path)
        sys.exit(1)
    s = read(path)
    if "Icons.close" in s:
        print("[skip] peer_card.dart 已经打过离线 X 图标补丁")
        return
    pattern = re.compile(
        r"CircleAvatar\(\s*radius:\s*3,\s*backgroundColor:\s*online\s*\?\s*Colors\.green\s*:\s*kColorWarn\s*\)",
        re.S,
    )
    replacement = (
        "online\n"
        "              ? const CircleAvatar(radius: 3, backgroundColor: Colors.green)\n"
        "              : const Icon(Icons.close, color: Colors.red, size: 12.0)"
    )
    new_s, count = pattern.subn(replacement, s)
    if count == 0:
        print("[warn] 没匹配到 getOnline 里的离线圆圈代码，源码可能已改版，跳过")
        return
    write(path, new_s)
    print("[ok] peer_card.dart: 离线状态改为红色 X 图标（%d 处）" % count)

def _iter_braces(s):
    """按 Rust 词法扫描，只吐出真正的大括号（跳过字符串/字符/注释里的括号）。

    rustdesk 源码里有 format!("{}")、"{" 这类字面量，按字符硬数括号会把函数体
    提前截断，进而把整个文件切坏（cargo 报 unexpected closing delimiter）。
    """
    n = len(s)
    i = 0
    while i < n:
        c = s[i]
        if c == "/" and i + 1 < n and s[i + 1] == "/":
            j = s.find("\n", i)
            i = n if j < 0 else j
            continue
        if c == "/" and i + 1 < n and s[i + 1] == "*":
            j = s.find("*/", i + 2)
            i = n if j < 0 else j + 2
            continue
        if c == '"':
            i += 1
            while i < n:
                if s[i] == "\\":
                    i += 2
                elif s[i] == '"':
                    i += 1
                    break
                else:
                    i += 1
            continue
        if c == "'":
            # 字符字面量 'x' / '\n'；生命周期 'a 直接跳过
            if (i + 2 < n and s[i + 2] == "'") or (i + 1 < n and s[i + 1] == "\\"):
                i += 1
                while i < n:
                    if s[i] == "\\":
                        i += 2
                    elif s[i] == "'":
                        i += 1
                        break
                    else:
                        i += 1
                continue
            i += 1
            continue
        if c in "{}":
            yield i, c
        i += 1


def _matching_brace(s: str, brace: int) -> int:
    """返回与第 brace 个字符（'{'）配对的那个 '}' 的下标，找不到返回 -1。"""
    depth = 0
    for idx, ch in _iter_braces(s):
        if idx < brace:
            continue
        if ch == "{":
            depth += 1
        else:
            depth -= 1
            if depth == 0:
                return idx
    return -1


def _braces_balanced(s: str) -> bool:
    """整文件括号健康检查：写入前用它兜底，宁可不打补丁也不能把源码切坏。"""
    depth = 0
    for _, ch in _iter_braces(s):
        depth += 1 if ch == "{" else -1
        if depth < 0:
            return False
    return depth == 0


def _strip_function_body(decl: str) -> None:
    """把 src/common.rs 里 do_check_software_update 的整个函数体替换成 return Ok(())。"""
    path = "src/common.rs"
    if not os.path.exists(path):
        print("[error] 找不到 %s" % path)
        sys.exit(1)
    s = read(path)
    signature = "pub async fn do_check_software_update() -> hbb_common::ResultType<()> {"
    if signature not in s:
        print("[warn] 没找到 do_check_software_update 函数，源码可能已改版，跳过")
        return
    if "// rdgen: skip update check" in s:
        print("[skip] src/common.rs 已经打过关闭更新检查补丁")
        return
    start = s.index(signature)
    brace = s.index("{", start)
    end = _matching_brace(s, brace)
    if end < 0:
        print("[warn] 括号配对失败，跳过 src/common.rs（不写文件，避免把源码切坏）")
        return
    new_body = (
        "pub async fn do_check_software_update() -> hbb_common::ResultType<()> {\n"
        "    // rdgen: skip update check\n"
        "    return Ok(());\n"
        "}"
    )
    new_s = s[:start] + new_body + s[end + 1:]
    if not _braces_balanced(new_s):
        print("[warn] 替换后括号不平衡，放弃写入 src/common.rs（保持源码原样）")
        return
    write(path, new_s)
    print("[ok] src/common.rs: do_check_software_update 函数体整体替换为 return Ok(())")

def patch_update_url() -> None:
    """桌面首页的 updateUrl.isNotEmpty -> false（隐藏新版本提示）。"""
    path = "flutter/lib/desktop/pages/desktop_home_page.dart"
    if not os.path.exists(path):
        print("[error] 找不到 %s" % path)
        sys.exit(1)
    s = read(path)
    if "updateUrl.isNotEmpty" not in s:
        print("[skip] desktop_home_page.dart 已经改过 updateUrl 判断")
        return
    s = s.replace("updateUrl.isNotEmpty", "false")
    write(path, s)
    print("[ok] desktop_home_page.dart: updateUrl.isNotEmpty -> false")

def patch_links() -> None:
    """把源码里硬编码的 rustdesk.com 链接换成自定义官网 / 更新下载链接。

    覆盖 sciter(src/ui/index.tis)、flutter 桌面/移动首页与更新进度页、以及旧版 updater.rs。
    环境变量: LINK_HOME(自定义官网链接) LINK_DOWNLOAD(更新下载链接)
    """
    home = (os.environ.get("LINK_HOME") or "").strip().rstrip("/")
    dl = (os.environ.get("LINK_DOWNLOAD") or "").strip().rstrip("/")
    if home in ("", "https://rustdesk.com"):
        home = ""
    if dl in ("", "https://rustdesk.com/download"):
        dl = ""
    if not home and not dl:
        print("[skip] 没有配置自定义官网/下载链接，跳过")
        return

    targets = [
        "src/ui/index.tis",
        "src/updater.rs",
        "flutter/lib/desktop/pages/desktop_home_page.dart",
        "flutter/lib/mobile/pages/connection_page.dart",
        "flutter/lib/desktop/widgets/update_progress.dart",
        "flutter/lib/desktop/pages/desktop_setting_page.dart",
    ]

    # 「设置 - 关于」里的 Website 链接单独处理：指向更新下载链接，而不是官网。
    # 这段要在通用替换之前做，否则 https://rustdesk.com 已经被换成官网链接了；
    # 同时也兼容工作流里 sed 先把它换成官网链接的情况。
    def patch_about_website(text: str, want: str) -> str:
        if not dl:
            return text
        candidates = ["https://rustdesk.com"] + ([home] if home else [])
        for old in [c for c in candidates if c]:
            # sciter: <div .link .custom-event url='https://rustdesk.com'>" + translate("Website")
            text = text.replace(
                "<div .link .custom-event url='%s'>\" + translate(\"Website\")" % old,
                "<div .link .custom-event url='%s'>\" + translate(\"Website\")" % dl,
            )
            # flutter: launchUrlString('https://rustdesk.com'); ... translate('Website')
            text = re.sub(
                r"(launchUrlString\('%s'\);\s*\}\s*,\s*child: Text\(\s*translate\('Website'\))"
                % re.escape(old),
                lambda m: m.group(1).replace(old, dl),
                text,
            )
        return text

    for path in targets:
        if not os.path.exists(path):
            continue
        s = read(path)
        before = s
        s = patch_about_website(s, dl)
        # 1) 更新下载链接（含旧版 updater.rs 的 &download_url 占位）
        if dl:
            s = s.replace("https://rustdesk.com/download", dl)
            s = s.replace("&download_url", '"%s"' % dl)
            s = s.replace("$downloadUrl/$downloadFile", dl)
        # 2) 官网 / 隐私 / 博客等其余 rustdesk.com 链接
        if home:
            s = s.replace("https://rustdesk.com/privacy.html", home + "/privacy.html")
            s = s.replace("https://rustdesk.com/blog/id-relay-set/", home + "/blog/id-relay-set/")
            if not dl:
                s = s.replace("https://rustdesk.com/download", home + "/download")
            s = s.replace("https://rustdesk.com", home)
        if s != before:
            write(path, s)
            print("[ok] %s: 已替换自定义链接" % path)
        else:
            print("[info] %s: 无匹配，未改动" % path)

    # 输出校验信息，方便在 Actions 日志里确认是否真的替换成功
    for path in ("src/ui/index.tis", "flutter/lib/desktop/pages/desktop_home_page.dart"):
        if os.path.exists(path):
            rest = [ln.strip() for ln in read(path).splitlines() if "rustdesk.com" in ln]
            if rest:
                print("[warn] %s 仍有 %d 处 rustdesk.com 链接:" % (path, len(rest)))
                for ln in rest[:5]:
                    print("       " + ln[:120])
            else:
                print("[ok] %s: 已无 rustdesk.com 硬编码链接" % path)


LOGO_CANDIDATES = ["res/logo.png", "res/128x128.png", "res/64x64.png", "res/icon.png"]

def _png_size(raw: bytes):
    """从 PNG 的 IHDR 里读宽高，用来按原始比例算显示尺寸（避免把横向 Logo 压成方块）。"""
    try:
        if raw[:8] != b"\x89PNG\r\n\x1a\n":
            return None
        w = int.from_bytes(raw[16:20], "big")
        h = int.from_bytes(raw[20:24], "big")
        if w > 0 and h > 0:
            return w, h
    except Exception:
        pass
    return None


def _fit_box(w: int, h: int, max_w: int, max_h: int):
    """等比缩放到 max_w x max_h 的框内，不裁切、不拉伸。"""
    scale = min(float(max_w) / w, float(max_h) / h)
    if scale > 1:
        scale = 1.0
    return max(1, int(round(w * scale))), max(1, int(round(h * scale)))


def _logo_data_uri():
    """优先用「应用 Logo」(res/logo.png) 转 data URI；没有就退回应用图标。

    返回 (data_uri, 显示宽, 显示高)，显示尺寸按原始宽高比等比缩放。
    """
    import base64
    for path in LOGO_CANDIDATES:
        if os.path.exists(path) and os.path.getsize(path) <= 1024 * 1024:
            with open(path, "rb") as f:
                raw = f.read()
            size = _png_size(raw)
            if size:
                dw, dh = _fit_box(size[0], size[1], 200, 84)
                note = "%dx%d -> 显示 %dx%d" % (size[0], size[1], dw, dh)
            else:
                dw, dh = 72, 72
                note = "尺寸未知，按 72x72"
            b = base64.b64encode(raw).decode()
            print("[ok] 使用 %s 作为界面 Logo（%d bytes，%s）" % (path, len(raw), note))
            return "data:image/png;base64," + b, dw, dh
    print("[warn] 没找到可用的 png，界面只去掉 powered-by 文字，不插入 Logo")
    return "", 0, 0

def patch_brand() -> None:
    """sciter 客户端：把「由 xxx 提供支持」换成居中的自定义图标。

    rustdesk 里这行是：
      {is_custom_client && handler.get_builtin_option("hide-powered-by-me") != "Y"
          ? <div .link #powered-by style="opacity:0.5;font-size:0.8em;text-decoration:underline">
              {translate('powered_by_me')}</div> : ""}

    改动仅限这个 div 内部：换掉样式和文字内容，不动 TISX 的表达式结构。
    之前那版直接把整段 {…} 换成静态 div 并插入 `<!-- -->` 注释，导致 sciter 的
    TISX 解析失败、整个界面渲染成黑屏（index.tis 上游一个 HTML 注释都没有）。
    """
    path = "src/ui/index.tis"
    if not os.path.exists(path):
        print("[error] 找不到 %s" % path)
        sys.exit(1)
    s = read(path)
    if "#rdgen-brand" in s:
        print("[skip] index.tis 已经处理过 powered-by / logo")
        return
    logo, lw, lh = _logo_data_uri()
    if not logo and "hide-powered-by-me" not in s:
        print("[skip] 既没有可用图标，也没有 powered-by 那行，未改动")
        return

    open_tag_old = ('<div .link #powered-by '
                    'style="opacity:0.5;font-size:0.8em;text-decoration:underline">')
    open_tag_new = ('<div .link #rdgen-brand '
                    'style="flow:horizontal; horizontal-align:center; '
                    'padding:2px 0 6px 0; opacity:1;">')

    new_s = s
    if open_tag_old in new_s:
        new_s = new_s.replace(open_tag_old, open_tag_new, 1)
        if logo:
            new_s = new_s.replace(
                "{translate('powered_by_me')}",
                '<img src="%s" style="width:%dpx; height:%dpx;" />' % (logo, lw, lh), 1)
        else:
            new_s = new_s.replace("{translate('powered_by_me')}", "", 1)
    else:
        # 源码结构变了：退回到「整段隐藏」，不再插入 markup，先保证界面不黑屏
        pattern = re.compile(
            r'\{\s*is_custom_client\s*&&\s*handler\.get_builtin_option\("hide-powered-by-me"\)\s*!=\s*"Y"\s*'
            r'\?[\s\S]*?:\s*""\s*\}',
        )
        new_s, n = pattern.subn("", new_s)
        if n == 0:
            print("[warn] 没匹配到 powered-by 那一行，源码可能已改版，跳过")
            return
        print("[warn] powered-by 结构不匹配，只做了隐藏，没有插入 Logo")

    # 原 powered-by 点击事件会跳 rustdesk.com，元素 id 已改名，顺手删掉
    handler = re.compile(r'event\s+click\s+\$\(#powered-by\)\s*\{[^}]*\}')
    new_s, nh = handler.subn("", new_s)
    if nh:
        print("[ok] 已移除 #powered-by 的点击事件（%d 处）" % nh)

    # 兜底：确认没有引入 sciter 解析不了的东西（HTML 注释曾导致黑屏）
    if "<!--" in new_s or "-->" in new_s:
        print("[warn] 补丁结果里出现 HTML 注释，放弃写入 src/ui/index.tis")
        return

    write(path, new_s)
    print("[ok] src/ui/index.tis: 已移除 powered-by%s" % ("，并在原位居中显示 Logo" if logo else ""))



def patch_incoming_width() -> None:
    """仅受控（incoming-only）客户端的窗口宽度。

    - sciter(src/ui/index.tis): const incoming_only_width = 180 -> N（同时影响 windowMinSize 和输入框宽度）
    - flutter: imcomingOnlyHomeSize 的宽度 + _updateWindowSize 里的最小宽度钳制
    环境变量 INCOMING_ONLY_WIDTH，默认 360。
    """
    try:
        width = int((os.environ.get("INCOMING_ONLY_WIDTH") or "360").strip())
    except ValueError:
        width = 360

    # 1) sciter
    p = "src/ui/index.tis"
    if os.path.exists(p):
        s = read(p)
        s2, n = re.subn(r"const incoming_only_width = \d+;",
                        "const incoming_only_width = %d;" % width, s, count=1)
        if n:
            write(p, s2)
            print("[ok] src/ui/index.tis: incoming_only_width -> %d" % width)
        else:
            print("[info] src/ui/index.tis: 没找到 incoming_only_width，未改动（可能不是 sciter 客户端）")

    # 2) flutter 默认尺寸
    p = "flutter/lib/common.dart"
    if os.path.exists(p):
        s = read(p)
        try:
            height = int((os.environ.get("INCOMING_ONLY_HEIGHT") or "350").strip())
        except ValueError:
            height = 350
        s2, n = re.subn(r"var imcomingOnlyHomeSize = Size\((\d+(?:\.\d+)?), (\d+(?:\.\d+)?)\);",
                        lambda m: "var imcomingOnlyHomeSize = Size(%d, %d);" % (width, height),
                        s, count=1)
        if n:
            write(p, s2)
            print("[ok] flutter/lib/common.dart: imcomingOnlyHomeSize -> %dx%d" % (width, height))
        else:
            print("[info] flutter/lib/common.dart: 没匹配到 imcomingOnlyHomeSize")

    # 3) flutter 首帧后按内容缩窗，补一个最小宽度钳制
    p = "flutter/lib/desktop/pages/desktop_home_page.dart"
    if os.path.exists(p):
        s = read(p)
        marker = "size.width < %d" % width
        if marker in s:
            print("[skip] desktop_home_page.dart: 已经有最小宽度钳制")
        else:
            m = re.search(r"\n(\s*)final size = renderObject\.size;\n\1if \(size != imcomingOnlyHomeSize\) \{", s)
            if m:
                ind = m.group(1)
                new = ("\n%svar size = renderObject.size;\n"
                       "%sif (size.width < %d) size = Size(%d, size.height);\n"
                       "%sif (size != imcomingOnlyHomeSize) {" % (ind, ind, width, width, ind))
                write(p, s.replace(m.group(0), new, 1))
                print("[ok] desktop_home_page.dart: 最小宽度钳制为 %d" % width)
            else:
                print("[warn] desktop_home_page.dart: 没匹配到 _updateWindowSize，未改动")



def patch_help_card_css() -> None:
    """帮助卡片（粉色安装提示）往上收一点，别离上面的内容太远。"""
    p = "src/ui/index.css"
    if not os.path.exists(p):
        print("[info] 没有 src/ui/index.css，跳过")
        return
    s = read(p)
    if "rdgen: 帮助卡片上移" in s:
        print("[skip] src/ui/index.css: 帮助卡片间距已调整")
        return
    old = "div.install-me, div.trust-me {\n    margin-top: 0.5em;\n    padding: 20px;"
    new = ("div.install-me, div.trust-me {\n"
           "    /* rdgen: 帮助卡片上移，靠近上方内容 */\n"
           "    margin-top: -1em;\n"
           "    padding: 10px 20px 16px 20px;")
    if old not in s:
        print("[warn] src/ui/index.css: 没找到 install-me 规则，未调整")
        return
    s = s.replace(old, new, 1)
    write(p, s)
    print("[ok] src/ui/index.css: 帮助卡片上移（margin-top -1em，padding-top 10px）")


def patch_no_install_tip() -> None:
    """sciter 客户端：去掉「你正在运行未安装版本…」那块粉色安装提示。

    该提示由 index.tis 里的
      {(!is_win || handler.is_installed() || disable_installation) ? "" : <InstallMe />}
    渲染，把条件直接换成空字符串即可；<InstallMe /> 组件本身不动（设置页等仍在用）。
    横幅去掉后窗口最小高度 390 会留空白，一并收到 300。
    """
    p = "src/ui/index.tis"
    if not os.path.exists(p):
        print("[info] 没有 src/ui/index.tis，跳过安装提示补丁")
        return
    s = read(p)
    before = s

    cond_old = '{(!is_win || handler.is_installed() || disable_installation) ? "" : <InstallMe />}'
    cond_new = ('{(!is_win || handler.is_installed() || disable_installation'
                ' || handler.get_builtin_option("hide-help-cards") == "Y") ? "" : <InstallMe />}')
    if cond_old in s:
        s = s.replace(cond_old, cond_new, 1)
        print("[ok] src/ui/index.tis: 帮助提示默认显示，hide-help-cards=Y 时隐藏")
    else:
        print("[skip] src/ui/index.tis: 帮助提示条件已是目标状态或源码结构变了")

    patch_help_card_css()

    try:
        h = int((os.environ.get("INCOMING_ONLY_HEIGHT") or "350").strip())
    except ValueError:
        h = 350
    s, nh = re.subn(r"\? 300 : (?:300|390)", "? %d : %d" % (h, h), s, count=1)
    if nh:
        print("[ok] src/ui/index.tis: 仅受控窗口默认/最小高度 -> %d" % h)

    if s != before:
        write(p, s)

JS_PWD = "\\u5bc6\\u7801:  "

RG_TIP_FUNC = ('var rdgen_tip_deadline = 0;\n'
              'function rdgen_tip_hide() {\n'
              '    rdgen_tip_deadline = 0;\n'
              '    var el = $(#rdgen-tip);\n'
              '    if (el) el.style.set { display: "none" };\n'
              '}\n'
              'function rdgen_tip(text) {\n'
              '    rdgen_tip_deadline = Date.now() + 2000;\n'
              '    var el = $(#rdgen-tip);\n'
              '    if (!el) return;\n'
              '    el.text = text;\n'
              '    el.style.set { display: "block" };\n'
              '}\n'
              'function rdgen_tip_tick() {\n'
              '    if (!rdgen_tip_deadline) return;\n'
              '    if (Date.now() < rdgen_tip_deadline) return;\n'
              '    rdgen_tip_hide();\n'
              '}\n\n')

RG_TIP_MARKUP = ('                        <div #rdgen-tip style="display:none; position:absolute; '
                 'left:12px; right:12px; bottom:44px; padding:9px 6px; border-radius:6px; '
                 'background:#2b2b2b; color:#ffffff; font-size:0.9em; text-align:center; '
                 'z-index:99;"></div>\n')
RG_COPY_MARKUP = ('                        {!outgoing_only && handler.get_builtin_option("copybtn") == "Y" ? <div style="padding: 16px 0 10px 0">'
                  '<button #rdgen-copy-credentials .button .outline style="width:*; height:38px; font-size:1em">'
                  '&#22797;&#21046; ID &#21644;&#23494;&#30721;</button></div> : ""}\n')

RG_FLUTTER_METHOD = """
  Widget buildRdgenCopyButton() {
    return ChangeNotifierProvider.value(
      value: gFFI.serverModel,
      child: Consumer<ServerModel>(
        builder: (context, model, child) {
          return Padding(
            padding: const EdgeInsets.only(left: 20.0, right: 16, bottom: 10),
            child: SizedBox(
              width: double.infinity,
              height: 38,
              child: OutlinedButton(
                onPressed: () {
                  final id = model.serverId.text;
                  final pwd = model.serverPasswd.text;
                  var text = 'ID: ' + id;
                  if (pwd.isNotEmpty && pwd != '-') {
                    text = text + '\\n密码: ' + pwd;
                  }
                  Clipboard.setData(ClipboardData(text: text));
                  showToast('已复制');
                },
                child: Text('复制 ID 和密码', style: TextStyle(fontSize: 14)),
              ),
            ),
          );
        },
      ),
    );
  }

"""

RG_ADMIN_MARKUP = ('                        {!outgoing_only && !handler.is_installed() && !handler.rdgen_is_elevated() && handler.get_builtin_option("prebtn") == "Y" ? '
                  '<div style="padding: 0 0 10px 0"><button #rdgen-run-as-admin .button .outline '
                  'style="width:*; height:34px; font-size:1em">'
                  '&#31649;&#29702;&#21592;&#26435;&#38480;&#36816;&#34892;</button></div> : ""}\n')

RG_ADMIN_HANDLER = ('event click $(button#rdgen-run-as-admin) {\n'
                    '    var rg_st = handler.rdgen_run_as_admin();\n'
                    '    if (rg_st == "ask")\n'
                    '        rdgen_tip("\\u8bf7\\u70b9\\u51fb\\u201c\\u662f\\u201d\\u6388\\u6743");\n'
                    '    else\n'
                    '        rdgen_tip("\\u8bf7\\u70b9\\u51fb\\u201c\\u662f\\u201d\\u6388\\u6743\\uFF08" + rg_st + "\\uFF09");\n'
                    '}\n\n')


RG_COPY_HANDLER = ('event click $(button#rdgen-copy-credentials) {\n'
                   '    var rg_id = get_id();\n'
                   '    var rg_pwd = password_cache && password_cache.length > 0 ? password_cache[0] : "";\n'
                   '    var rg_text = "ID: " + rg_id;\n'
                   '    if (rg_pwd && rg_pwd != "-") rg_text = rg_text + "\\n' + JS_PWD + '" + rg_pwd;\n'
                   '    handler.rdgen_copy_text(rg_text);\n'
                   '    rdgen_tip("\\u590d\\u5236\\u6210\\u529f");\n'
                   '}\n\n')


def patch_copy_credentials() -> None:
    """仅受控客户端左栏下方加一个「复制 ID 和密码」按钮。

    - src/ui.rs: 暴露 handler.rdgen_copy_text(String)，内部用 arboard 写系统剪贴板
    - src/ui/index.tis: PasswordArea 之后插入按钮，事件处理放在脚本顶层
    - 文案用 HTML 实体 / \\u 转义，保证 .tis 里不出现非 ASCII 字节
    """
    p = "src/ui.rs"
    if os.path.exists(p):
        s = read(p)
        if "rdgen_copy_text" in s:
            print("[skip] src/ui.rs: 复制到剪贴板的 handler 已存在")
        else:
            anchor = "    fn get_id(&self) -> String {\n        ipc::get_id()\n    }\n"
            method = ('    fn rdgen_copy_text(&self, text: String) {\n'
                      '        use arboard::Clipboard as ClipboardContext;\n'
                      '        if let Ok(mut ctx) = ClipboardContext::new() {\n'
                      '            ctx.set_text(text).ok();\n'
                      '        }\n'
                      '    }\n\n'
                      '    fn rdgen_is_elevated(&self) -> bool {\n'
                      '        // 清掉历史版本写进配置的 pre-elevate-service=Y，避免每次启动都走便携服务\n'
                      '        std::sync::Once::new().call_once(|| {\n'
                      '            if hbb_common::config::LocalConfig::get_option("pre-elevate-service") == "Y" {\n'
                      '                let _ = hbb_common::config::LocalConfig::set_option(\n'
                      '                    "pre-elevate-service".to_owned(),\n'
                      '                    String::new(),\n'
                      '                );\n'
                      '            }\n'
                      '        });\n'
                      '        crate::platform::is_elevated(None).unwrap_or(false)\n'
                      '    }\n\n'
                      '    fn rdgen_run_as_admin(&self) -> String {\n'
                      '        use crate::portable_service::client;\n'
                      '        crate::portable_service::set_rdgen_elevate_once(true);\n'
                      '        client::set_quick_support(true);\n'
                      '        if client::running() {\n'
                      '            // 已有非提权助手在运行，无法原地升级，只能整进程提权重启\n'
                      '            std::thread::spawn(|| {\n'
                      '                if let Ok(true) = crate::platform::elevate("") {\n'
                      '                    std::process::exit(0);\n'
                      '                }\n'
                      '            });\n'
                      '            return "restart".to_owned();\n'
                      '        }\n'
                      '        std::thread::spawn(|| {\n'
                      '            match client::start_portable_service(client::StartPara::Direct) {\n'
                      '                Ok(()) => {}\n'
                      '                Err(e) => {\n'
                      '                    // 便携服务起不来时兜底：整进程以管理员身份重启，保证按钮有效\n'
                      '                    log::warn!("rdgen: start elevated portable service failed: {e}");\n'
                      '                    if let Ok(true) = crate::platform::elevate("") {\n'
                      '                        std::process::exit(0);\n'
                      '                    }\n'
                      '                }\n'
                      '            }\n'
                      '        });\n'
                      '        "ask".to_owned()\n'
                      '    }\n\n')
            if anchor in s:
                s = s.replace(anchor, anchor + method, 1)
                s = s.replace("        fn get_id();\n",
                              "        fn get_id();\n"
                              "        fn rdgen_copy_text(String);\n"
                              "        fn rdgen_is_elevated();\n"
                              "        fn rdgen_run_as_admin() -> String;\n", 1)
                write(p, s)
                print("[ok] src/ui.rs: 新增 handler.rdgen_copy_text")
            else:
                print("[warn] src/ui.rs: 没找到 get_id 锚点，剪贴板 handler 未插入")
    else:
        print("[info] 没有 src/ui.rs，跳过（flutter 客户端不需要）")

    patch_inline_sciter_encoding()

    p = "src/ui/index.tis"
    if not os.path.exists(p):
        print("[info] 没有 src/ui/index.tis，跳过")
        return
    s = read(p)
    if "rdgen-copy-credentials" in s:
        print("[skip] src/ui/index.tis: 复制按钮已存在")
        return
    anchor_markup = "                        {!outgoing_only && <PasswordArea />}\n"
    anchor_handler = "\nfunction updatePasswordArea() {"
    if anchor_markup not in s or anchor_handler not in s:
        print("[warn] src/ui/index.tis: 锚点没找到，按钮未插入")
        return
    s = s.replace(anchor_markup, anchor_markup + RG_COPY_MARKUP + RG_ADMIN_MARKUP
                  + RG_TIP_MARKUP, 1)
    s = s.replace("""    self.timer(1s, function() {
        var temporary_password = handler.temporary_password();""",
                  """    self.timer(1s, function() {
        rdgen_tip_tick();
        var temporary_password = handler.temporary_password();""", 1)
    s = s.replace(anchor_handler,
                  "\n" + RG_TIP_FUNC + RG_COPY_HANDLER + RG_ADMIN_HANDLER
                  + "function updatePasswordArea() {", 1)
    write(p, s)
    print("[ok] src/ui/index.tis: 已加入复制 ID 和密码按钮")

    patch_flutter_copy_button()


def patch_flutter_copy_button() -> None:
    """flutter 客户端（64 位标准端）左栏加「复制 ID 和密码」按钮。"""
    p = "flutter/lib/desktop/pages/desktop_home_page.dart"
    if not os.path.exists(p):
        print("[info] 没有 desktop_home_page.dart，跳过 flutter 复制按钮")
        return
    s = read(p)
    if "buildRdgenCopyButton" in s:
        print("[skip] desktop_home_page.dart: 复制按钮已存在")
        return
    anchor_call = "      if (!isOutgoingOnly) buildPasswordBoard(context),\n"
    anchor_method = "\n  buildPasswordBoard(BuildContext context) {"
    if anchor_call not in s or anchor_method not in s:
        print("[warn] desktop_home_page.dart: 锚点没找到，按钮未插入")
        return
    s = s.replace(anchor_call,
                  anchor_call + "      if (!isOutgoingOnly && bind.mainGetBuildinOption('copybtn') == 'Y') buildRdgenCopyButton(),\n", 1)
    s = s.replace(anchor_method, "\n" + RG_FLUTTER_METHOD + "  buildPasswordBoard(BuildContext context) {", 1)
    write(p, s)
    print("[ok] desktop_home_page.dart: 已加入复制 ID 和密码按钮")


RG_FLUTTER_ADMIN_METHOD = """
  Widget buildRdgenRunAsAdminButton() {
    if (bind.mainGetBuildinOption('prebtn') != 'Y') return SizedBox();
    if (bind.mainIsInstalled()) return SizedBox();
    if (bind.rdgenIsElevated()) return SizedBox();
    return Padding(
      padding: const EdgeInsets.only(left: 20.0, right: 16, bottom: 10),
      child: SizedBox(
        width: double.infinity,
        height: 38,
        child: OutlinedButton(
          onPressed: () {
            showToast('\\u8bf7\\u70b9\\u51fb\\u201c\\u662f\\u201d\\u6388\\u6743');
            bind.rdgenRunAsAdmin();
          },
          child: Text('\\u7ba1\\u7406\\u5458\\u6743\\u9650\\u8fd0\\u884c',
              style: TextStyle(fontSize: 14)),
        ),
      ),
    );
  }

"""

RG_FLUTTER_FFI_ANCHOR = """pub fn main_get_buildin_option(key: String) -> SyncReturn<String> {
    SyncReturn(get_builtin_option(&key))
}
"""

RG_FLUTTER_FFI_CODE = """

#[cfg(target_os = "windows")]
#[inline]
pub fn rdgen_is_elevated() -> SyncReturn<bool> {
    // rdgen: 顺带清掉历史版本写进本地配置的 pre-elevate-service=Y，
    // 避免每次启动都被拉起便携服务而弹 UAC
    std::sync::Once::new().call_once(|| {
        if hbb_common::config::LocalConfig::get_option("pre-elevate-service") == "Y" {
            let _ = hbb_common::config::LocalConfig::set_option(
                "pre-elevate-service".to_owned(),
                String::new(),
            );
        }
    });
    SyncReturn(crate::platform::is_elevated(None).unwrap_or(false))
}

#[cfg(target_os = "windows")]
pub fn rdgen_run_as_admin() {
    std::thread::spawn(|| {
        // 只把便携服务以管理员身份拉起（弹 UAC），界面进程不重启；
        // 一次性开关，下次正常启动不会弹窗
        crate::portable_service::set_rdgen_elevate_once(true);
        crate::portable_service::client::set_quick_support(true);
        if crate::portable_service::client::running() {
            if let Ok(true) = crate::platform::elevate("") {
                std::process::exit(0);
            }
            return;
        }
        match crate::portable_service::client::start_portable_service(
            crate::portable_service::client::StartPara::Direct,
        ) {
            Ok(()) => {}
            Err(e) => {
                // 便携服务起不来时兜底：整进程以管理员身份重启
                log::warn!("rdgen: start elevated portable service failed: {e}");
                if let Ok(true) = crate::platform::elevate("") {
                    std::process::exit(0);
                }
            }
        }
    });
}
"""


def patch_flutter_run_as_admin() -> None:
    """flutter（64 位标准端）也加「管理员权限运行」按钮，由 prebtn=Y 控制。

    - src/server/portable_service.rs: 与 32 位共用「一次性开关 + runas 拉起便携服务」
    - src/flutter_ffi.rs: 暴露 rdgen_is_elevated / rdgen_run_as_admin 给 dart
      （flutter_rust_bridge 按这个文件生成 flutter/lib/generated_bridge.dart）
    - desktop_home_page.dart: 复制按钮下面加按钮，未安装且未提权才显示
    """
    patch_ps_elevate_launch()

    p = "src/flutter_ffi.rs"
    if not os.path.exists(p):
        print("[info] 没有 src/flutter_ffi.rs，跳过 flutter 提权按钮")
        return
    s = read(p)
    if "rdgen_run_as_admin" in s:
        print("[skip] src/flutter_ffi.rs: 提权接口已存在")
    elif RG_FLUTTER_FFI_ANCHOR in s:
        s = s.replace(RG_FLUTTER_FFI_ANCHOR, RG_FLUTTER_FFI_ANCHOR + RG_FLUTTER_FFI_CODE, 1)
        write(p, s)
        print("[ok] src/flutter_ffi.rs: 新增 rdgen_is_elevated / rdgen_run_as_admin")
    else:
        print("[warn] src/flutter_ffi.rs: 锚点没找到，未加提权接口")

    p = "flutter/lib/desktop/pages/desktop_home_page.dart"
    if not os.path.exists(p):
        print("[info] 没有 desktop_home_page.dart，跳过 flutter 提权按钮")
        return
    s = read(p)
    if "buildRdgenRunAsAdminButton" in s:
        print("[skip] desktop_home_page.dart: 管理员按钮已存在")
        return
    call_anchor = ("      if (!isOutgoingOnly && bind.mainGetBuildinOption('copybtn') == 'Y')"
                   " buildRdgenCopyButton(),\n")
    method_anchor = "\n  buildPasswordBoard(BuildContext context) {"
    if call_anchor not in s or method_anchor not in s:
        print("[warn] desktop_home_page.dart: 锚点没找到（先跑 flutter-copy-credentials？）")
        return
    s = s.replace(call_anchor, call_anchor + "      buildRdgenRunAsAdminButton(),\n", 1)
    s = s.replace(method_anchor, "\n" + RG_FLUTTER_ADMIN_METHOD + "  buildPasswordBoard(BuildContext context) {", 1)
    write(p, s)
    print("[ok] desktop_home_page.dart: 已加入管理员权限运行按钮")


def patch_inline_sciter_encoding() -> None:
    """res/inline-sciter.py 默认用系统编码(cp1252)读 src/ui/*.tis。

    TISX 里一旦出现中文（例如「复制 ID 和密码」按钮），Windows runner 上就会
    UnicodeDecodeError 直接构建失败。这里让它的 open() 默认按 UTF-8 读写。
    """
    p = "res/inline-sciter.py"
    if not os.path.exists(p):
        print("[info] 没有 res/inline-sciter.py，跳过编码补丁")
        return
    s = read(p)
    if "_rdgen_open" in s:
        print("[skip] res/inline-sciter.py: UTF-8 读取补丁已存在")
        return
    anchor = "import re\n"
    wrapper = ("""import re
import builtins as _rdgen_builtins

_rdgen_open = _rdgen_builtins.open


def open(path, mode="r", *args, **kwargs):
    if "b" not in mode and "encoding" not in kwargs:
        kwargs["encoding"] = "utf-8"
    return _rdgen_open(path, mode, *args, **kwargs)
""")
    if anchor in s:
        s = s.replace(anchor, wrapper, 1)
        write(p, s)
        print("[ok] res/inline-sciter.py: 已改为按 UTF-8 读取 UI 源码")
    else:
        print("[warn] res/inline-sciter.py: 没找到 import re 锚点，未改动")



def patch_ps_elevate_launch() -> None:
    """便携服务（quick support 的采集/输入助手）默认用 run_background 静默拉起。

    这里让它在 pre-elevate-service=Y 且当前未提权时改用 runas(ShellExecuteW)，
    UAC 授权后助手进程以管理员身份运行，而界面进程保持原样（不重启）。
    """
    p = "src/server/portable_service.rs"
    if not os.path.exists(p):
        print("[info] 没有 src/server/portable_service.rs，跳过")
        return
    s = read(p)
    if "rdgen_launch_portable_service" in s:
        print("[skip] portable_service.rs: 提权拉起补丁已存在")
        return
    anchor_call = "                    match crate::platform::run_background("
    anchor_import = "use super::video_qos;\n"
    if anchor_call not in s or anchor_import not in s:
        print("[warn] portable_service.rs: 锚点没找到，未修改")
        return
    helper = ("\n"
              "// rdgen: 只有用户点过「管理员权限运行」按钮（一次性开关）才用 runas 拉起便携服务，\n"
              "// 平时保持上游的静默 run_background 行为，启动时不会弹 UAC。\n"
              "static RDGEN_ELEVATE_ONCE: AtomicBool = AtomicBool::new(false);\n"
              "\n"
              "pub fn set_rdgen_elevate_once(v: bool) {\n"
              "    RDGEN_ELEVATE_ONCE.store(v, Ordering::SeqCst);\n"
              "}\n"
              "\n"
              "fn rdgen_launch_portable_service(exe: &str, arg: &str) -> ResultType<bool> {\n"
              "    if RDGEN_ELEVATE_ONCE.swap(false, Ordering::SeqCst)\n"
              "        && !crate::platform::is_elevated(None).unwrap_or(false)\n"
              "    {\n"
              "        crate::platform::run_uac(exe, arg)\n"
              "    } else {\n"
              "        crate::platform::run_background(exe, arg)\n"
              "    }\n"
              "}\n")
    s = s.replace(anchor_import, anchor_import + helper, 1)
    s = s.replace(anchor_call, "                    match rdgen_launch_portable_service(", 1)
    write(p, s)
    print("[ok] portable_service.rs: 按需以 runas 拉起便携服务（仅按钮触发）")


def patch_sciter_permissions() -> None:
    """sciter(32 位) 构建也要遵守 access-mode。

    上游 is_permission_enabled_locally 里读 access-mode 的逻辑被 #[cfg(feature = "flutter")]
    挡掉了，所以自定义客户端在 sciter 版会退回 enable-* 的默认值，表现为「只有可查看权限」；
    flutter(64 位) 因为读到 access-mode=full 才是一切正常。
    """
    p = "src/server/connection.rs"
    if not os.path.exists(p):
        print("[info] 没有 src/server/connection.rs，跳过")
        return
    s = read(p)
    if "rdgen: sciter" in s:
        print("[skip] connection.rs: access-mode 补丁已存在")
        return
    old = ('    pub fn is_permission_enabled_locally(enable_prefix_option: &str) -> bool {\n'
           '        #[cfg(feature = "flutter")]\n'
           '        #[cfg(not(any(target_os = "android", target_os = "ios")))]\n'
           '        {\n')
    new = ('    pub fn is_permission_enabled_locally(enable_prefix_option: &str) -> bool {\n'
           '        // rdgen: sciter(32 位) 构建也遵守 access-mode，否则自定义客户端退回 enable-*\n'
           '        // 默认值，表现为「只有可查看权限」\n'
           '        #[cfg(not(any(target_os = "android", target_os = "ios")))]\n'
           '        {\n')
    if old not in s:
        print("[warn] connection.rs: 锚点没找到，未修改")
        return
    s = s.replace(old, new, 1)
    write(p, s)
    print("[ok] connection.rs: 32 位构建也按 access-mode 决定权限")

    patch_access_mode_expand()


def patch_access_mode_expand() -> None:
    """把 access-mode 展开成逐项权限开关。

    sciter 设置面板的勾选读的是 enable-* / allow-remote-config-modification 这些逐项
    option（get_option(id) != "N"），只看 access-mode 会导致「运行时有权限、界面全不勾」。
    这里在读完自定义客户端配置后，按 access-mode 把逐项开关写成 Y/N，界面与判定一致。
    """
    p = "src/common.rs"
    if not os.path.exists(p):
        print("[info] 没有 src/common.rs，跳过")
        return
    s = read(p)
    if "rdgen: access-mode" in s:
        print("[skip] src/common.rs: access-mode 展开补丁已存在")
        return
    anchor = """    for (k, v) in data {
        if let Some(v) = v.as_str() {
            config::HARD_SETTINGS
                .write()
                .unwrap()
                .insert(k, v.to_owned());
        };
    }
}"""
    if anchor not in s:
        print("[warn] src/common.rs: 锚点没找到，未展开 access-mode")
        return
    code = """    for (k, v) in data {
        if let Some(v) = v.as_str() {
            config::HARD_SETTINGS
                .write()
                .unwrap()
                .insert(k, v.to_owned());
        };
    }
    // rdgen: access-mode 展开到逐项开关（sciter 的界面勾选/远程修改配置都读这些键）
    let access_mode = {
        let d = config::DEFAULT_SETTINGS.read().unwrap();
        let o = config::OVERWRITE_SETTINGS.read().unwrap();
        o.get("access-mode")
            .or_else(|| d.get("access-mode"))
            .cloned()
            .unwrap_or_default()
    };
    if access_mode == "full" || access_mode == "view" {
        let v = if access_mode == "full" { "Y" } else { "N" }.to_owned();
        let perm_keys = [
            "enable-keyboard",
            "enable-clipboard",
            "enable-file-transfer",
            "enable-camera",
            "enable-terminal",
            "enable-remote-restart",
            "enable-tunnel",
            "enable-block-input",
            "enable-privacy-mode",
            "enable-lan-discovery",
            "enable-remote-printer",
            "enable-audio",
            "allow-remote-config-modification",
        ];
        let mut d = config::DEFAULT_SETTINGS.write().unwrap();
        let mut o = config::OVERWRITE_SETTINGS.write().unwrap();
        for k in perm_keys {
            d.insert(k.to_owned(), v.clone());
            o.insert(k.to_owned(), v.clone());
        }
    }
}"""
    s = s.replace(anchor, code, 1)
    write(p, s)
    print("[ok] src/common.rs: access-mode 已展开到逐项权限开关")


def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(2)
    action = sys.argv[1]
    if action == "xoffline":
        patch_peer_card()
    elif action == "brand":
        patch_brand()
    elif action == "copy-credentials":
        patch_copy_credentials()
    elif action == "flutter-copy-credentials":
        patch_flutter_copy_button()
        patch_flutter_run_as_admin()
    elif action == "run-as-admin":
        patch_ps_elevate_launch()
    elif action == "permissions":
        patch_sciter_permissions()
    elif action == "no-install-tip":
        patch_no_install_tip()
    elif action == "incoming-width":
        patch_incoming_width()
    elif action == "links":
        patch_links()
    elif action == "no-update-notif":
        patch_update_url()
        _strip_function_body(action)
    else:
        print("未知动作: %s" % action)
        sys.exit(2)

if __name__ == "__main__":
    main()

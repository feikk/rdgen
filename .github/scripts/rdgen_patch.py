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
    if cond_old in s:
        s = s.replace(cond_old, '{""}', 1)
        print("[ok] src/ui/index.tis: 已隐藏未安装提示横幅")
    else:
        print("[skip] src/ui/index.tis: 未安装提示横幅已隐藏或源码结构变了")

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

RG_COPY_MARKUP = ('                        {incoming_only ? <div style="padding: 16px 0 10px 0">'
                  '<button #rdgen-copy-credentials .button style="width:*; height:38px; font-size:1.05em">'
                  '&#22797;&#21046; ID &#21644;&#23494;&#30721;</button></div> : ""}\n')

RG_COPY_HANDLER = ('event click $(button#rdgen-copy-credentials) {\n'
                   '    var rg_id = get_id();\n'
                   '    var rg_pwd = password_cache && password_cache.length > 0 ? password_cache[0] : "";\n'
                   '    var rg_text = "ID: " + rg_id;\n'
                   '    if (rg_pwd && rg_pwd != "-") rg_text = rg_text + "\\n' + JS_PWD + '" + rg_pwd;\n'
                   '    handler.rdgen_copy_text(rg_text);\n'
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
                      '    }\n\n')
            if anchor in s:
                s = s.replace(anchor, anchor + method, 1)
                s = s.replace("        fn get_id();\n",
                              "        fn get_id();\n        fn rdgen_copy_text(String);\n", 1)
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
    s = s.replace(anchor_markup, anchor_markup + RG_COPY_MARKUP, 1)
    s = s.replace(anchor_handler, "\n" + RG_COPY_HANDLER + "function updatePasswordArea() {", 1)
    write(p, s)
    print("[ok] src/ui/index.tis: 已加入复制 ID 和密码按钮")


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

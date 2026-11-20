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
                dw, dh = _fit_box(size[0], size[1], 190, 80)
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
                    'padding:1px 0 3px 0; opacity:1;">')

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


RG_ONLYID_HELPERS = ('function rdgen_geek_on() { return handler.get_builtin_option("geek") == "Y" && incoming_only; }\n')


RG_ONLYID_READY = '    // rdgen: only id compact layout\n    if (rdgen_geek_on()) {\n        var rdgen_fit = function() {\n            try {\n                var t = $(div.left-pane > div > div.title); if (t) t.style#display = "none";\n                var mm = $(#myid svg#menu); if (mm) mm.style#display = "none";\n                var tip = $(div.left-pane > div > div.lighter-text); if (tip) tip.style#display = "none";\n                var im = $(div.left-pane > div.install-me); if (im) im.style#display = "none";\n                var tm = $(div.left-pane > div.trust-me); if (tm) tm.style#display = "none";\n                var content = $(div.left-pane > div);\n                if (content) content.style#padding-bottom = "8px";\n                var h = 0;\n                if (content && content.children) {\n                    for (var j = 0; j < content.children.length; ++j) {\n                        var cc = content.children[j];\n                        if (cc && cc.style#display != "none") h += cc.box(#height, #border);\n                    }\n                }\n                if (h < scaleIt(150)) h = scaleIt(150);\n                var total = h + scaleIt(96);\n                var r = handler.get_size();\n                if (r && r.length >= 4 && r[2] > 0 && r[3] > 0) {\n                    view.move(r[0], r[1], scaleIt(300), total);\n                } else {\n                    centerize(scaleIt(300), total);\n                }\n            } catch (e) { }\n        };\n        self.timer(150ms, rdgen_fit);\n        self.timer(700ms, rdgen_fit);\n    }\n'


def patch_only_id() -> None:
    """onlyid=Y：界面只留 ID（可选中复制）、各可选按钮和「知道了」，点了收进托盘。"""
    ensure_hidden_module()

    p = "src/ui.rs"
    if os.path.exists(p):
        s = read(p)
        if "rdgen_hide_to_tray" not in s:
            anchor = "    fn rdgen_run_as_admin(&self) {"
            method = ('    fn rdgen_hide_to_tray(&self) {\n'
                      '        #[cfg(target_os = "windows")]\n'
                      '        crate::rdgen_hidden::hide_to_tray();\n'
                      '    }\n\n')
            if anchor in s:
                s = s.replace(anchor, method + anchor, 1)
                decl = "        fn rdgen_run_as_admin();\n"
                if decl in s:
                    s = s.replace(decl, decl + "        fn rdgen_hide_to_tray();\n", 1)
                write(p, s)
                print("[ok] src/ui.rs: 新增 handler.rdgen_hide_to_tray")
            else:
                print("[warn] src/ui.rs: 没找到 rdgen_run_as_admin 锚点，托盘隐藏接口未加")
        s = read(p)
        if "rdgen: record hwnd" not in s:
            anchor2 = "    frame.set_title(&crate::get_app_name());\n"
            if anchor2 in s:
                s = s.replace(anchor2, anchor2
                              + "    // rdgen: record hwnd\n"
                              + "    #[cfg(windows)]\n"
                              + "    crate::rdgen_hidden::set_main_window_handle(frame.get_hwnd() as isize);\n", 1)
                write(p, s)
                print("[ok] src/ui.rs: 记录主窗口句柄")

    p = "src/ui/index.tis"
    if not os.path.exists(p):
        print("[info] 没有 src/ui/index.tis，跳过 only-id")
        return
    s = read(p)
    if "rdgen-onlyid-ok" in s:
        print("[skip] src/ui/index.tis: only-id 界面已存在")
        return

    if "function rdgen_geek_on()" not in s:
        ha = "const disable_change_id = handler.get_builtin_option(\"disable-change-id\") == \"Y\";\n"
        if ha in s:
            s = s.replace(ha, ha + RG_ONLYID_HELPERS, 1)
            print("[ok] src/ui/index.tis: 加入 only-id 参数判断函数")
        else:
            print("[warn] src/ui/index.tis: 没找到常量区锚点")

    min_anchor = "} else {\n  view.windowMinSize = (560, 300);\n}\n"
    if min_anchor in s:
        s = s.replace(min_anchor, min_anchor
                      + "if (rdgen_geek_on()) { view.windowMinSize = (300, 160); }\n", 1)
        print("[ok] src/ui/index.tis: only-id 最小窗口 300x160")
    else:
        print("[warn] src/ui/index.tis: 没找到 windowMinSize 锚点")

    # 右栏隐藏
    rp_anchor = "{!incoming_only && <div .right-pane>"
    if rp_anchor in s:
        s = s.replace(rp_anchor, "{!incoming_only && !rdgen_geek_on() && <div .right-pane>", 1)
        print("[ok] src/ui/index.tis: only-id 隐藏右栏")
    else:
        print("[warn] src/ui/index.tis: 没找到 right-pane 锚点")

    # 左栏占满宽度
    lp_anchor = 'style={incoming_only ? "width:*" : ""}'
    if lp_anchor in s:
        s = s.replace(lp_anchor, 'style={(incoming_only || rdgen_geek_on()) ? "width:*" : ""}', 1)
        print("[ok] src/ui/index.tis: only-id 左栏占满宽度")
    else:
        print("[warn] src/ui/index.tis: 没找到 left-pane 宽度锚点")

    # 左栏显示「就绪」状态栏（官方只在 incoming_only 时才有）
    cs_anchor = '{incoming_only ? <ConnectStatus @{this.connect_status} /> : ""}'
    if cs_anchor in s:
        s = s.replace(cs_anchor, '{(incoming_only || rdgen_geek_on()) ? <ConnectStatus @{this.connect_status} /> : ""}', 1)
        print("[ok] src/ui/index.tis: only-id 显示就绪状态栏")
    else:
        print("[warn] src/ui/index.tis: 没找到左栏 ConnectStatus 锚点")

    # 「知道了」按钮
    ok_anchor = '{(!is_win || handler.is_installed() || disable_installation) ? "" : <InstallMe />}\n'
    if ok_anchor not in s:
        ok_anchor = ('{(!is_win || handler.is_installed() || disable_installation'
                     ' || handler.get_builtin_option("hide-help-cards") == "Y") ? "" : <InstallMe />}\n')
    ok_block = ("                        {rdgen_geek_on() ? <div id=\"rdgen-onlyid-head\" "
                "style=\"padding:0 0 6px 0\">"
                "<button #rdgen-onlyid-ok .button .outline "
                "style=\"width:*; height:38px; font-size:1em\">"
                "&#30693;&#36947;&#20102;</button></div> : \"\"}\n")
    if ok_anchor in s:
        s = s.replace(ok_anchor, ok_block + ok_anchor, 1)
        print("[ok] src/ui/index.tis: 已加入「知道了」按钮")
    else:
        print("[warn] src/ui/index.tis: 没找到 InstallMe 锚点")

    app_anchor = "    event click $(button#connect) {\n"
    ok_handler = ("    event click $(button#rdgen-onlyid-ok) {\n"
                  "        handler.rdgen_hide_to_tray();\n"
                  "    }\n\n")
    if app_anchor in s:
        s = s.replace(app_anchor, ok_handler + app_anchor, 1)
        print("[ok] src/ui/index.tis: 「知道了」点击事件已加入")
    else:
        print("[warn] src/ui/index.tis: 没找到 App 事件锚点")

    up = "        if (update && passwordArea) passwordArea.update();\n"
    if up in s and "pwd_eye.update()" not in s:
        s = s.replace(up, up
                      + "        if (update && app && app.pwd_eye) app.pwd_eye.update();\n", 1)

    # 启动时：隐藏多余控件、按内容高度收缩窗口
    ready_anchor = "    if (!handler.get_remote_id()) {\n"
    if ready_anchor in s and "rdgen: only id compact" not in s:
        s = s.replace(ready_anchor, RG_ONLYID_READY + ready_anchor, 1)
        print("[ok] src/ui/index.tis: only-id 启动时整理界面")

    if "<!--" in s:
        print("[warn] 补丁结果里出现 HTML 注释，放弃写入")
        return
    write(p, s)


def patch_flutter_incoming_fit() -> None:
    """64 位 flutter：incoming-only 启动时总是按内容重算窗口尺寸。

    上游 `_updateWindowSize()` 里只有 `size != imcomingOnlyHomeSize` 时才 setSize，
    内容正好等于官方默认 280x300 时不会触发，窗口就沿用本地持久化的旧尺寸
    （之前生成的 331/360 宽版本留下的），于是左栏 280 右侧空一大块。
    这里保持官方的「按内容算尺寸」逻辑不变，只去掉那个相等判断（幂等）。
    """
    p = "flutter/lib/desktop/pages/desktop_home_page.dart"
    if not os.path.exists(p):
        print("[info] 没有 desktop_home_page.dart，跳过窗口自适应")
        return
    s = read(p)
    if "rdgen: fit window" in s:
        print("[skip] desktop_home_page.dart: 窗口尺寸自适应已存在")
        return
    old = ("      final size = renderObject.size;\n"
           "      if (size != imcomingOnlyHomeSize) {\n"
           "        imcomingOnlyHomeSize = size;\n"
           "        windowManager.setSize(getIncomingOnlyHomeSize());\n"
           "      }\n")
    new = ("      final size = renderObject.size;\n"
           "      imcomingOnlyHomeSize = size;\n"
           "      // rdgen: fit window（每次都按内容设尺寸，避免沿用旧版窗口尺寸）\n"
           "      windowManager.setSize(getIncomingOnlyHomeSize());\n")
    if old not in s:
        print("[warn] desktop_home_page.dart: 没找到 _updateWindowSize 原始代码，跳过")
        return
    s = s.replace(old, new, 1)
    write(p, s)
    print("[ok] desktop_home_page.dart: incoming-only 每次启动都按内容设窗口尺寸")


def patch_incoming_width() -> None:
    """仅受控（incoming-only）客户端的窗口宽度。

    - sciter(src/ui/index.tis): const incoming_only_width = 180 -> N（同时影响 windowMinSize 和输入框宽度）
    - flutter: 仅 INCOMING_ONLY_PATCH_FLUTTER=1 时才改尺寸（默认走官方样式）
    环境变量 INCOMING_ONLY_WIDTH，默认 300。
    """
    try:
        width = int((os.environ.get("INCOMING_ONLY_WIDTH") or "300").strip())
    except ValueError:
        width = 300

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

    # 2) flutter 默认尺寸：仅当显式要求时才改，64 位默认用官方样式
    p = "flutter/lib/common.dart"
    if os.path.exists(p) and os.environ.get("INCOMING_ONLY_PATCH_FLUTTER") == "1":
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

    # 3) flutter 首帧后按内容缩窗，补一个最小宽度钳制（64 位不启用，走官方默认）
    p = "flutter/lib/desktop/pages/desktop_home_page.dart"
    if os.path.exists(p) and os.environ.get("INCOMING_ONLY_PATCH_FLUTTER") == "1":
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
    """帮助卡片（粉色安装提示）上移 2.8em，padding 10px 15px，按钮间距保持官方原样。"""
    p = "src/ui/index.css"
    if not os.path.exists(p):
        print("[info] 没有 src/ui/index.css，跳过")
        return
    s = read(p)
    if "rdgen: help card lifted" in s:
        print("[skip] src/ui/index.css: 帮助卡片已上移")
        return

    old = "div.install-me, div.trust-me {\n    margin-top: 0.5em;\n    padding: 20px;"
    new_rule = ("div.install-me, div.trust-me {\n"
                "    /* rdgen: help card lifted */\n"
                "    margin-top: -2.6em;\n"
                "    padding: 10px 15px;")
    if old not in s:
        print("[warn] src/ui/index.css: 没找到 install-me 规则，未调整")
        return
    s = s.replace(old, new_rule, 1)
    write(p, s)
    print("[ok] src/ui/index.css: 帮助卡片上移 2.6em，padding 10px 15px")


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
        h = int((os.environ.get("INCOMING_ONLY_HEIGHT") or "500").strip())
    except ValueError:
        h = 500

    # 全部用整行字符串替换，别用正则：多/少一个括号就会让 .tis 变成语法错误（黑屏）
    # 1) 最小窗口高度（官方 is_installed() ? 300 : 390）
    old_min = ("view.windowMinSize = (incoming_only_width, (handler.is_installed() "
               "|| disable_installation) ? 300 : 390);")
    new_min = ("view.windowMinSize = (incoming_only_width, (handler.is_installed() "
               "|| disable_installation) ? %d : %d); // rdgen: window height" % (h, h))
    if old_min in s:
        s = s.replace(old_min, new_min, 1)
        print("[ok] src/ui/index.tis: incoming-only min/height -> %d" % h)
    elif "rdgen: window height" in s:
        pass
    else:
        print("[warn] src/ui/index.tis: 没找到 windowMinSize 原行，最小高度未改")

    # 2) 首次打开时的居中尺寸（官方 incoming_only ? 390 : 600）
    old_center = "scaleIt(incoming_only ? 390 : 600)"
    if old_center in s:
        s = s.replace(old_center, "scaleIt(incoming_only ? %d : 600)" % h, 1)
        print("[ok] src/ui/index.tis: incoming-only initial height -> %d" % h)

    # 2b) 启动后按实际内容高度再撑一次（最低 h），内容多时按钮/帮助卡不会被裁掉
    if "rdgen: auto height" not in s:
        rc_anchor = "    if (!handler.get_remote_id()) {\n"
        rc_fmt = "    // rdgen: auto height (incoming-only, min {H})\n    if (incoming_only) {\n        var rdgen_inc_fit = function() {\n            try {\n                var need = scaleIt({H});\n                var pane = $(div.app > div.left-pane);\n                var total = 0;\n                if (pane && pane.children) {\n                    for (var i = 0; i < pane.children.length; ++i) {\n                        var el = pane.children[i];\n                        if (el.style#position == 'absolute') continue;\n                        var hh = el.box(#height, #border);\n                        if (hh > 0) total += hh;\n                    }\n                }\n                if (total + scaleIt(52) > need) need = total + scaleIt(52);\n                var cs = $(div.connect-status);\n                if (cs) cs.style#display = 'block';\n                var w = scaleIt(incoming_only_width);\n                var r = handler.get_size();\n                if (r && r.length >= 4 && r[2] > 0 && r[3] > 0) {\n                    view.move(r[0], r[1], w, need);\n                } else {\n                    centerize(w, need);\n                }\n            } catch (e) { }\n        };\n        self.timer(120ms, rdgen_inc_fit);\n        self.timer(700ms, rdgen_inc_fit);\n    }\n"
        rc_code = rc_fmt.replace("{H}", str(h))
        if rc_anchor in s:
            s = s.replace(rc_anchor, rc_code + rc_anchor, 1)
            print("[ok] src/ui/index.tis: incoming-only 高度自适应（最低 %d）" % h)
        else:
            print("[warn] src/ui/index.tis: 没找到 ready 尾部锚点，高度自适应未加")

    # 3) 恢复上次保存的窗口尺寸时抬到 h（老用户本地存的是旧高度）
    if "rdgen: force min height" not in s:
        anchor = "    if (isReasonableSize(r) && r[2] > 0) {\n"
        if anchor in s:
            s = s.replace(anchor, anchor
                          + "        if (incoming_only && r[3] < %d) r[3] = %d; // rdgen: force min height\n" % (h, h), 1)
            print("[ok] src/ui/index.tis: clamp restored window height -> %d" % h)
        else:
            print("[warn] src/ui/index.tis: 没找到 self.ready 的尺寸判断，跳过高度钳制")

    if s != before:
        write(p, s)

JS_PWD = "\\u5bc6\\u7801:  "

RG_TIP_FUNC = ('var rdgen_tip_seq = 0;\n'
              'var rdgen_tip_countdown = 0;\n'
              'function rdgen_tip_hide() {\n'
              '    rdgen_tip_countdown = 0;\n'
              '    var el = $(#rdgen-tip);\n'
              '    if (el) el.style.set{ display: "none" };\n'
              '}\n'
              'function rdgen_tip(text) {\n'
              '    var el = $(#rdgen-tip);\n'
              '    if (!el) return;\n'
              '    rdgen_tip_seq = rdgen_tip_seq + 1;\n'
              '    var rg_seq = rdgen_tip_seq;\n'
              '    rdgen_tip_countdown = 3;\n'
              '    el.content(text);\n'
              '    el.style.set{ display: "inline-block" };\n'
              '    self.timer(2s, function() {\n'
              '        if (rg_seq != rdgen_tip_seq) return;\n'
              '        rdgen_tip_hide();\n'
              '    });\n'
              '}\n'
              'function rdgen_tip_tick() {\n'
              '    if (!rdgen_tip_countdown) return;\n'
              '    rdgen_tip_countdown = rdgen_tip_countdown - 1;\n'
              '    if (!rdgen_tip_countdown) rdgen_tip_hide();\n'
              '}\n\n')

RG_TIP_MARKUP = ('                <div style="position:absolute; left:0; right:0; bottom:56px; '
                 'text-align:center; z-index:999;">'
                 '<div #rdgen-tip style="display:none; max-width:90%; padding:9px 16px; '
                 'border-radius:6px; background:#2b2b2b; color:#ffffff; font-size:0.95em; '
                 'white-space:nowrap;"></div></div>\n')
RG_COPY_MARKUP = ('                        {!outgoing_only && handler.get_builtin_option("copybtn") == "Y" ? <div style="padding: 8px 0 6px 0">'
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
                  '<div style="padding: 0 0 6px 0"><button #rdgen-run-as-admin .button .outline '
                  'style="width:*; height:34px; font-size:1em">'
                  '&#31649;&#29702;&#21592;&#26435;&#38480;&#36816;&#34892;</button></div> : ""}\n')

RG_ADMIN_HANDLER = ('event click $(button#rdgen-run-as-admin) {\n'
                    '    handler.rdgen_run_as_admin();\n'
                    '    rdgen_tip("\\u8bf7\\u70b9\\u51fb\\u201c\\u662f\\u201d\\u6388\\u6743");\n'
                    '}\n\n')


RG_COPY_HANDLER = ('event click $(button#rdgen-copy-credentials) {\n'
                   '    var rg_id = get_id();\n'
                   '    var rg_pwd = password_cache && password_cache.length > 0 ? password_cache[0] : "";\n'
                   '    var rg_text = "ID: " + rg_id;\n'
                   '    if (rg_pwd && rg_pwd != "-") rg_text = rg_text + "\\n' + JS_PWD + '" + rg_pwd;\n'
                   '    handler.rdgen_copy_text(rg_text);\n'
                   '    rdgen_tip("\\u590d\\u5236\\u6210\\u529f");\n'
                   '}\n\n')


RG_INSTALL_MARKUP = ('                        {!outgoing_only && is_win && !handler.is_installed() && !disable_installation'
                    ' && handler.get_builtin_option("installbtn") == "Y" ? <div style="padding: 0 0 6px 0">'
                    '<button #rdgen-install .button .outline style="width:*; height:38px; font-size:1em">'
                    '&#23433;&#35013;&#21040;&#26412;&#26426;</button></div> : ""}\n')

RG_INSTALL_HANDLER = ('event click $(button#rdgen-install) {\n'
                      '    handler.goto_install();\n'
                      '}\n\n')


def patch_install_button() -> None:
    """installbtn=Y 时左栏多一个「安装到本机」按钮。

    样式与「复制 ID 和密码」一致（.button .outline、38px 高、* 宽），
    动作直接复用帮助提示卡片里那个安装按钮的 `handler.goto_install()`。
    只在 Windows、未安装（即便携运行）、未禁用安装时显示。
    """
    p = "src/ui/index.tis"
    if not os.path.exists(p):
        print("[info] 没有 src/ui/index.tis，跳过安装按钮")
        return
    s = read(p)
    if "rdgen-install" in s:
        print("[skip] src/ui/index.tis: 安装按钮已存在")
        return
    anchors = [RG_ADMIN_MARKUP,
               RG_COPY_MARKUP,
               "                        {!outgoing_only && <PasswordArea />}\n"]
    placed = False
    for a in anchors:
        if a in s:
            s = s.replace(a, a + RG_INSTALL_MARKUP, 1)
            placed = True
            break
    if not placed:
        print("[warn] src/ui/index.tis: 没找到插入点，安装按钮未加入")
        return
    if RG_ADMIN_HANDLER in s:
        s = s.replace(RG_ADMIN_HANDLER, RG_ADMIN_HANDLER + RG_INSTALL_HANDLER, 1)
    elif RG_COPY_HANDLER in s:
        s = s.replace(RG_COPY_HANDLER, RG_COPY_HANDLER + RG_INSTALL_HANDLER, 1)
    elif "\nfunction updatePasswordArea() {" in s:
        s = s.replace("\nfunction updatePasswordArea() {",
                      "\n" + RG_INSTALL_HANDLER + "function updatePasswordArea() {", 1)
    else:
        print("[warn] src/ui/index.tis: 没找到事件处理插入点，安装按钮未加入")
        return
    write(p, s)
    print("[ok] src/ui/index.tis: 已加入安装到本机按钮（installbtn=Y 时显示）")


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
                      '    fn rdgen_run_as_admin(&self) {\n'
                      '        use crate::portable_service::client;\n'
                      '        // 一次性开关：只在这次按钮触发的启动里以管理员身份拉起便携服务\n'
                      '        crate::portable_service::set_rdgen_elevate_once(true);\n'
                      '        client::set_quick_support(true);\n'
                      '        std::thread::spawn(|| {\n'
                      '            if client::running() {\n'
                      '                // 已有助手在跑，无法原地升级 -> 整进程提权重启（会弹 UAC）\n'
                      '                if let Ok(true) = crate::platform::elevate("") {\n'
                      '                    std::process::exit(0);\n'
                      '                }\n'
                      '                return;\n'
                      '            }\n'
                      '            match client::start_portable_service(client::StartPara::Direct) {\n'
                      '                Ok(()) => {}\n'
                      '                Err(e) => {\n'
                      '                    // 便携服务起不来（already running / 抓屏失败等）兜底：整进程提权重启\n'
                      '                    log::warn!("rdgen: start elevated portable service failed: {e}");\n'
                      '                    if let Ok(true) = crate::platform::elevate("") {\n'
                      '                        std::process::exit(0);\n'
                      '                    }\n'
                      '                }\n'
                      '            }\n'
                      '        });\n'
                      '    }\n\n')
            if anchor in s:
                s = s.replace(anchor, anchor + method, 1)
                s = s.replace("        fn get_id();\n",
                              "        fn get_id();\n"
                              "        fn rdgen_copy_text(String);\n"
                              "        fn rdgen_is_elevated();\n"
                              "        fn rdgen_run_as_admin();\n", 1)
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
    s = s.replace(anchor_markup, anchor_markup + RG_COPY_MARKUP + RG_ADMIN_MARKUP, 1)
    anchor_app = "            <div .app>\n"
    if anchor_app in s:
        s = s.replace(anchor_app, anchor_app + RG_TIP_MARKUP, 1)
    else:
        # 兜底：放回 PasswordArea 之后
        s = s.replace(anchor_markup, anchor_markup + RG_TIP_MARKUP, 1)
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
                  anchor_call + "      if (!isOutgoingOnly && bind.mainGetBuildinOption(key: 'copybtn') == 'Y') buildRdgenCopyButton(),\n", 1)
    s = s.replace(anchor_method, "\n" + RG_FLUTTER_METHOD + "  buildPasswordBoard(BuildContext context) {", 1)
    write(p, s)
    print("[ok] desktop_home_page.dart: 已加入复制 ID 和密码按钮")


RG_FLUTTER_ADMIN_METHOD = """
  Widget buildRdgenRunAsAdminButton() {
    if (bind.mainGetBuildinOption(key: 'prebtn') != 'Y') return SizedBox();
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

#[inline]
pub fn rdgen_is_elevated() -> SyncReturn<bool> {
    #[cfg(target_os = "windows")]
    {
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
    #[cfg(not(target_os = "windows"))]
    {
        SyncReturn(false)
    }
}

pub fn rdgen_is_hidden() -> SyncReturn<bool> {
    SyncReturn(crate::rdgen_hidden::is_hidden())
}

pub fn rdgen_set_hidden(hidden: bool) {
    #[cfg(target_os = "windows")]
    {
        crate::rdgen_hidden::set_hidden_from_ui(hidden);
    }
}

pub fn rdgen_start_hotkey_watch() {
    #[cfg(target_os = "windows")]
    {
        crate::rdgen_hidden::spawn_hotkey_watcher();
    }
}

pub fn rdgen_take_toggle_request() -> SyncReturn<bool> {
    #[cfg(target_os = "windows")]
    {
        SyncReturn(crate::rdgen_hidden::take_toggle_request())
    }
    #[cfg(not(target_os = "windows"))]
    {
        SyncReturn(false)
    }
}

pub fn rdgen_run_as_admin() {
    #[cfg(target_os = "windows")]
    {
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
}
"""


def ensure_hidden_module() -> None:
    """让 src/rdgen_hidden.rs 存在并在 lib.rs 里注册（bridge 生成任务也要能解析）。"""
    p = "src/rdgen_hidden.rs"
    if not os.path.exists(p):
        write(p, RG_HIDDEN_RS)
        print("[ok] src/rdgen_hidden.rs: 新增隐身模式模块")
    else:
        print("[skip] src/rdgen_hidden.rs: 已存在")
    p = "src/lib.rs"
    if os.path.exists(p):
        s = read(p)
        if "mod rdgen_hidden;" not in s:
            anchor = "mod tray;\n"
            if anchor in s:
                s = s.replace(anchor, anchor + "mod rdgen_hidden;\n", 1)
                write(p, s)
                print("[ok] src/lib.rs: 注册 mod rdgen_hidden")
            else:
                print("[warn] src/lib.rs: 没找到 mod tray 锚点")


def patch_flutter_ffi_only() -> None:
    """只给 src/flutter_ffi.rs 加提权接口，供 bridge 生成任务（Linux）使用。

    dart 侧的 bind.rdgenIsElevated / bind.rdgenRunAsAdmin 是由 flutter_rust_bridge
    按 flutter_ffi.rs 生成的，生成任务在 Ubuntu 上跑，所以必须在它 checkout 完源码、
    生成绑定之前把这两个函数补进去，Windows 构建任务再恢复同一份绑定。
    """
    p = "src/flutter_ffi.rs"
    if not os.path.exists(p):
        print("[info] 没有 src/flutter_ffi.rs，跳过")
        return
    s = read(p)
    if "rdgen_run_as_admin" in s:
        print("[skip] src/flutter_ffi.rs: 提权接口已存在")
        return
    if RG_FLUTTER_FFI_ANCHOR not in s:
        print("[warn] src/flutter_ffi.rs: 锚点没找到，未加提权接口")
        return
    s = s.replace(RG_FLUTTER_FFI_ANCHOR, RG_FLUTTER_FFI_ANCHOR + RG_FLUTTER_FFI_CODE, 1)
    write(p, s)
    print("[ok] src/flutter_ffi.rs: 新增 rdgen_is_elevated / rdgen_run_as_admin")
    ensure_hidden_module()


RG_FLUTTER_INSTALL_METHOD = """
  Widget buildRdgenInstallButton() {
    if (bind.mainGetBuildinOption(key: 'installbtn') != 'Y') return SizedBox();
    if (!isWindows) return SizedBox();
    if (bind.isDisableInstallation()) return SizedBox();
    if (bind.mainIsInstalled()) return SizedBox();
    return Padding(
      padding: const EdgeInsets.only(left: 20.0, right: 16, bottom: 10),
      child: SizedBox(
        width: double.infinity,
        height: 38,
        child: OutlinedButton(
          onPressed: () async {
            await rustDeskWinManager.closeAllSubWindows();
            bind.mainGotoInstall();
          },
          child: Text('\u5b89\u88c5\u5230\u672c\u673a', style: TextStyle(fontSize: 14)),
        ),
      ),
    );
  }

"""


def patch_flutter_install_button() -> None:
    """64 位 flutter：installbtn=Y 时左栏加「安装到本机」按钮。

    显示条件与 32 位一致：Windows + 便携运行（未安装）+ 未禁用安装。
    动作等同帮助卡片里的安装按钮：先关子窗口再 mainGotoInstall()。
    """
    p = "flutter/lib/desktop/pages/desktop_home_page.dart"
    if not os.path.exists(p):
        print("[info] 没有 desktop_home_page.dart，跳过 flutter 安装按钮")
        return
    s = read(p)
    if "buildRdgenInstallButton" in s:
        print("[skip] desktop_home_page.dart: 安装按钮已存在")
        return
    anchor_method = "\n  buildPasswordBoard(BuildContext context) {"
    if anchor_method not in s:
        print("[warn] desktop_home_page.dart: 没找到 buildPasswordBoard，安装按钮未插入")
        return
    s = s.replace(anchor_method, "\n" + RG_FLUTTER_INSTALL_METHOD + "  buildPasswordBoard(BuildContext context) {", 1)
    copy_call = "      if (!isOutgoingOnly && bind.mainGetBuildinOption(key: 'copybtn') == 'Y') buildRdgenCopyButton(),\n"
    pass_call = "      if (!isOutgoingOnly) buildPasswordBoard(context),\n"
    if copy_call in s:
        s = s.replace(copy_call, copy_call + "      if (!isOutgoingOnly) buildRdgenInstallButton(),\n", 1)
    elif pass_call in s:
        s = s.replace(pass_call, pass_call + "      if (!isOutgoingOnly) buildRdgenInstallButton(),\n", 1)
    else:
        print("[warn] desktop_home_page.dart: 没找到左栏 children 插入点，安装按钮未插入")
        return
    write(p, s)
    print("[ok] desktop_home_page.dart: 已加入安装到本机按钮（installbtn=Y 时显示）")


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
    call_anchor = ("      if (!isOutgoingOnly && bind.mainGetBuildinOption(key: 'copybtn') == 'Y')"
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
        // 只写 DEFAULT_SETTINGS：写进 OVERWRITE_SETTINGS 会被 is_option_fixed 判定为
        // 「强制锁定」，界面变灰且用户/远程都无法修改。默认生效方式下用户应可自行更改。
        let mut d = config::DEFAULT_SETTINGS.write().unwrap();
        for k in perm_keys {
            d.insert(k.to_owned(), v.clone());
        }
    }
}"""
    s = s.replace(anchor, code, 1)
    write(p, s)
    print("[ok] src/common.rs: access-mode 已展开到逐项权限开关（只写默认设置，用户可改）")


def patch_hide_gui() -> None:
    """hideconn=Y 时让 IPC 的 hide_cm 直接返回 true。

    上游本来就有「隐藏连接管理窗口」的机制：sciter 在 src/ui.rs 里用
    frame.collapse(true) + run_loop 建一个隐藏窗口，flutter 在 main.dart 里用
    windowManager.hide()；两边都靠 ipc 查询 "hide_cm" 决定。这里把这个查询的结果
    接到自定义客户端参数 hideconn 上，一处改动同时覆盖 32 位(sciter)与 64 位(flutter)。
    """
    p = "src/ipc.rs"
    if not os.path.exists(p):
        print("[info] 没有 src/ipc.rs，跳过")
        return
    s = read(p)
    if "rdgen: hideconn" in s:
        print("[skip] src/ipc.rs: hideconn 补丁已存在")
        return
    old = """                } else if name == "hide_cm" {
                    value = if crate::hbbs_http::sync::is_pro() || crate::common::is_custom_client()
                    {
                        Some(hbb_common::password_security::hide_cm().to_string())
                    } else {
                        None
                    };"""
    new = """                } else if name == "hide_cm" {
                    // rdgen: hideconn / 隐身模式下都隐藏连接管理窗口
                    let rdgen_hidden_now = hbb_common::config::LocalConfig::get_option("hidden-all") == "Y"
                        && (crate::ui_interface::get_builtin_option("hideallbtn") == "Y"
                            || crate::ui_interface::get_builtin_option("hideall") == "Y");
                    value = if crate::ui_interface::get_builtin_option("hideconn") == "Y"
                        || rdgen_hidden_now
                    {
                        Some("true".to_owned())
                    } else if crate::hbbs_http::sync::is_pro() || crate::common::is_custom_client()
                    {
                        Some(hbb_common::password_security::hide_cm().to_string())
                    } else {
                        None
                    };"""
    if old not in s:
        print("[warn] src/ipc.rs: hide_cm 锚点没找到，未接 hideconn")
        return
    s = s.replace(old, new, 1)
    write(p, s)
    print("[ok] src/ipc.rs: hideconn=Y 时隐藏连接窗口（sciter + flutter 共用）")


RG_HIDEALL_MARKUP = ('                        {!outgoing_only && handler.get_builtin_option("hideallbtn") == "Y" ? '
                     '<div style="padding: 0 0 6px 0"><button #rdgen-hide-all .button .outline '
                     'style="width:*; height:34px; font-size:1em">'
                     '&#38544;&#36523;&#27169;&#24335;</button></div> : ""}\n')

RG_HIDEALL_HANDLER = ('event click $(button#rdgen-hide-all) {\n'
                      '    handler.rdgen_hide_all();\n'
                      '}\n\n')

RG_HIDDEN_RS = """use hbb_common::{config::LocalConfig, log};
use std::sync::atomic::{AtomicBool, AtomicIsize, Ordering};

// rdgen: hideallbtn 的持久化标记（写在本地配置文件里，重启后依然生效；
// 直接把配置里的 hidden-all 改掉或删掉也能恢复正常模式）。
pub const KEY: &str = "hidden-all";

static MAIN_HWND: AtomicIsize = AtomicIsize::new(0);
static WATCHING: AtomicBool = AtomicBool::new(false);
// flutter（64 位）没有能直接 ShowWindow 的句柄，热键只置位，由 dart 侧轮询执行显隐
static TOGGLE_REQ: AtomicBool = AtomicBool::new(false);

/// 只有构建参数里带 hideallbtn=Y 或 hideall=Y 时，客户端才支持隐身
/// （hideallbtn 额外在界面上给一个按钮；hideall 只开功能、不出按钮）。
pub fn enabled() -> bool {
    let opt = |k: &str| crate::common::get_builtin_option(k);
    opt("hideallbtn") == "Y" || opt("hideall") == "Y"
}

pub fn is_hidden() -> bool {
    enabled() && LocalConfig::get_option(KEY) == "Y"
}

pub fn set_hidden(on: bool) {
    if on && !enabled() {
        return;
    }
    LocalConfig::set_option(KEY.to_owned(), if on { "Y" } else { "" }.to_owned());
}

pub fn set_main_window_handle(hwnd: isize) {
    MAIN_HWND.store(hwnd, Ordering::SeqCst);
}

#[cfg(windows)]
fn show_main_window(visible: bool) {
    let hwnd = MAIN_HWND.load(Ordering::SeqCst);
    if hwnd == 0 {
        return;
    }
    unsafe {
        winapi::um::winuser::ShowWindow(hwnd as _, if visible {
            winapi::um::winuser::SW_SHOW
        } else {
            winapi::um::winuser::SW_HIDE
        });
        if visible {
            winapi::um::winuser::SetForegroundWindow(hwnd as _);
        }
    }
}

#[cfg(not(windows))]
fn show_main_window(_visible: bool) {}

/// 启动时进入隐身模式用：窗口创建好、消息循环开始前直接把它藏起来。
pub fn hide_main_window_now() {
    show_main_window(false);
}

/// 「知道了」按钮：隐藏主窗口，只留托盘图标（托盘被禁用时退化为最小化）。
#[cfg(windows)]
pub fn hide_to_tray() {
    let hide_tray = crate::common::get_builtin_option("hide-tray") == "Y";
    if !hide_tray && !crate::check_process("--tray", true) {
        let _ = crate::run_me(vec!["--tray".to_owned()]);
    }
    let hwnd = MAIN_HWND.load(Ordering::SeqCst);
    if hwnd == 0 {
        return;
    }
    unsafe {
        winapi::um::winuser::ShowWindow(
            hwnd as _,
            if hide_tray {
                winapi::um::winuser::SW_MINIMIZE
            } else {
                winapi::um::winuser::SW_HIDE
            },
        );
    }
}

#[cfg(not(windows))]
pub fn hide_to_tray() {}

/// 结束托盘进程（托盘是独立的 `--tray` 进程，按命令行参数精确匹配，避免误杀自身）。
/// 32 位构建走 wmic（sysinfo 读不到 32 位进程的命令行），64 位走 sysinfo。
#[cfg(windows)]
fn kill_tray() {
    let exe = format!("{}.exe", crate::get_app_name());
    #[cfg(not(target_pointer_width = "64"))]
    let pids = match crate::platform::windows::get_pids_with_first_arg_check_session(
        &exe, "--tray", true,
    ) {
        Ok(pids) => pids,
        Err(e) => {
            log::warn!("rdgen: list tray pids failed: {e}");
            return;
        }
    };
    #[cfg(target_pointer_width = "64")]
    let pids = crate::platform::get_pids_of_process_with_first_arg(&exe, "--tray");
    if pids.is_empty() {
        return;
    }
    // CREATE_NO_WINDOW：taskkill 是控制台程序，不加这个参数会闪一下黑框
    use std::os::windows::process::CommandExt;
    let mut cmd = std::process::Command::new("taskkill");
    cmd.arg("/F");
    for p in pids.iter() {
        cmd.arg("/PID").arg(p.as_u32().to_string());
    }
    cmd.creation_flags(0x0800_0000);
    let _ = cmd.output();
}

#[cfg(not(windows))]
fn kill_tray() {}

/// 进入隐身模式：不重启进程（重启会丢掉一次性的管理员提权），
/// 只把主窗口隐藏、托盘进程结束，并启动快捷键监听。
pub fn enter_hidden_in_place() {
    set_hidden(true);
    kill_tray();
    show_main_window(false);
    spawn_hotkey_watcher();
    log::info!("rdgen: hidden mode on");
}

/// 退出隐身模式：清标记并把主窗口显示出来，权限状态原样保留。
pub fn exit_hidden_in_place() {
    set_hidden(false);
    show_main_window(true);
    restore_tray();
    log::info!("rdgen: hidden mode off");
}

/// 从隐身恢复时把托盘进程拉回来（进隐身时被 kill 掉了）。
#[cfg(windows)]
fn restore_tray() {
    if crate::check_process("--tray", true) {
        return;
    }
    if let Err(e) = crate::run_me(vec!["--tray".to_owned()]) {
        log::warn!("rdgen: restart tray failed: {e}");
    }
}

#[cfg(not(windows))]
fn restore_tray() {}

/// 命令行恢复：当前实例看不到别的进程的窗口句柄，所以清标记后重启自身进程；
/// 结束其它实例时按 PID 排除自己，避免把刚拉起的进程一起杀掉。
pub fn unhide_from_command() {
    set_hidden(false);
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        let pid = std::process::id();
        let exe = format!("{}.exe", crate::get_app_name());
        let mut cmd = std::process::Command::new("taskkill");
        cmd.args(["/F", "/FI", &format!("PID ne {}", pid), "/IM", &exe]);
        cmd.creation_flags(0x0800_0000); // CREATE_NO_WINDOW
        let _ = cmd.output();
    }
    let _ = crate::run_me(Vec::<String>::new());
}

/// 热键默认 Ctrl+Alt+6；构建参数 `hideall-key=ctrl+alt+5` 可改默认值，
/// 本地配置文件里的 `hidden-all-key` 仍优先，重启程序生效。
/// 只支持 ctrl / alt / shift / win + 单个字母、数字或 F1~F12。
#[cfg(windows)]
fn hotkey_combo() -> (u32, u32, String) {
    const DEFAULT_KEY: &str = "ctrl+alt+6";
    let raw = LocalConfig::get_option("hidden-all-key");
    let raw = raw.trim().to_owned();
    let cfg = if !raw.is_empty() {
        raw
    } else {
        // 本地配置没写时才用构建时写进 exe 的默认值（hideall-key）
        let builtin = crate::common::get_builtin_option("hideall-key");
        let builtin = builtin.trim().to_owned();
        if builtin.is_empty() { DEFAULT_KEY.to_owned() } else { builtin }
    };
    match parse_hotkey(&cfg) {
        Some((mods, vk)) => (mods, vk, cfg),
        None => {
            log::warn!("rdgen: bad hidden-all-key {:?}, fallback to {DEFAULT_KEY}", cfg);
            let (mods, vk) = parse_hotkey(DEFAULT_KEY).unwrap();
            (mods, vk, DEFAULT_KEY.to_owned())
        }
    }
}

#[cfg(windows)]
fn parse_hotkey(s: &str) -> Option<(u32, u32)> {
    use winapi::um::winuser::{MOD_ALT, MOD_CONTROL, MOD_NOREPEAT, MOD_SHIFT, MOD_WIN};
    let mut mods: u32 = 0;
    let mut vk: u32 = 0;
    for part in s.split('+') {
        let p = part.trim().to_ascii_lowercase();
        if p.is_empty() {
            continue;
        }
        match p.as_str() {
            "ctrl" | "control" => mods |= MOD_CONTROL as u32,
            "alt" => mods |= MOD_ALT as u32,
            "shift" => mods |= MOD_SHIFT as u32,
            "win" | "meta" | "super" => mods |= MOD_WIN as u32,
            _ => {
                if vk != 0 {
                    return None;
                }
                vk = match p.as_str() {
                    "f1" => 0x70,
                    "f2" => 0x71,
                    "f3" => 0x72,
                    "f4" => 0x73,
                    "f5" => 0x74,
                    "f6" => 0x75,
                    "f7" => 0x76,
                    "f8" => 0x77,
                    "f9" => 0x78,
                    "f10" => 0x79,
                    "f11" => 0x7a,
                    "f12" => 0x7b,
                    _ => {
                        let mut it = p.chars();
                        let c = it.next()?;
                        if it.next().is_some() {
                            return None;
                        }
                        if c.is_ascii_lowercase() {
                            c.to_ascii_uppercase() as u32
                        } else if c.is_ascii_digit() {
                            c as u32
                        } else {
                            return None;
                        }
                    }
                };
            }
        }
    }
    if vk == 0 || mods == 0 {
        None
    } else {
        Some((mods | MOD_NOREPEAT as u32, vk))
    }
}

/// 全局热键 Ctrl+Alt+6：按一下在「显示 / 隐身」之间切换。
/// 用 RegisterHotKey 而不是 GetAsyncKeyState 轮询——被远程控制时按键是别的进程
/// SendInput 注入的，GetAsyncKeyState 读不到；RegisterHotKey 走系统热键通道，
/// 注入的按键一样会触发（FSCapture 之类能用的原因也是这样）。窗口不可见不影响，
/// 热键消息只要求线程有自己的消息队列。
pub fn spawn_hotkey_watcher() {
    if !enabled() {
        return;
    }
    #[cfg(windows)]
    if !WATCHING.swap(true, Ordering::SeqCst) {
        std::thread::spawn(|| unsafe {
            use winapi::um::winuser::{
                DispatchMessageW, GetMessageW, RegisterHotKey, TranslateMessage, UnregisterHotKey,
                MSG, WM_HOTKEY,
            };
            const ID: i32 = 0x5244; // "RD"
            let (mods, vk, label) = hotkey_combo();
            if RegisterHotKey(std::ptr::null_mut(), ID, mods, vk) == 0
            {
                log::warn!("rdgen: register {label} failed (maybe taken)");
                WATCHING.store(false, Ordering::SeqCst);
                return;
            }
            log::info!("rdgen: hotkey {label} registered");
            loop {
                let mut msg: MSG = std::mem::zeroed();
                let ret = GetMessageW(&mut msg, std::ptr::null_mut(), 0, 0);
                if ret == 0 || ret == -1 {
                    break;
                }
                if msg.message == WM_HOTKEY && msg.wParam == ID as usize {
                    // flutter 侧窗口由 dart 的 windowManager 管理，这里只置位
                    #[cfg(feature = "flutter")]
                    {
                        log::info!("rdgen: hotkey hit (flutter, hand over to dart)");
                        TOGGLE_REQ.store(true, Ordering::SeqCst);
                    }
                    #[cfg(not(feature = "flutter"))]
                    {
                        if is_hidden() {
                            log::info!("rdgen: hotkey -> show");
                            exit_hidden_in_place();
                        } else {
                            log::info!("rdgen: hotkey -> hidden");
                            enter_hidden_in_place();
                        }
                    }
                }
                TranslateMessage(&msg);
                DispatchMessageW(&msg);
            }
            UnregisterHotKey(std::ptr::null_mut(), ID);
        });
    }
    #[cfg(not(windows))]
    {
        WATCHING.store(false, Ordering::SeqCst);
    }
}

/// 供 dart 轮询：热键是否要求切换显隐（64 位 flutter 端用）。
pub fn take_toggle_request() -> bool {
    TOGGLE_REQ.swap(false, Ordering::SeqCst)
}

/// 供 dart 调用：进入/退出隐身模式（只写配置 + 结束托盘，窗口由 dart 收起来）。
pub fn set_hidden_from_ui(on: bool) {
    if on {
        set_hidden(true);
        kill_tray();
    } else {
        set_hidden(false);
        restore_tray();
    }
}

fn _restart_self() {
    let _ = crate::run_me(Vec::<String>::new());
    std::process::exit(0);
}
"""


def patch_hide_all_btn() -> None:
    """hideallbtn=Y 时加「隐身模式」按钮：隐藏主窗口并结束托盘进程（不重启进程，
    所以管理员提权不丢），Ctrl+Alt+6 / --rdgen-unhide / 改本地配置都能恢复。"""
    p = "src/rdgen_hidden.rs"
    if not os.path.exists(p):
        write(p, RG_HIDDEN_RS)
        print("[ok] src/rdgen_hidden.rs: 新增隐身模式模块")
    else:
        print("[skip] src/rdgen_hidden.rs: 已存在")

    p = "src/lib.rs"
    if os.path.exists(p):
        s = read(p)
        if "mod rdgen_hidden;" not in s:
            anchor = "mod tray;\n"
            if anchor in s:
                s = s.replace(anchor, anchor + "mod rdgen_hidden;\n", 1)
                write(p, s)
                print("[ok] src/lib.rs: 注册 mod rdgen_hidden")
            else:
                print("[warn] src/lib.rs: 没找到 mod tray 锚点")

    p = "src/tray.rs"
    if os.path.exists(p):
        s = read(p)
        if "rdgen_hidden::is_hidden" not in s:
            anchor = "pub fn start_tray() {\n"
            if anchor in s:
                s = s.replace(anchor, anchor
                              + "    // rdgen: 隐身模式不创建托盘图标\n"
                              + "    if crate::rdgen_hidden::is_hidden() {\n        return;\n    }\n", 1)
                write(p, s)
                print("[ok] src/tray.rs: 隐身模式跳过托盘")
            else:
                print("[warn] src/tray.rs: 没找到 start_tray 锚点")
        else:
            print("[skip] src/tray.rs: 托盘补丁已存在")

    p = "src/core_main.rs"
    if os.path.exists(p):
        s = read(p)
        if "--rdgen-unhide" not in s:
            anchor = ('            } else if arg == "--no-server" {\n'
                      '                no_server = true;\n')
            if anchor in s:
                s = s.replace(anchor, anchor
                              + '            } else if arg == "--rdgen-unhide" {\n'
                              + "                // rdgen: 命令行退出隐身模式\n"
                              + "                crate::rdgen_hidden::unhide_from_command();\n"
                              + "                return None;\n", 1)
                write(p, s)
                print("[ok] src/core_main.rs: 支持 --rdgen-unhide")
            else:
                print("[warn] src/core_main.rs: 没找到参数锚点")
        else:
            print("[skip] src/core_main.rs: 命令行补丁已存在")

    p = "src/ui.rs"
    if os.path.exists(p):
        s = read(p)
        if "rdgen_hide_all(&self)" not in s:
            anchor = '    fn get_id(&self) -> String {\n        ipc::get_id()\n    }\n'
            method = ('    fn rdgen_hide_all(&self) {\n'
                      '        crate::rdgen_hidden::enter_hidden_in_place();\n'
                      '    }\n\n')
            if anchor in s:
                s = s.replace(anchor, method + anchor, 1)
            else:
                print("[warn] src/ui.rs: 没找到 get_id 锚点，handler 未加")
        if "fn rdgen_hide_all();" not in s:
            decl_anchor = None
            for decl in ("        fn rdgen_run_as_admin();\n",
                         "        fn rdgen_copy_text(String);\n",
                         "        fn get_id();\n"):
                if decl in s:
                    decl_anchor = decl
                    break
            if decl_anchor:
                s = s.replace(decl_anchor,
                              decl_anchor + "        fn rdgen_hide_all();\n", 1)
        write(p, s)
        print("[ok] src/ui.rs: 加入 rdgen_hide_all")

        s = read(p)
        if "rdgen: record hwnd" not in s:
            anchor = "    frame.set_title(&crate::get_app_name());\n"
            if anchor in s:
                s = s.replace(anchor, anchor
                              + "    // rdgen: record hwnd\n"
                              + "    #[cfg(windows)]\n"
                              + "    crate::rdgen_hidden::set_main_window_handle(frame.get_hwnd() as isize);\n", 1)
                write(p, s)
                print("[ok] src/ui.rs: 记录主窗口句柄")
            else:
                print("[warn] src/ui.rs: 没找到 set_title 锚点，句柄未记录")

        s = read(p)
        if "rdgen: hidden mode" not in s:
            anchor = "    frame.run_app();\n"
            if anchor in s:
                code = ('    // rdgen: hotkey watcher 常开（Ctrl+Alt+6 切换显示/隐身）\n'
                        '    crate::rdgen_hidden::spawn_hotkey_watcher();\n'
                        '    // rdgen: hidden mode\n'
                        '    if args.is_empty() && crate::rdgen_hidden::is_hidden() {\n'
                        '        frame.collapse(true);\n'
                        '        frame.run_loop();\n'
                        '        return;\n'
                        '    }\n')
                s = s.replace(anchor, code + anchor, 1)
                write(p, s)
                print("[ok] src/ui.rs: 隐身模式下不显示主窗口")
            else:
                print("[warn] src/ui.rs: 没找到 run_app 锚点")

    p = "src/ui/index.tis"
    if os.path.exists(p):
        s = read(p)
        if "rdgen-hide-all" in s:
            print("[skip] src/ui/index.tis: 隐身按钮已存在")
        else:
            anchor = None
            for cand in ('handler.get_builtin_option("prebtn") == "Y" ? ',
                         'handler.get_builtin_option("copybtn") == "Y" ? '):
                idx = s.find(cand)
                if idx != -1:
                    start = s.rfind("\n", 0, idx) + 1
                    end = s.find(': ""}\n', idx)
                    if end != -1:
                        anchor = s[start:end + len(': ""}\n')]
                        break
            if anchor is None:
                fallback = "                        {!outgoing_only && <PasswordArea />}\n"
                if fallback in s:
                    anchor = fallback
            if anchor:
                s = s.replace(anchor, anchor + RG_HIDEALL_MARKUP, 1)
                handler_anchor = "function updatePasswordArea() {"
                if handler_anchor in s:
                    s = s.replace("\n" + handler_anchor,
                                  "\n" + RG_HIDEALL_HANDLER + handler_anchor, 1)
                write(p, s)
                print("[ok] src/ui/index.tis: 已加入隐身模式按钮")
            else:
                print("[warn] src/ui/index.tis: 没找到按钮锚点")
    else:
        print("[info] 没有 src/ui/index.tis，跳过按钮")

    p = "src/ui/index.tis"
    if os.path.exists(p):
        data = open(p, "rb").read()
        if any(b > 127 for b in data):
            print("[warn] src/ui/index.tis: 出现非 ASCII 字节")

    patch_flutter_hide_all()


RG_FLUTTER_HIDEALL_METHOD = """
  Widget buildRdgenHideAllButton() {
    if (bind.mainGetBuildinOption(key: 'hideallbtn') != 'Y') return SizedBox();
    return Padding(
      padding: const EdgeInsets.only(left: 20.0, right: 16, bottom: 10),
      child: SizedBox(
        width: double.infinity,
        height: 38,
        child: OutlinedButton(
          onPressed: () async {
            await rdgenHideMainWindow();
          },
          child: Text('\\u9690\\u8eab\\u6a21\\u5f0f',
              style: TextStyle(fontSize: 14)),
        ),
      ),
    );
  }

"""

RG_FLUTTER_HIDEALL_MAIN = """
// rdgen: 隐身模式（hideallbtn=Y）——热键只置位，由这里收/放窗口
bool _rdgenHiddenPollingStarted = false;

Future<void> rdgenHideMainWindow() async {
  await bind.rdgenStartHotkeyWatch();
  await bind.rdgenSetHidden(hidden: true);
  rdgenStartHiddenPolling();
  await windowManager.hide();
}

void rdgenStartHiddenPolling() {
  if (_rdgenHiddenPollingStarted) return;
  _rdgenHiddenPollingStarted = true;
  Timer.periodic(const Duration(milliseconds: 400), (t) async {
    if (!bind.rdgenTakeToggleRequest()) return;
    if (bind.rdgenIsHidden()) {
      await bind.rdgenSetHidden(hidden: false);
      await windowManager.show();
      await windowManager.focus();
    } else {
      await bind.rdgenSetHidden(hidden: true);
      await windowManager.hide();
    }
  });
}
"""


def patch_flutter_hide_all() -> None:
    """flutter（64 位标准端）也支持 hideallbtn=Y。

    - src/flutter_ffi.rs: rdgenIsHidden / rdgenSetHidden / rdgenStartHotkeyWatch /
      rdgenTakeToggleRequest（与 32 位共用 src/rdgen_hidden.rs 的热键注册）
    - desktop_home_page.dart: 左栏加「隐身模式」按钮
    - main.dart: 启动即隐身时不显示窗口；热键切换由 400ms 轮询承接
    """
    p = "src/flutter_ffi.rs"
    if not os.path.exists(p):
        print("[info] 没有 src/flutter_ffi.rs，跳过 flutter 隐身模式")
        return
    s = read(p)
    if "rdgen_is_hidden" in s:
        print("[skip] src/flutter_ffi.rs: 隐身接口已存在")
    elif RG_FLUTTER_FFI_ANCHOR in s:
        s = s.replace(RG_FLUTTER_FFI_ANCHOR, RG_FLUTTER_FFI_ANCHOR + RG_FLUTTER_FFI_CODE, 1)
        write(p, s)
        print("[ok] src/flutter_ffi.rs: 新增 rdgen 隐身 / 热键接口")
    else:
        print("[warn] src/flutter_ffi.rs: 锚点没找到，未加隐身接口")

    p = "flutter/lib/desktop/pages/desktop_home_page.dart"
    if os.path.exists(p):
        s = read(p)
        if "buildRdgenHideAllButton" in s:
            print("[skip] desktop_home_page.dart: 隐身按钮已存在")
        else:
            anchor_call = "      buildRdgenRunAsAdminButton(),\n"
            if anchor_call in s:
                s = s.replace(anchor_call, anchor_call + "      buildRdgenHideAllButton(),\n", 1)
                method_anchor = "\n  buildPasswordBoard(BuildContext context) {"
                if method_anchor in s:
                    s = s.replace(method_anchor, "\n" + RG_FLUTTER_HIDEALL_METHOD
                                  + "  buildPasswordBoard(BuildContext context) {", 1)
                class_anchor = "class DesktopHomePage extends StatefulWidget {"
                if class_anchor in s:
                    s = s.replace(class_anchor, RG_FLUTTER_HIDEALL_MAIN + "\n" + class_anchor, 1)
                write(p, s)
                print("[ok] desktop_home_page.dart: 已加入隐身模式按钮")
            else:
                print("[warn] desktop_home_page.dart: 没找到管理员按钮锚点，隐身按钮未插入")
    else:
        print("[info] 没有 desktop_home_page.dart，跳过 flutter 隐身按钮")

    p = "flutter/lib/main.dart"
    if not os.path.exists(p):
        print("[info] 没有 main.dart，跳过 flutter 启动隐身")
        return
    s = read(p)
    if "rdgenStartHiddenPolling" in s:
        print("[skip] main.dart: 隐身模式补丁已存在")
    else:
        imp = "import 'package:flutter_hbb/desktop/pages/desktop_home_page.dart';\n"
        if imp not in s:
            imp_anchor = "import 'package:flutter_hbb/desktop/pages/desktop_tab_page.dart';\n"
            if imp_anchor in s:
                s = s.replace(imp_anchor, imp_anchor + imp, 1)
        ready_anchor = ("  windowManager.waitUntilReadyToShow(windowOptions, () async {\n"
                        "    // Restore the location of the main window before window hide or show.\n")
        if ready_anchor in s:
            boot = ("    // rdgen: 热键 Ctrl+Alt+6 常开（不需要 hideallbtn 参数，只是没有按钮）\n"
                    "    await bind.rdgenStartHotkeyWatch();\n"
                    "    rdgenStartHiddenPolling();\n")
            s = s.replace(ready_anchor, ready_anchor + boot, 1)
        else:
            print("[warn] main.dart: 没找到 waitUntilReadyToShow 锚点，热键未注册")
        show_anchor = ("    } else {\n"
                       "      windowManager.show();\n"
                       "      windowManager.focus();\n")
        if show_anchor not in s:
            write(p, s)
            print("[warn] main.dart: 没找到显示窗口分支，只加了工具函数")
        else:
            hide_branch = ("    } else if (bind.rdgenIsHidden()) {\n"
                           "      // rdgen: 启动即隐身\n"
                           "      await bind.rdgenStartHotkeyWatch();\n"
                           "      rdgenStartHiddenPolling();\n"
                           "      windowManager.setOpacity(1);\n"
                           "      windowManager.setTitle(getWindowName());\n"
                           "      windowManager.hide();\n")
            s = s.replace(show_anchor, hide_branch + show_anchor, 1)
            write(p, s)
            print("[ok] main.dart: 启动隐身 + 热键轮询")

    # 连接管理窗口（CM）：隐身模式下启动就隐藏，不信 ipc 的 hide_cm 查询结果
    s = read(p)
    if "rdgen: cm hide" in s:
        print("[skip] main.dart: CM 窗口隐身判断已存在")
    else:
        old_hide = ("  final hide = await bind.cmGetConfig(name: \"hide_cm\") == 'true';\n")
        new_hide = ("  // rdgen: cm hide（隐身模式下连接窗口也要藏起来）\n"
                    "  final hide = await bind.cmGetConfig(name: \"hide_cm\") == 'true' ||\n"
                    "      bind.rdgenIsHidden();\n")
        if old_hide in s:
            s = s.replace(old_hide, new_hide, 1)
            write(p, s)
            print("[ok] main.dart: 隐身模式下 CM 窗口启动即隐藏")
        else:
            print("[warn] main.dart: 没找到 cmGetConfig 锚点，CM 窗口未接隐身")

    p = "flutter/lib/models/server_model.dart"
    if not os.path.exists(p):
        print("[info] 没有 server_model.dart，跳过 CM 窗口隐身")
        return
    s = read(p)
    if "_rdgenHideCm" in s:
        print("[skip] server_model.dart: CM 窗口隐身补丁已存在")
        return
    old_field = "  bool hideCm = false;\n"
    new_field = ("  bool _rdgenHideCm = false;\n"
                 "  // rdgen: 隐身模式下 CM 窗口一定不显示\n"
                 "  bool get hideCm => _rdgenHideCm || bind.rdgenIsHidden();\n"
                 "  set hideCm(bool v) {\n"
                 "    _rdgenHideCm = v;\n"
                 "  }\n")
    if old_field not in s:
        print("[warn] server_model.dart: 没找到 hideCm 字段锚点")
        return
    s = s.replace(old_field, new_field, 1)
    old_timer = ("      if (desktopType == DesktopType.cm) {\n"
                 "        final res = await bind.cmCheckClientsLength(length: _clients.length);\n")
    new_timer = ("      if (desktopType == DesktopType.cm && bind.rdgenIsHidden()) {\n"
                 "        await hideCmWindow();\n"
                 "      }\n"
                 "      if (desktopType == DesktopType.cm) {\n"
                 "        final res = await bind.cmCheckClientsLength(length: _clients.length);\n")
    if old_timer in s:
        s = s.replace(old_timer, new_timer, 1)
    else:
        print("[warn] server_model.dart: 没找到 timer 锚点，运行中进入隐身不会自动收起 CM")
    write(p, s)
    print("[ok] server_model.dart: 隐身模式下 CM 窗口始终隐藏")


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
        patch_install_button()
        patch_flutter_install_button()
        patch_only_id()
    elif action == "flutter-ffi":
        patch_flutter_ffi_only()
    elif action == "flutter-copy-credentials":
        patch_flutter_copy_button()
        patch_flutter_run_as_admin()
        patch_flutter_install_button()
    elif action == "run-as-admin":
        patch_ps_elevate_launch()
    elif action == "hide-all":
        patch_hide_all_btn()
    elif action in ("hide-conn", "hide-gui"):
        patch_hide_gui()
    elif action == "permissions":
        patch_sciter_permissions()
    elif action == "no-install-tip":
        patch_no_install_tip()
    elif action == "incoming-width":
        patch_incoming_width()
        patch_flutter_incoming_fit()
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

#!/usr/bin/env python3
"""rdgen 源码补丁工具。

用语义匹配替代「按行号 git apply」和「按行区间 sed」，
避免 rustdesk 上游代码一变动就把源码改坏（例如把函数体切断导致 unexpected closing delimiter）。
用法:
    python rdgen_patch.py xoffline          # 离线状态显示红色 X 而不是橙圈
    python rdgen_patch.py no-update-notif   # 关闭新版本升级提示 / 客户端更新检查
    python rdgen_patch.py links             # 把源码里所有 rustdesk.com 链接换成自定义官网/下载链接
    python rdgen_patch.py brand             # sciter 客户端：去掉「由 xxx 提供支持」并在原位居中放自定义图标
两者都幂等，重复执行不会破坏文件。
"""
import os
import re
import sys

def read(path: str) -> str:
    with open(path, "r", encoding="utf-8", newline="") as f:
        return f.read()

def write(path: str, content: str) -> None:
    with open(path, "w", encoding="utf-8", newline="") as f:
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
    ]
    for path in targets:
        if not os.path.exists(path):
            continue
        s = read(path)
        before = s
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


LOGO_CANDIDATES = ["res/128x128.png", "res/64x64.png", "res/icon.png"]

def _logo_data_uri() -> str:
    """优先用 magick 生成的 64x64.png 转 data URI，避免把大图塞进 HTML。"""
    import base64
    for path in LOGO_CANDIDATES:
        if os.path.exists(path) and os.path.getsize(path) <= 512 * 1024:
            with open(path, "rb") as f:
                b = base64.b64encode(f.read()).decode()
            print("[ok] 使用 %s 作为界面 Logo（%d bytes）" % (path, os.path.getsize(path)))
            return "data:image/png;base64," + b
    print("[warn] 没找到可用的 icon png，界面只去掉 powered-by 文字，不插入 Logo")
    return ""

def patch_brand() -> None:
    """sciter 客户端：把「由 xxx 提供支持」换成居中的自定义图标。

    rustdesk 里这行是：{is_custom_client && handler.get_builtin_option("hide-powered-by-me") != "Y" ? <div .link #powered-by ...>{translate('powered_by_me')}</div> : ""}
    """
    path = "src/ui/index.tis"
    if not os.path.exists(path):
        print("[error] 找不到 %s" % path)
        sys.exit(1)
    s = read(path)
    if "rdgen: centered brand logo" in s:
        print("[skip] index.tis 已经处理过 powered-by / logo")
        return
    logo = _logo_data_uri()
    if logo:
        block = ('<div style="flow:horizontal; margin:0 *; padding:2px 0 8px 0;">'
                 '<img src="%s" style="width:72px; height:72px;" />'
                 '</div><!-- rdgen: centered brand logo -->' % logo)
    else:
        block = '<!-- rdgen: centered brand logo -->'

    pattern = re.compile(
        r'\{\s*is_custom_client\s*&&\s*handler\.get_builtin_option\("hide-powered-by-me"\)\s*!=\s*"Y"\s*'
        r'\?[\s\S]*?:\s*""\s*\}',
    )
    new_s, n = pattern.subn(block, s)
    if n == 0:
        print("[warn] 没匹配到 powered-by 那一行，源码可能已改版，跳过")
        return
    # 顺手去掉已经无主的 #powered-by 点击事件（元素已删除，事件留着只是死代码）
    handler = re.compile(r'event\s+click\s+\$\(#powered-by\)\s*\{[^}]*\}')
    new_s, nh = handler.subn('', new_s)
    if nh:
        print("[ok] 已移除 #powered-by 的点击事件（%d 处）" % nh)
    write(path, new_s)
    print("[ok] src/ui/index.tis: 已移除 powered-by%s" % ("，并在原位居中显示 Logo" if logo else ""))


def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(2)
    action = sys.argv[1]
    if action == "xoffline":
        patch_peer_card()
    elif action == "brand":
        patch_brand()
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

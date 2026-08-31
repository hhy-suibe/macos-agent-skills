#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
scan.py —— 磁盘清理报告的确定性扫描基座（只读）。

产出 data.json（供 build.py 注入模板）。它覆盖常见垃圾位置的机械扫描；
机器特有的深挖（应用内缓存分类、按月明细的判断文案等）由运行 skill 的
agent 在此基础上补充——本脚本保证底座数据真实、分类保守。

用法： python3 scan.py [输出路径，默认 ./data.json]
原则： 只读（du/find/stat/df/sw_vers）；路径不存在即跳过；拿不准就标 report。
"""
import json
import hashlib
import os
import re
import subprocess
import sys
import time

HOME = os.path.expanduser("~")
OUT = sys.argv[1] if len(sys.argv) > 1 else "data.json"
UID_HASH = None  # 自动发现微信目录用

def sh(cmd, timeout=120):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.stdout
    except Exception:
        return ""

def du_bytes(path):
    out = sh(["/usr/bin/du", "-skx", path])
    m = re.match(r"(\d+)", out)
    return int(m.group(1)) * 1024 if m else None

def du_d1(path, top=15):
    # 注意 BSD du 的 -s 与 -d 互斥，下钻时必须去掉 -s
    out = sh(["/usr/bin/du", "-kx", "-d", "1", path], timeout=300)
    rows = []
    for line in out.splitlines():
        m = re.match(r"(\d+)\t(.+)", line)
        if m:
            rows.append((int(m.group(1)) * 1024, m.group(2)))
    rows = [r for r in rows if r[1] != path]
    rows.sort(reverse=True)
    return rows[:top]

def mt(path):
    out = sh(["/usr/bin/stat", "-f", "%Sm", "-t", "%Y-%m-%d", path])
    return out.strip() or None

def cnt(path, cap=True):
    b = du_bytes(path)
    if cap and b is not None and b > 3 * 1024 ** 3:
        return -1
    out = sh(["/usr/bin/find", path, "-type", "f"], timeout=180)
    return len(out.splitlines()) if out else 0

def exists(p):
    return os.path.exists(p)

def stable_id(prefix, path, role=""):
    slug = re.sub(r"[^a-z0-9]+", "-", os.path.basename(path).lower()).strip("-") or "item"
    raw = os.path.realpath(path) + "\0" + role
    return "%s-%s-%s" % (prefix, slug[:20], hashlib.sha256(raw.encode("utf-8", "surrogateescape")).hexdigest()[:12])

# ───────────────────────── 目录清单（分类保守：拿不准标 report）
def catalog():
    c = [
        # (路径, 名称, 分类, 安全级, 说明, 动作)
        ("~/Library/Caches/ms-playwright", "Playwright 浏览器内核", "cache", "safe", "自动化测试下载的浏览器，删后自动重新下载", "trash"),
        ("~/Library/Caches/com.tencent.xinWeChat", "微信图片/视频缓存", "cache", "safe", "聊天媒体缓存，删了不丢聊天记录", "trash"),
        ("~/.npm", "npm 下载缓存", "cache", "safe", "npm/npx 下载缓存，删后重新联网下载", "trash"),
        ("~/Library/Caches/@zcodedesktop-updater", "ZCode 更新包缓存", "cache", "safe", "历史更新安装包", "trash"),
        ("~/Library/Caches/Google", "Google 更新缓存", "cache", "safe", "Chrome 更新下载缓存", "trash"),
        ("~/Library/Caches/Homebrew", "Homebrew 下载缓存", "cache", "safe", "brew 官方命令清理更佳", "brew"),
        ("~/Library/Caches/pip", "pip 缓存", "cache", "safe", "pip 下载缓存", "trash"),
        ("~/Library/Caches/uv", "uv 缓存", "cache", "safe", "uv 包管理缓存", "trash"),
        ("~/Library/Caches/Yarn", "Yarn 缓存", "cache", "safe", "Yarn 下载缓存", "trash"),
        ("~/Library/Caches/CocoaPods", "CocoaPods 缓存", "cache", "safe", "iOS 依赖缓存", "trash"),
        ("~/Library/Developer/Xcode/DerivedData", "Xcode DerivedData", "dev", "safe", "编译产物，删后重新编译", "trash"),
        ("~/Library/Developer/CoreSimulator", "iOS 模拟器数据", "dev", "caution", "模拟器，删后需重建", "trash"),
        ("~/Library/Developer/Xcode/iOS DeviceSupport", "Xcode 设备符号", "dev", "caution", "旧设备调试符号，重连时重建", "trash"),
        ("~/go/pkg/mod", "Go 模块缓存", "dev", "caution", "删后重新下载", "trash"),
        ("~/.gradle/caches", "Gradle 缓存", "dev", "safe", "构建缓存", "trash"),
        ("~/.cargo/registry", "Cargo 注册表", "dev", "caution", "删后重新下载", "trash"),
        ("~/.cache/huggingface", "HuggingFace 模型缓存", "dev", "caution", "模型权重，删后重下", "trash"),
        ("~/.cache", "~/.cache 总量", "dev", "report", "各类工具的 POSIX 缓存汇总", None),
        ("~/.npm-global/lib/node_modules", "npm 全局工具", "dev", "report", "npm -g 安装的 CLI 本体（不是缓存）", None),
        ("~/anaconda3/pkgs", "Anaconda 包缓存", "dev", "safe", "conda 安装包缓存", "trash"),
        ("~/miniconda3/pkgs", "Miniconda 包缓存", "dev", "safe", "conda 安装包缓存", "trash"),
        ("~/miniforge3/pkgs", "Miniforge 包缓存", "dev", "safe", "conda 安装包缓存", "trash"),
        ("~/Library/Logs", "应用运行日志汇总", "logs", "report", "日志根目录受 macOS 权限保护，只展示总量；请清理下方明确列出的日志子目录。", None),
        ("~/Library/Logs/DiagnosticReports", "用户级崩溃诊断日志", "logs", "safe", "用户级崩溃诊断文件，可删，不影响个人文档。", "trash"),
        ("~/.Trash", "废纸篓（清空=彻底删除）", "downloads", "caution", "等价于 Finder 清倒废纸篓，不可恢复", "trashcan"),
        ("/private/var/log", "系统日志", "logs", "report", "系统滚动管理，不建议手动动", None),
        ("/private/var/vm", "休眠镜像/交换文件", "report", "report", "系统按需管理，勿删", None),
        ("~/Library/Application Support/MobileSync/Backup", "iPhone/iPad 本机备份", "report", "report", "属于个人数据；只在访达的“管理备份”中核对设备与日期后处理。", None),
        ("~/Library/Application Support", "应用数据总目录（汇总）", "report", "report", "各应用配置与数据", None),
        ("~/Library/Containers", "App 沙盒容器总目录（汇总）", "report", "report", "沙盒应用全部数据", None),
        ("~/Library/Group Containers", "App 共享组数据（汇总）", "report", "report", "应用组共享数据", None),
        ("~/Library", "~/Library 总量（汇总）", "report", "report", "用户资源库全量", None),
    ]
    out = []
    for p, n, c, s, note, act in c:
        rp = os.path.expanduser(p)
        out.append((rp, n, c, s, note, act))
    return out

def make_node(nid, p, n, c, s, b, note, act=None, kids=None, agg=False, sp=None, mtv=None, cntv=None):
    if s == "report" and act:
        raise ValueError("report 条目不能带删除动作: %s" % nid)
    if s == "caution" and not act and not kids:
        raise ValueError("caution 条目必须带删除动作或子项: %s" % nid)
    node = {"id": nid, "p": p, "n": n, "c": c, "s": s, "b": b, "note": note, "mt": mtv, "cnt": cntv}
    if act == "trash":
        node["act"] = {"t": "trash"}
    elif act == "brew":
        node["act"] = {"t": "cmd", "c": "brew cleanup --prune=all"}
    elif act == "trashcan":
        node["act"] = {"t": "cmd", "c": "osascript -e 'tell application \"Finder\" to empty trash'"}
    if kids:
        node["kids"] = kids
    if agg:
        node["agg"] = True
    if sp:
        node["sp"] = sp
    return node

def drill(path, prefix, top=10, maxdepth_bytes=100 * 1024 ** 2):
    """对大目录做一层通用下钻；用途不明时只展示，不提供删除。"""
    kids = []
    for b, sub in du_d1(path, top):
        if b < maxdepth_bytes:
            continue
        kids.append(make_node(stable_id(prefix, sub, "drill"),
                              sub, os.path.basename(sub), "report", "report", b,
                              "自动发现的大目录；用途未确认，默认不提供删除按钮。", act=None,
                              mtv=mt(sub), cntv=cnt(sub)))
    return kids

def main():
    items = []
    # 1) $TMPDIR 与系统临时区
    tmpdir = os.environ.get("TMPDIR", "")
    if tmpdir and exists(tmpdir):
        items.append(make_node("t-tmpdir", tmpdir.rstrip("/"), "当前用户临时目录 $TMPDIR", "temp", "report",
                               du_bytes(tmpdir) or 0,
                               "包含正在使用或受 macOS 保护的临时文件；不再提供整目录/批量命令删除。重启通常会自动清理可回收部分。",
                               mtv=mt(tmpdir), cntv=cnt(tmpdir)))
    if exists("/private/tmp"):
        for b, sub in du_d1("/private/tmp", 10):
            if b < 10 * 1024 ** 2:
                continue
            items.append(make_node(stable_id("t", sub, "private-tmp"), sub,
                                   os.path.basename(sub) + " 临时文件", "temp", "caution", b,
                                   "来源应用未完全确认；确认对应程序已经退出且不需要恢复任务后再删。",
                                   act="trash", mtv=mt(sub), cntv=cnt(sub)))
        items.append(make_node("t-privtmp", "/private/tmp", "/private/tmp 汇总", "temp", "report",
                               du_bytes("/private/tmp") or 0, "系统临时区，重启自动清空。",
                               agg=True, mtv=mt("/private/tmp")))

    # 2) 目录清单
    for i, (rp, n, c, s, note, act) in enumerate(catalog()):
        if not exists(rp):
            continue
        b = du_bytes(rp)
        agg = "汇总" in n
        items.append(make_node("c-%02d" % i, rp, n, c, s, b or 0, note, act=(None if agg else act),
                               agg=agg, mtv=mt(rp), cntv=cnt(rp) if not agg else -1))

    # 3) 大头深挖：App Support / Containers / Group Containers 中 >500MB 的条目
    for base, prefix in [("~/Library/Application Support", "a"), ("~/Library/Containers", "k"), ("~/Library/Group Containers", "g")]:
        bp = os.path.expanduser(base)
        if not exists(bp):
            continue
        for b, sub in du_d1(bp, 12):
            if b < 500 * 1024 ** 2:
                continue
            name = os.path.basename(sub)
            sp = "wechat" if "xinWeChat" in name else ("docker" if "docker.docker" in name else None)
            note = "应用数据大目录；内部用途尚未确认，默认只展示、不整目录删除。"
            if sp == "wechat":
                note = "微信数据。分层：缓存可删/媒体谨慎/聊天数据库绝对不能删。"
            if sp == "docker":
                note = "Docker 虚拟磁盘，直接删会损坏 Docker，必须应用内清理。"
            node = make_node(stable_id(prefix, sub, "app-root"), sub,
                             name, "report", "report", b, note,
                             kids=drill(sub, prefix + "x") if not sp else None, sp=sp, mtv=mt(sub), cntv=cnt(sub))
            items.append(node)

    # 4) 微信按月明细（若存在新版微信结构）
    wxg = os.path.expanduser("~/Library/Containers/com.tencent.xinWeChat/Data/Documents/xwechat_files")
    if exists(wxg):
        wechat_item = next((it for it in items if it.get("sp") == "wechat"), None)
        if wechat_item is None:
            wx_container = os.path.expanduser("~/Library/Containers/com.tencent.xinWeChat")
            wechat_item = make_node(stable_id("k", wx_container, "wechat-root"), wx_container,
                                    "com.tencent.xinWeChat", "report", "report", du_bytes(wx_container) or 0,
                                    "微信目录同时包含聊天数据库、媒体与缓存，不能整目录删除；请展开明细处理。",
                                    sp="wechat", mtv=mt(wx_container), cntv=cnt(wx_container))
            items.append(wechat_item)
        for acc in sorted(os.listdir(wxg)):
            if not acc.startswith("wxid_"):
                continue
            for sub, prefix, unit in [("msg/file", "m-f", "该月聊天收发的文件"), ("msg/video", "m-v", "该月聊天视频")]:
                base = os.path.join(wxg, acc, sub)
                if not exists(base):
                    continue
                # 挂到对应微信大头条目；按账号/类型分组，保留递归月份结构。
                wechat_item.setdefault("kids", [])
                month_kids = []
                for b, mdir in du_d1(base, 1000):
                    if b <= 0:
                        continue
                    month_kids.append(make_node(stable_id(prefix, mdir, acc + ":" + sub), mdir,
                                                os.path.basename(mdir), "downloads", "caution", b,
                                                unit + "；删除后本机可能无法再次打开，且不保证能够重新下载。",
                                                act="trash", mtv=mt(mdir), cntv=cnt(mdir)))
                if month_kids:
                    group_id = stable_id(prefix + "g", base, acc + ":" + sub)
                    group_name = "聊天文件（按月份）" if sub == "msg/file" else "聊天视频（按月份）"
                    wechat_item["kids"].append(make_node(group_id, base, group_name, "report", "report",
                                                         sum(k.get("b") or 0 for k in month_kids),
                                                         "这是用户聊天媒体汇总；只能展开后逐月确认，不能整目录删除。",
                                                         kids=month_kids, mtv=mt(base), cntv=cnt(base)))

    # 5) 下载文件夹概览
    dl = os.path.expanduser("~/Downloads")
    if exists(dl):
        kids = []
        safe_names = {".DS_Store", "__pycache__"}
        installer_exts = (".dmg", ".pkg", ".xpi")
        for pos, entry in enumerate(sorted(os.scandir(dl), key=lambda e: e.name.casefold())):
            sub, name = entry.path, entry.name
            b = du_bytes(sub) or 0
            nid = stable_id("dl", sub, "download")
            if name in safe_names:
                safety, note, action = "safe", "可重新生成的缓存或系统索引文件。", "trash"
            elif name.lower().endswith(installer_exts):
                safety, note, action = "caution", "安装包或扩展包；确认已安装、已有可靠来源或不再需要离线备份后再删。", "trash"
            else:
                safety, action = "report", None
                note = "下载目录中的个人文件、数据、源码或归档；默认不视为垃圾，请先打开确认。"
            kids.append(make_node(nid, sub, name, "downloads", safety, b, note, act=action,
                                  mtv=mt(sub), cntv=cnt(sub)))
        items.append(make_node("w-dl", dl, "下载文件夹", "downloads", "report", du_bytes(dl) or 0,
                               "已逐条列出直接内容；个人文件默认不提供删除按钮。", kids=kids, agg=True, mtv=mt(dl), cntv=cnt(dl)))

    meta = {
        "scanDate": time.strftime("%Y-%m-%d"),
        "os": (sh(["/usr/bin/sw_vers", "-productVersion"]).strip() or "macOS"),
        "disk": disk_info(),
        "notes": ["由 scan.py 生成保守扫描底座；用途无法确认的内容只展示，不提供删除动作。",
                  "扫描全程只读，未移动或删除任何文件。"],
    }
    json.dump({"meta": meta, "items": items}, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("✅ 扫描完成：%d 个条目 → %s（含按月/深挖子项）" % (count_all(items), OUT))

def count_all(items):
    n = 0
    def w(xs):
        nonlocal n
        for x in xs:
            n += 1
            w(x.get("kids") or [])
    w(items)
    return n

def disk_info():
    out = sh(["/bin/df", "-h", "/System/Volumes/Data"])
    m = re.search(r"\S+\s+(\S+)\s+(\S+)\s+(\S+)", out.splitlines()[-1] if out else "")
    return {"total": m.group(1) if m else "?", "used": m.group(2) if m else "?", "free": m.group(3) if m else "?"}

if __name__ == "__main__":
    main()

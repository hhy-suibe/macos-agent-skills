#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
build.py —— 把扫描数据 JSON 注入报告模板，产出可直接使用的三件套。

用法：
  python3 build.py <data.json> <模板html> <输出目录>
    输出：<目录>/电脑垃圾文件清理报告.html
          <目录>/清理报告服务器.py（复制自脚本同目录 server.py）
          <目录>/启动清理报告.command

校验（任何一步失败即退出非 0，不产出半成品）：
  1) data.json 可解析、id 全局唯一、act 类型合法
  2) 路径保险栓：trash 动作不得指向 /、/Users、家目录等过浅路径
  3) 逐条校验路径存在性：已不存在的条目自动剔除并记录到 meta.notes
     （sp 特殊条目与 link:false 条目除外）
  4) 注入后重新抽取 DATA 做 json.loads 闭环验证
  5) 抽取 <script> 做 node --check 语法验证（有 node 时）
"""
import json
import os
import re
import shutil
import subprocess
import sys


def die(msg):
    print("❌ " + msg)
    sys.exit(1)


def collect(nodes, out, parent=None):
    for n in nodes:
        out.append((n, parent))
        for k in n.get("kids") or []:
            collect([k], out, n)


# ── 系统数据/元数据保护清单（与 server.py 的 PROTECTED 保持同步）──
# trash 动作命中即拒绝构建；删除这些会丢失系统数据或登录态/数据库等元数据。
PROTECTED = [
    (r"^/System/", "系统文件"),
    (r"^/usr/", "系统文件"),
    (r"^/Library/", "系统级资源库"),
    (r"^/private/var/db", "系统数据库"),
    (r"^/private/var/vm", "休眠镜像/虚拟内存"),
    (r"^/private/var/folders/", "系统每用户缓存(TMPDIR 除外)"),
    (r"/Keychains/", "钥匙串"),
    (r"/Preferences/", "应用偏好设置"),
    (r"/CloudStorage/", "iCloud 云盘"),
    (r"/MobileSync/", "iPhone 备份"),
    (r"/Group Containers/", "应用共享数据容器"),
    (r"/HTTPStorages/com\.apple\.", "苹果系统服务登录态"),
    (r"/WebKit/com\.apple\.", "苹果系统 WebKit 数据"),
    (r"\.(db|sqlite|sqlitedb)$", "数据库文件"),
    (r"/com\.goodnotesapp\.x/Data/Documents/", "GoodNotes 笔记"),
    (r"xinWeChat.*db_storage", "微信聊天数据库目录"),
    (r"com\.goodnotesapp\.x/Data/Library/Databases", "GoodNotes 笔记数据库目录"),
    (r"Google/Chrome/(Default|Profile\d*)$", "浏览器 Profile"),
    (r"/\.ssh/|/\.gnupg/|/\.aws/", "凭据"),
]
# 明确豁免：系统 TMPDIR（/private/var/folders/<a>/<b>/T）与用户应用日志目录。
# 必须锚定完整前缀，不能用子串匹配，否则 /Keychains/T/x 这类路径会被豁免打穿保护清单。
PROTECTED_ALLOWED = [r"^/private/var/folders/[^/]+/[^/]+/T(/|$)", r"^/Users/[^/]+/Library/Logs(/|$)"]


def protected_reason(path):
    if not path or path.startswith("tmutil"):
        return None
    import re as _re
    if any(_re.search(a, path) for a in PROTECTED_ALLOWED):
        return None
    for pat, why in PROTECTED:
        if _re.search(pat, path):
            return why
    return None


def main():
    if len(sys.argv) < 4:
        die("用法: build.py <data.json> <template.html> <outdir>")
    data_path, tpl_path, outdir = sys.argv[1], sys.argv[2], sys.argv[3]
    data = json.load(open(data_path, encoding="utf-8"))
    tpl = open(tpl_path, encoding="utf-8").read()

    flat = []
    collect(data.get("items") or [], flat)

    # 1) id 唯一性 + 字符集（id 会进 HTML 属性与内联 onclick，禁特殊字符）
    ids = [n["id"] for n, _ in flat]
    dup = {i for i in ids if ids.count(i) > 1}
    if dup:
        die("重复 id: %s" % dup)
    bad_ids = [i for i in ids if not re.fullmatch(r"[A-Za-z0-9._-]+", i)]
    if bad_ids:
        die("id 含非法字符（只允许字母数字._-）: %s" % bad_ids[:5])

    # 2) act 与路径保险栓
    home = os.path.expanduser("~")
    shallow = {"/", "/Users", "/private", "/private/tmp", "/opt", home}
    for n, _ in flat:
        act = n.get("act")
        if not act:
            continue
        if act.get("t") not in ("trash", "cmd"):
            die("非法动作类型 %s (id=%s)" % (act.get("t"), n["id"]))
        if act["t"] == "trash":
            ps = act.get("paths") or n.get("dp") or [n.get("p")]
            for p in ps:
                if not p or p in shallow or len(str(p).strip("/").split("/")) < 3:
                    die("trash 动作路径过浅，拒绝构建: %s (id=%s)" % (p, n["id"]))
                why = protected_reason(p)
                if why:
                    die("trash 动作指向受保护的%s，拒绝构建: %s (id=%s)\n  该类内容只允许 report 级展示，不给删除按钮" % (why, p, n["id"]))

    # 3) 存在性校验：剔除已消失条目（含 trash 动作的多目标 paths/dp）
    stale = []
    stale_ids = set()
    for n, _ in flat:
        p = n.get("p")
        if n.get("sp") or n.get("link") is False or not p or p.startswith("tmutil"):
            continue
        act = n.get("act") or {}
        targets = act.get("paths") or n.get("dp") or ([p] if p else [])
        if act.get("t") == "cmd" or not targets:
            if p and not os.path.exists(p):
                stale.append("%s（%s，可能已被清理）" % (n.get("n"), n["id"]))
                stale_ids.add(n["id"])
            continue
        alive = [t for t in targets if t and os.path.exists(t)]
        if not alive:
            stale.append("%s（%s，目标已全部不存在）" % (n.get("n"), n["id"]))
            stale_ids.add(n["id"])
        elif len(alive) < len(targets):
            # 部分目标已删：只保留还存在的目标，并同步缩减统计大小
            if act.get("paths"):
                act["paths"] = alive
            if n.get("dp"):
                n["dp"] = alive
            n["note"] = (n.get("note", "") + "（部分目标此前已被清理，本条目已自动收缩范围。）").strip()

    def prune(nodes):
        out = []
        for n in nodes:
            if n["id"] in stale_ids:
                continue
            if n.get("kids"):
                n["kids"] = prune(n["kids"])
            out.append(n)
        return out

    data["items"] = prune(data["items"])
    if stale:
        notes = data.setdefault("meta", {}).setdefault("notes", [])
        notes.insert(0, "本次构建校验时以下 %d 个条目已不存在，自动从报告剔除：%s" % (
            len(stale), "；".join(stale[:8]) + ("…" if len(stale) > 8 else "")))

    # 4) 注入
    scan_date = data.get("meta", {}).get("scanDate", "未知日期")
    meta_line = "%s · %s" % (data.get("meta", {}).get("os", "macOS"),
                             data.get("meta", {}).get("disk", {}).get("used", "") + " 已用 / " + data.get("meta", {}).get("disk", {}).get("total", ""))
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")  # 防 </script> 提前终止
    for token in ("__SCAN_DATA__", "__SCAN_DATE__", "__SCAN_META__", "__REPORT_TOKEN__"):
        if token not in tpl:
            die("模板缺少占位符 " + token)
    html = tpl.replace("__SCAN_DATA__", payload).replace("__SCAN_DATE__", scan_date).replace("__SCAN_META__", meta_line)

    # 闭环验证：从产物里再抽出来 parse 一遍
    m = re.search(r"/\*DATA-START\*/\s*var DATA = (\{.*?\});\s*/\*DATA-END\*/", html, re.S)
    if not m:
        die("产物中找不到 DATA 块")
    json.loads(m.group(1))

    # 5) node --check
    m2 = re.search(r"<script>\n(\"use strict\";[\s\S]*?)\n</script>", html)
    if not m2:
        die("产物中找不到主 script")
    js_path = os.path.join(outdir, ".report_check.js")
    open(js_path, "w", encoding="utf-8").write(m2.group(1))
    try:
        if shutil.which("node"):
            r = subprocess.run(["node", "--check", js_path], capture_output=True, text=True)
            if r.returncode != 0:
                die("JS 语法错误:\n" + r.stderr[:800])
    finally:
        if os.path.exists(js_path):
            os.remove(js_path)

    # 写产物
    os.makedirs(outdir, exist_ok=True)
    out_html = os.path.join(outdir, "电脑垃圾文件清理报告.html")
    open(out_html, "w", encoding="utf-8").write(html)

    # 复制服务器与启动器模板（与 build.py 同目录的 server.py / launcher 模板）
    here = os.path.dirname(os.path.abspath(__file__))
    srv_src = os.path.join(here, "server.py")
    if os.path.exists(srv_src):
        shutil.copy(srv_src, os.path.join(outdir, "清理报告服务器.py"))
        os.chmod(os.path.join(outdir, "清理报告服务器.py"), 0o755)
    launcher = os.path.join(outdir, "启动清理报告.command")
    open(launcher, "w", encoding="utf-8").write(
        '#!/bin/bash\n# 双击我：启动清理报告服务器并自动打开报告页面（增强模式，可点击直接删除）\n'
        'cd "$(dirname "$0")" || exit 1\npython3 "清理报告服务器.py"\necho\n'
        'echo "──────────────────────────────"\nread -n 1 -p "服务器已停止，按任意键关闭窗口…" _\necho\n')
    os.chmod(launcher, 0o755)

    n_top = len(data["items"])
    n_all = collect_ids_count(data["items"])
    print("✅ 构建完成：%s（顶层 %d 项 / 共 %d 节点，剔除失效 %d 项）" % (out_html, n_top, n_all, len(stale)))
    print("   同目录生成：清理报告服务器.py、启动清理报告.command")


def collect_ids_count(nodes):
    c = 0
    for n in nodes:
        c += 1 + collect_ids_count(n.get("kids") or [])
    return c


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
清理报告本地服务器
==================
让「电脑垃圾文件清理报告.html」获得三个能力：
  1. 点击「删除」直接执行（默认移入废纸篓，可恢复；可选彻底删除）
  2. 点击「打开」直接在访达中定位
  3. 点击路径/刷新时重新测量目录当前占用

安全设计（为什么可以放心让它跑在后台）：
  · 只监听 127.0.0.1，外网/局域网完全访问不到
  · 校验 Host 头，防 DNS 重绑定攻击
  · 每个请求必须带一次性随机令牌（令牌只注入到本服务器吐出的网页里，
    磁盘上的 HTML 文件不含令牌；别的网页因同源策略拿不到它）
  · 绝不执行网页传来的任何路径/命令——只接受「动作编号(id)」，
    然后执行服务器自己从报告数据里解析出的白名单动作
  · 「打开/测量」同样只对白名单里的路径放行
  · 每一次删除都追加记录到同目录 清理记录.log，可回溯

用法：双击「启动清理报告.command」，或终端执行 python3 清理报告服务器.py
停止：终端按 Control-C，或活动监视器结束 python3。
报告文件自动发现：与本脚本同目录、含 /*DATA-START*/ 标记的 .html（多份取最近修改者）。
"""

import glob
import hmac
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = 8648

# ── 系统数据/元数据保护清单（纵深防御：与 build.py 的 PROTECTED 保持同步）──
# 即使报告数据被错误配置，执行删除前也会按此清单二次拦截。
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
PROTECTED_ALLOWED = [r"^/private/var/folders/[^/]+/[^/]+/T(/|$)", r"^/Users/[^/]+/Library/Logs(/|$)"]


def protected_reason(path):
    if not path:
        return None
    if any(re.search(a, path) for a in PROTECTED_ALLOWED):
        return None
    for pat, why in PROTECTED:
        if re.search(pat, path):
            return why
    return None
HERE = os.path.dirname(os.path.abspath(__file__))
LOG_PATH = os.path.join(HERE, "清理记录.log")
TRASH = os.path.expanduser("~/.Trash")
TOKEN = secrets.token_hex(16)
PLACEHOLDER = "__REPORT_TOKEN__"
MARKER = "/*DATA-START*/"


def find_report_html():
    """自动发现报告：同目录下含 DATA 标记的 html（多份时取最近修改者）。"""
    cands = []
    for p in glob.glob(os.path.join(HERE, "*.html")):
        try:
            with open(p, encoding="utf-8") as f:
                if MARKER in f.read():
                    cands.append((os.path.getmtime(p), p))
        except OSError:
            continue
    if not cands:
        raise RuntimeError("同目录下没有找到含 /*DATA-START*/ 标记的报告 HTML")
    return max(cands)[1]


HTML_PATH = find_report_html()


# ---------------------------------------------------------------- 数据加载
def load_report():
    """从报告 HTML 里提取 /*DATA-START*/ var DATA = {...}; /*DATA-END*/ 数据，
    构建 id→动作 白名单 与 可打开/可测量路径集合。HTML 是唯一数据源，
    服务器与网页永不漂移。"""
    with open(HTML_PATH, encoding="utf-8") as f:
        src = f.read()
    m = re.search(r"/\*DATA-START\*/\s*var DATA = (\{.*?\});\s*/\*DATA-END\*/", src, re.S)
    if not m:
        raise RuntimeError("报告 HTML 中找不到 DATA 数据块")
    data = json.loads(m.group(1))

    actions, paths = {}, set()

    def walk(node):
        if isinstance(node, dict):
            nid = node.get("id")
            if nid:
                if node.get("p") and isinstance(node["p"], str):
                    paths.add(node["p"])
                for extra in (node.get("dp") or []):
                    if isinstance(extra, str):
                        paths.add(extra)
                act = node.get("act")
                if act and act.get("t"):
                    actions[nid] = {
                        "name": node.get("n") or nid,
                        "kind": act["t"],
                        "paths": act.get("paths") or node.get("dp") or ([node["p"]] if node.get("p") else []),
                        "cmd": act.get("c") or "",
                    }
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(data.get("items"))
    if not actions:
        raise RuntimeError("DATA 中没有找到任何可执行动作")
    return data, actions, paths


# ---------------------------------------------------------------- 删除实现
def _unique_trash_name(base: str) -> str:
    name, ext = os.path.splitext(base)
    dst = os.path.join(TRASH, base)
    i = 1
    while os.path.exists(dst):
        dst = os.path.join(TRASH, "%s ·清理报告%d%s" % (name, i, ext))
        i += 1
    return dst


_TRASH_LOCK = threading.Lock()


def trash_move(path: str):
    """把路径移入废纸篓（同卷 rename，瞬间完成、可恢复）。加锁避免并发删除时同名覆盖。"""
    if not os.path.exists(path):
        return {"ok": False, "reason": "路径不存在（可能已删除）"}
    with _TRASH_LOCK:
        dst = _unique_trash_name(os.path.basename(path.rstrip("/")))
        try:
            if os.stat(path).st_dev != os.stat(TRASH).st_dev:
                return {"ok": False, "reason": "跨磁盘分区，无法移入废纸篓；请用网页上的「生成脚本」方式删除"}
            os.rename(path, dst)
            return {"ok": True, "detail": "已移入废纸篓（可恢复）：~/.Trash/" + os.path.basename(dst)}
        except OSError as e:
            return {"ok": False, "reason": str(e)}


def permanent_remove(path: str):
    """彻底删除（不可恢复）。"""
    try:
        if os.path.isdir(path) and not os.path.islink(path):
            shutil.rmtree(path)
        else:
            os.remove(path)
        return {"ok": True, "detail": "已彻底删除"}
    except OSError as e:
        return {"ok": False, "reason": str(e)}


def run_fixed_command(cmd: str):
    """执行白名单里的固定命令（brew cleanup / osascript / find 等），
    命令字符串来自服务器本地数据，绝无网页输入。"""
    try:
        r = subprocess.run(["/bin/bash", "-c", cmd], capture_output=True, text=True, timeout=600)
        out = (r.stdout or r.stderr or "").strip()[:500]
        return {"ok": r.returncode == 0, "detail": out or ("完成 (exit %d)" % r.returncode)}
    except subprocess.TimeoutExpired:
        return {"ok": False, "reason": "命令超时（>600s）"}


def measure(path: str):
    try:
        r = subprocess.run(["/usr/bin/du", "-shx", path], capture_output=True, text=True, timeout=300)
        out = (r.stdout or "").split("\t")[0].strip()
        return {"ok": bool(out), "detail": out or "未知"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "reason": str(e)}


def log_action(record: dict):
    record["time"] = time.strftime("%Y-%m-%d %H:%M:%S")
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError:
        pass


# ---------------------------------------------------------------- HTTP
try:
    DATA, ACTIONS, WHITELIST_PATHS = load_report()
except Exception as e:  # noqa: BLE001
    print("初始化失败：" + str(e))
    print("请确认报告 HTML 与本脚本在同一文件夹、且未被改动损坏。")
    raise SystemExit(1)


class Handler(BaseHTTPRequestHandler):
    server_version = "CleanupReport/3.0"

    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _forbidden(self, why="forbidden"):
        self._json({"ok": False, "reason": why}, 403)

    def _host_ok(self):
        host = self.headers.get("Host", "")
        return host in ("127.0.0.1:%d" % PORT, "localhost:%d" % PORT, "[::1]:%d" % PORT)

    def _token_ok(self):
        return hmac.compare_digest(self.headers.get("X-Report-Token", ""), TOKEN)

    def do_GET(self):  # noqa: N802
        if not self._host_ok():
            return self._forbidden("bad host")
        if self.path in ("/", "/index.html"):
            with open(HTML_PATH, encoding="utf-8") as f:
                html = f.read()
            if PLACEHOLDER in html:
                html = html.replace(PLACEHOLDER, TOKEN)
            body = html.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path == "/api/ping":
            return self._json({"ok": True, "mode": "server", "actions": len(ACTIONS)})
        return self._json({"ok": False, "reason": "not found"}, 404)

    def do_POST(self):  # noqa: N802
        if not self._host_ok():
            return self._forbidden("bad host")
        if not self._token_ok():
            return self._forbidden("bad token")
        try:
            length = int(self.headers.get("Content-Length") or 0)
            req = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
        except (ValueError, UnicodeDecodeError):
            return self._json({"ok": False, "reason": "bad json"}, 400)

        route = self.path
        if route == "/api/delete":
            act = ACTIONS.get(req.get("id", ""))
            if not act:
                return self._json({"ok": False, "reason": "未知或不可删除的项 id"}, 404)
            perm = bool(req.get("perm"))
            if act["kind"] == "trash":
                for p in act["paths"]:
                    why = protected_reason(p)
                    if why:
                        log_action({"op": "delete-blocked", "id": req.get("id"), "path": p, "reason": why})
                        return self._json({"ok": False, "reason": "已拦截：该项指向受保护的%s（%s）。系统数据/元数据不提供删除" % (why, p)}, 403)
            if act["kind"] == "cmd":
                res = run_fixed_command(act["cmd"])
            elif perm:
                res = {"ok": True, "detail": ""}
                for p in act["paths"]:
                    res = permanent_remove(p)
                    if not res["ok"]:
                        break
            else:
                res = {"ok": True, "detail": ""}
                for p in act["paths"]:
                    res = trash_move(p)
                    if not res["ok"]:
                        break
            log_action({"op": "delete", "id": req.get("id"), "name": act["name"],
                        "perm": perm, "result": res})
            return self._json(res)

        if route == "/api/open":
            p = req.get("p", "")
            if p not in WHITELIST_PATHS:
                return self._forbidden("path not in whitelist")
            if not os.path.exists(p):
                return self._json({"ok": False, "reason": "路径不存在"})
            subprocess.Popen(["/usr/bin/open", p])
            log_action({"op": "open", "path": p, "result": {"ok": True}})
            return self._json({"ok": True})

        if route == "/api/du":
            p = req.get("p", "")
            if p not in WHITELIST_PATHS:
                return self._forbidden("path not in whitelist")
            res = measure(p)
            return self._json(res)

        return self._json({"ok": False, "reason": "not found"}, 404)

    def log_message(self, fmt, *args):  # 静默常规访问日志
        pass


def main():
    try:
        srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    except OSError:
        # 端口被占：大概率是上一个实例还在跑，探测确认后直接打开页面
        import urllib.request
        try:
            with urllib.request.urlopen("http://127.0.0.1:%d/api/ping" % PORT, timeout=2) as r:
                if json.loads(r.read()).get("ok"):
                    print("服务器已在运行，直接打开报告页面…")
                    webbrowser.open("http://127.0.0.1:%d/" % PORT)
                    return
        except Exception:  # noqa: BLE001
            pass
        print("端口 %d 被占用且不是本服务，请修改脚本里的 PORT。" % PORT)
        sys.exit(1)

    srv.daemon_threads = True
    url = "http://127.0.0.1:%d/" % PORT
    print("─" * 52)
    print("  磁盘清理报告服务器已启动")
    print("  页面地址: %s" % url)
    print("  报告文件: %s" % os.path.basename(HTML_PATH))
    print("  白名单动作: %d 个 · 删除默认进废纸篓（可恢复）" % len(ACTIONS))
    print("  记录文件: %s" % LOG_PATH)
    print("  停止: 按 Control-C")
    print("─" * 52)
    threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n服务器已停止。")
        srv.server_close()


if __name__ == "__main__":
    main()

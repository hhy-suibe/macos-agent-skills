#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
清理报告本地服务器
==================
让「电脑垃圾文件清理报告.html」获得三个能力：
  1. 点击「删除」直接执行（文件只移入废纸篓，可恢复；网页端不提供彻底删除）
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
import hashlib
import hmac
import json
import os
import re
import secrets
import subprocess
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

BASE_PORT = int(os.environ.get("CLEANUP_REPORT_PORT", "8648"))
PORT_SPAN = 20

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
ALLOWED_COMMANDS = {
    "brew cleanup --prune=all",
    "osascript -e 'tell application \"Finder\" to empty trash'",
}


def protected_reason(path):
    if not path:
        return None
    path = os.path.realpath(os.path.abspath(path))
    for pat, why in PROTECTED:
        if re.search(pat, path):
            return why
    return None


def validated_target(raw):
    """返回规范化操作路径；所有安全判断同时基于 realpath，防 .. / symlink 绕过。"""
    if not isinstance(raw, str) or not raw or "\x00" in raw or not os.path.isabs(raw):
        return None, "路径必须是非空绝对路径"
    path = os.path.abspath(os.path.normpath(raw))
    real = os.path.realpath(path)
    home = os.path.realpath(os.path.expanduser("~"))
    shallow = {"/", "/Users", "/private", "/private/tmp", "/opt", home,
               os.path.join(home, "Library", "Logs")}
    if real in shallow or len(real.strip("/").split("/")) < 3:
        return None, "路径过浅或为受保护的根目录"
    why = protected_reason(real)
    if why:
        return None, "该路径属于受保护的%s" % why
    return path, None
HERE = os.path.dirname(os.path.abspath(__file__))
LOG_PATH = os.path.join(HERE, "清理记录.log")
TEST_MODE = os.environ.get("CLEANUP_REPORT_TEST_MODE") == "1"
TRASH = os.path.abspath(os.environ.get("CLEANUP_REPORT_TRASH", os.path.expanduser("~/.Trash")) if TEST_MODE else os.path.expanduser("~/.Trash"))
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

    actions, paths, seen = {}, set(), set()

    def walk(node):
        if isinstance(node, dict):
            nid = node.get("id")
            if nid:
                if nid in seen:
                    raise RuntimeError("报告含重复 id: " + nid)
                seen.add(nid)
                if node.get("p") and isinstance(node["p"], str):
                    paths.add(node["p"])
                for extra in (node.get("dp") or []):
                    if isinstance(extra, str):
                        paths.add(extra)
                act = node.get("act")
                if act and act.get("t"):
                    if node.get("s") not in ("safe", "caution") or node.get("agg"):
                        raise RuntimeError("不可执行的 report/agg 条目含 act: " + nid)
                    if act.get("t") not in ("trash", "cmd"):
                        raise RuntimeError("未知动作类型: " + str(act.get("t")))
                    targets = act.get("paths") or node.get("dp") or ([node.get("p")] if node.get("p") else [])
                    if act.get("t") == "trash":
                        if not targets:
                            raise RuntimeError("trash 动作没有目标: " + nid)
                        checked = []
                        for target in targets:
                            normalized, why = validated_target(target)
                            if why:
                                raise RuntimeError("危险 trash 动作 %s: %s" % (nid, why))
                            checked.append(normalized)
                        targets = checked
                    elif not act.get("c") or act.get("c") not in ALLOWED_COMMANDS:
                        raise RuntimeError("命令型动作不在固定白名单: " + nid)
                    actions[nid] = {
                        "name": node.get("n") or nid,
                        "kind": act["t"],
                        "paths": targets,
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
    report_id = data.get("meta", {}).get("reportId") or hashlib.sha256(m.group(1).encode("utf-8")).hexdigest()[:20]
    return data, actions, paths, report_id


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
    if not os.path.lexists(path):
        return {"ok": True, "status": "already_absent", "already": True, "detail": "路径已不存在（此前可能已清理）"}
    with _TRASH_LOCK:
        dst = _unique_trash_name(os.path.basename(path.rstrip("/")))
        try:
            if os.stat(path).st_dev != os.stat(TRASH).st_dev:
                return {"ok": False, "reason": "跨磁盘分区，无法移入废纸篓；请用网页上的「生成脚本」方式删除"}
            os.rename(path, dst)
            return {"ok": True, "status": "deleted", "detail": "已移入废纸篓（可恢复）：~/.Trash/" + os.path.basename(dst)}
        except OSError as e:
            return {"ok": False, "status": "failed", "reason": str(e)}


def trash_many(paths):
    results = []
    for raw in paths:
        path, why = validated_target(raw)
        if why:
            results.append({"path": raw, "ok": False, "status": "failed", "reason": why})
            continue
        result = trash_move(path)
        results.append(dict(result, path=path))
    deleted = sum(1 for r in results if r.get("status") == "deleted")
    skipped = sum(1 for r in results if r.get("status") == "already_absent")
    failed = sum(1 for r in results if not r.get("ok"))
    if failed:
        status = "partial" if deleted or skipped else "failed"
        reasons = [r.get("reason", "未知错误") for r in results if not r.get("ok")]
        return {"ok": False, "partial": status == "partial", "status": status,
                "deleted": deleted, "skipped": skipped, "failed": failed,
                "reason": "；".join(reasons[:3]), "results": results}
    return {"ok": True, "status": "already_absent" if skipped and not deleted else "success",
            "already": skipped > 0 and deleted == 0, "deleted": deleted, "skipped": skipped,
            "failed": 0, "detail": "目标已处理完成", "results": results}


def run_fixed_command(cmd: str):
    """执行白名单里的固定命令（brew cleanup / osascript / find 等），
    命令字符串来自服务器本地数据，绝无网页输入。"""
    try:
        r = subprocess.run(["/bin/bash", "-c", cmd], capture_output=True, text=True, timeout=600)
        out = (r.stdout or r.stderr or "").strip()[:1000]
        if r.returncode == 0:
            return {"ok": True, "status": "success", "detail": out or "完成"}
        return {"ok": False, "status": "failed", "reason": out or ("命令退出码 %d" % r.returncode), "detail": out}
    except subprocess.TimeoutExpired:
        return {"ok": False, "reason": "命令超时（>600s）"}
    except OSError as e:
        return {"ok": False, "reason": str(e)}


def measure(path: str):
    try:
        r = subprocess.run(["/usr/bin/du", "-shx", path], capture_output=True, text=True, timeout=300)
        out = (r.stdout or "").split("\t")[0].strip()
        if r.returncode != 0 or not out:
            return {"ok": False, "reason": (r.stderr or "无法测量").strip()[:500]}
        return {"ok": True, "detail": out}
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
    DATA, ACTIONS, WHITELIST_PATHS, REPORT_ID = load_report()
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
        port = self.server.server_port
        return host in ("127.0.0.1:%d" % port, "localhost:%d" % port, "[::1]:%d" % port)

    def _token_ok(self):
        return hmac.compare_digest(self.headers.get("X-Report-Token", ""), TOKEN)

    def do_GET(self):  # noqa: N802
        if not self._host_ok():
            return self._forbidden("bad host")
        route = urlsplit(self.path).path
        if route in ("/", "/index.html"):
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
        if route == "/api/ping":
            gone = []
            for aid, action in ACTIONS.items():
                if action["kind"] == "trash" and action["paths"] and all(not os.path.lexists(p) for p in action["paths"]):
                    gone.append(aid)
            return self._json({"ok": True, "mode": "server", "actions": len(ACTIONS),
                               "reportId": REPORT_ID, "gone": gone})
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

        route = urlsplit(self.path).path
        if route == "/api/delete":
            act = ACTIONS.get(req.get("id", ""))
            if not act:
                return self._json({"ok": False, "reason": "未知或不可删除的项 id"}, 404)
            if req.get("perm") not in (None, False):
                return self._json({"ok": False, "reason": "网页端已禁用彻底删除；请使用默认的废纸篓方式"}, 409)
            if act["kind"] == "trash":
                for p in act["paths"]:
                    _normalized, why = validated_target(p)
                    if why:
                        log_action({"op": "delete-blocked", "id": req.get("id"), "path": p, "reason": why})
                        return self._json({"ok": False, "reason": "已拦截：该项指向受保护的%s（%s）。系统数据/元数据不提供删除" % (why, p)}, 403)
            if act["kind"] == "cmd":
                res = run_fixed_command(act["cmd"])
            else:
                res = trash_many(act["paths"])
            log_action({"op": "delete", "id": req.get("id"), "name": act["name"],
                        "perm": False, "result": res})
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
    srv = None
    for port in range(BASE_PORT, BASE_PORT + PORT_SPAN):
        try:
            srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
            break
        except OSError:
            continue
    if srv is None:
        print("端口 %d-%d 均被占用，无法启动报告。" % (BASE_PORT, BASE_PORT + PORT_SPAN - 1))
        sys.exit(1)

    srv.daemon_threads = True
    url = "http://127.0.0.1:%d/" % srv.server_port
    print("─" * 52)
    print("  磁盘清理报告服务器已启动")
    print("  页面地址: %s" % url)
    print("  报告文件: %s" % os.path.basename(HTML_PATH))
    print("  白名单动作: %d 个 · 删除默认进废纸篓（可恢复）" % len(ACTIONS))
    print("  记录文件: %s" % LOG_PATH)
    print("  停止: 按 Control-C")
    print("─" * 52)
    if os.environ.get("CLEANUP_REPORT_NO_BROWSER") != "1":
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n服务器已停止。")
        srv.server_close()


if __name__ == "__main__":
    main()

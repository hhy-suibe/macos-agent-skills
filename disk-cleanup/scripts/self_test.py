#!/usr/bin/env python3
"""disk-cleanup 的无用户数据回归测试；所有可写/删除目标都位于临时目录。"""

import copy
import http.client
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request


HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(HERE)
BUILD = os.path.join(HERE, "build.py")
TPL = os.path.join(SKILL, "assets", "report.template.html")


def check(condition, message):
    if not condition:
        raise AssertionError(message)


def write_json(path, value):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(value, fh, ensure_ascii=False)


def build(root, name, data, expect_ok=True):
    data_path = os.path.join(root, name + ".json")
    out = os.path.join(root, name)
    write_json(data_path, data)
    result = subprocess.run([sys.executable, BUILD, data_path, TPL, out], capture_output=True, text=True)
    if expect_ok:
        check(result.returncode == 0, "合法报告构建失败：" + result.stdout + result.stderr)
    else:
        check(result.returncode != 0, "非法报告未被拒绝：" + name)
    return out, result


def consecutive_ports():
    for base in range(18648, 19648):
        sockets = []
        try:
            for port in (base, base + 1):
                sock = socket.socket()
                sock.bind(("127.0.0.1", port))
                sockets.append(sock)
            return base
        except OSError:
            pass
        finally:
            for sock in sockets:
                sock.close()
    raise RuntimeError("找不到连续空闲测试端口")


def wait_json(url, timeout=8):
    end = time.time() + timeout
    while time.time() < end:
        try:
            with urllib.request.urlopen(url, timeout=0.5) as response:
                return json.loads(response.read().decode("utf-8"))
        except Exception:
            time.sleep(0.1)
    raise RuntimeError("服务器未按时启动: " + url)


def post(port, route, token, body):
    req = urllib.request.Request(
        "http://127.0.0.1:%d%s" % (port, route),
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", "X-Report-Token": token},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=3) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read().decode("utf-8"))


def main():
    test_parent = os.path.abspath(os.environ.get("CLEANUP_REPORT_TEST_ROOT", os.getcwd()))
    with tempfile.TemporaryDirectory(prefix="disk-cleanup-selftest-", dir=test_parent) as root:
        cache = os.path.join(root, "user", "Library", "Caches", "Demo")
        model = os.path.join(root, "user", "Library", "Caches", "Model")
        profile = os.path.join(root, "user", "Library", "Application Support", "Browser", "Default")
        multi_a = os.path.join(root, "user", "Library", "Caches", "MultiA")
        locked_parent = os.path.join(root, "user", "Library", "Caches", "Locked")
        multi_b = os.path.join(locked_parent, "MultiB")
        os.makedirs(cache)
        os.makedirs(model)
        os.makedirs(profile)
        os.makedirs(multi_a)
        os.makedirs(multi_b)
        with open(os.path.join(cache, "cache.bin"), "wb") as fh:
            fh.write(b"cache")

        data = {
            "meta": {"scanDate": "2099-01-01", "os": "testOS", "disk": {"total": "1Gi", "used": "1Mi", "free": "1023Mi"}},
            "items": [
                {"id": "safe-cache", "p": cache, "n": "测试缓存", "c": "cache", "s": "safe", "b": 5,
                 "note": "测试缓存，可重建。", "act": {"t": "trash"}},
                {"id": "caution-model", "p": model, "n": "测试模型", "c": "dev", "s": "caution", "b": 0,
                 "note": "删除后需要重新下载。", "act": {"t": "trash"}},
                {"id": "report-profile", "p": profile, "n": "测试资料", "c": "report", "s": "report", "b": 0,
                 "note": "用户资料，只展示。"},
                {"id": "multi-trash", "p": multi_a, "n": "多目标测试缓存", "c": "cache", "s": "safe", "b": 0,
                 "note": "用于验证部分成功状态。", "act": {"t": "trash", "paths": [multi_a, multi_b]}},
                {"id": "agg-root", "p": os.path.join(root, "user"), "n": "测试汇总", "c": "report", "s": "report", "b": 123,
                 "note": "只读汇总。", "agg": True},
            ],
        }

        out_a, _ = build(root, "report-a", data)
        html_a = open(os.path.join(out_a, "电脑垃圾文件清理报告.html"), encoding="utf-8").read()
        check("permChk" not in html_a, "页面仍包含永久删除复选框")
        check('return "rm -rf "' not in html_a, "静态页面仍生成 rm -rf")
        check(re.search(r'"reportId":"[0-9a-f]{20}"', html_a) is not None, "缺少 reportId")

        bad = copy.deepcopy(data)
        bad["items"][2]["act"] = {"t": "trash"}
        build(root, "bad-report-act", bad, False)
        bad = copy.deepcopy(data)
        bad["items"][1].pop("act")
        build(root, "bad-caution-no-act", bad, False)
        bad = copy.deepcopy(data)
        bad["items"][0]["p"] = "/"
        build(root, "bad-shallow", bad, False)
        bad = copy.deepcopy(data)
        bad["items"][0]["p"] = os.path.expanduser("~/Library/Logs")
        build(root, "bad-logs-root", bad, False)
        preferences_logs = os.path.join(root, "user", "Library", "Preferences", "Logs")
        os.makedirs(preferences_logs)
        bad = copy.deepcopy(data)
        bad["items"][0]["p"] = preferences_logs
        build(root, "bad-allowlist-bypass", bad, False)
        bad = copy.deepcopy(data)
        bad["items"][0]["act"] = {"t": "cmd", "c": "echo unsafe"}
        build(root, "bad-command", bad, False)
        symlink = os.path.join(root, "user", "Library", "Caches", "system-link")
        os.symlink("/System", symlink)
        bad = copy.deepcopy(data)
        bad["items"][0]["p"] = symlink
        build(root, "bad-symlink", bad, False)

        data_b = copy.deepcopy(data)
        data_b["meta"]["scanDate"] = "2099-01-02"
        data_b["items"][0]["n"] = "另一份测试缓存"
        out_b, _ = build(root, "report-b", data_b)

        test_trash = os.path.join(root, "test-trash")
        os.makedirs(test_trash)
        base = consecutive_ports()
        env = os.environ.copy()
        env.update({
            "CLEANUP_REPORT_PORT": str(base),
            "CLEANUP_REPORT_NO_BROWSER": "1",
            "CLEANUP_REPORT_TEST_MODE": "1",
            "CLEANUP_REPORT_TRASH": test_trash,
        })
        procs = []
        try:
            for out in (out_a, out_b):
                procs.append(subprocess.Popen([sys.executable, "清理报告服务器.py"], cwd=out, env=env,
                                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
                wait_json("http://127.0.0.1:%d/api/ping" % (base + len(procs) - 1))
            ping_a = wait_json("http://127.0.0.1:%d/api/ping" % base)
            ping_b = wait_json("http://127.0.0.1:%d/api/ping" % (base + 1))
            check(ping_a["reportId"] != ping_b["reportId"], "第二份报告错误复用了旧服务")

            page = urllib.request.urlopen("http://127.0.0.1:%d/" % base).read().decode("utf-8")
            token = re.search(r'var TOKEN = "([0-9a-f]+)"', page).group(1)
            status, body = post(base, "/api/delete", "bad-token", {"id": "safe-cache"})
            check(status == 403 and os.path.exists(cache), "无效 token 未被拒绝")
            status, body = post(base, "/api/open", token, {"p": "/etc"})
            check(status == 403, "非白名单打开路径未被拒绝")
            os.chmod(locked_parent, 0o500)
            try:
                status, body = post(base, "/api/delete", token, {"id": "multi-trash", "perm": False})
                check(status == 200 and body.get("partial") and body.get("deleted") == 1 and body.get("failed") == 1,
                      "多目标动作没有返回部分成功")
            finally:
                os.chmod(locked_parent, 0o700)
            status, body = post(base, "/api/delete", token, {"id": "safe-cache", "perm": True})
            check(status == 409 and os.path.exists(cache), "服务器未拒绝网页端永久删除")
            status, body = post(base, "/api/delete", token, {"id": "safe-cache", "perm": False})
            check(status == 200 and body.get("ok") and not os.path.exists(cache), "首次移入测试废纸篓失败")
            status, body = post(base, "/api/delete", token, {"id": "safe-cache", "perm": False})
            check(status == 200 and body.get("already"), "重复删除没有按幂等成功处理")
            check("safe-cache" in wait_json("http://127.0.0.1:%d/api/ping" % base).get("gone", []), "状态接口未返回已清理 id")

            conn = http.client.HTTPConnection("127.0.0.1", base, timeout=3)
            conn.request("GET", "/api/ping", headers={"Host": "evil.example"})
            check(conn.getresponse().status == 403, "Host 校验失效")
            conn.close()
        finally:
            for proc in procs:
                proc.terminate()
            for proc in procs:
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    proc.kill()

    print("✅ disk-cleanup self-test passed")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""merge.py — 把增强数据（子 agent 返回的明细树）幂等合并进 data.json。

用法:
    python3 merge.py <data.json> <增强数据1.json> [增强数据2.json ...]

增强数据格式（两种都支持）:
    [ {节点树}, {节点树}, ... ]    # JSON 数组，每项一棵完整节点树
    { "root": {节点树} }            # 单根包装（如下载文件夹逐条分析）

节点字段: p(绝对路径,合并主键) n(名称) c(分类) s(safe|caution|report) b(字节)
          note(中文说明) mt(YYYY-MM-DD) cnt(文件数) act(动作) kids(子节点)

合并规则（幂等，重复执行结果一致）:
  - 按绝对路径 p 为唯一键递归合并 kids；绝不整树覆盖（保住扫描底座的既有子树，
    如微信按月份明细）。
  - 增强数据只更新元数据；act 按安全级别修正:
      report/agg → 绝无 act；safe/caution → 补 {"t":"trash"}（cmd 型保留原值）。
  - trash 命中保护清单的节点强制降级 report 并在 note 说明（PROTECTED 与
    build.py / server.py 保持同步，改动须三处同步）。
  - 全树归位: 任何节点的父目录若以节点形式存在于树中其他位置，移挂过去；顶层
    条目的祖先在树中时补建缺失中间目录——消除「同一物理目录两个节点并列」导致
    的重复计数。
  - 中间链路节点的 b 用 kids 之和回填（仅当缺失或偏小时）。
"""
import hashlib
import json
import os
import re
import sys

# ── 系统数据/元数据保护清单（与 build.py / server.py 保持同步；trash 命中即降级 report）──
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

META_FIELDS = ("n", "c", "s", "b", "note", "mt", "cnt", "sp", "agg", "measure")


def node_id(p):
    return "x-%s" % hashlib.sha256(p.encode()).hexdigest()[:12]


def norm(p):
    return os.path.realpath(os.path.expanduser(p))


def protected_reason(path):
    path = os.path.realpath(os.path.abspath(path))
    for pat, why in PROTECTED:
        if re.search(pat, path):
            return why
    return None


def fix_act(node):
    """act 与 s 的一致性；trash 命中保护清单则降级 report。"""
    s = node.get("s")
    act = node.get("act")
    if s == "report" or node.get("agg"):
        node.pop("act", None)
        return
    if s in ("safe", "caution"):
        if act and act.get("t") == "trash":
            why = protected_reason(node.get("p", ""))
            if why:
                node["s"] = "report"
                node.pop("act", None)
                node["note"] = (node.get("note") or "") + "（该位置属于%s保护范围，只展示不提供删除。）" % why
                return
        if not act:
            node["act"] = {"t": "trash"}
        elif act.get("t") not in ("trash", "cmd"):
            node["act"] = {"t": "trash"}


def load_roots(path):
    with open(path) as f:
        data = json.load(f)
    if isinstance(data, dict) and "root" in data:
        return [data["root"]]
    if isinstance(data, dict):
        return [data]
    return list(data)


def build_index(nodes, index, parents):
    for n in nodes:
        index[norm(n.get("p", ""))] = n
        for k in n.get("kids") or []:
            parents[norm(k.get("p", ""))] = n
        build_index(n.get("kids") or [], index, parents)


def upsert(enode, index, parents, top_items):
    """把增强节点及其整棵子树 upsert 进现有树。"""
    p = norm(enode["p"])
    cur = index.get(p)
    if cur is None:
        cur = {"id": node_id(p), "p": enode["p"]}
        parent_dir = os.path.dirname(p)
        # 父节点优先取 kids 索引，其次全局路径索引（父可能是树中任意位置的节点）
        parent = parents.get(parent_dir) or index.get(parent_dir)
        if parent is not None:
            parent.setdefault("kids", []).append(cur)
            parents[p] = parent
        else:
            top_items.append(cur)
            parents[p] = None
        index[p] = cur
    for f in META_FIELDS:
        if f in enode:
            cur[f] = enode[f]
    fix_act(cur)
    for k in enode.get("kids") or []:
        upsert(k, index, parents, top_items)
    return cur


def full_reparent(top_items):
    """全树归位 + 顶层补链；循环至稳定，消除并列重复计数。"""
    moved = True
    guard = 0
    while moved and guard < 50:
        moved = False
        guard += 1
        index = {}

        def rebuild(nodes):
            for n in nodes:
                index[norm(n["p"])] = n
                rebuild(n.get("kids") or [])

        rebuild(top_items)

        # 1) 子级归位：把每个 kid 移到其真实父目录节点下（若存在且不是当前父）
        def walk_nodes(nodes):
            for node in nodes:
                kids = node.get("kids")
                if not kids:
                    continue
                for k in list(kids):
                    d = os.path.dirname(norm(k["p"]))
                    target = index.get(d)
                    if target is not None and target is not node and k not in (target.get("kids") or []):
                        kids.remove(k)
                        target.setdefault("kids", []).append(k)
                        moved = True
                walk_nodes(kids)

        walk_nodes(top_items)

        # 2) 顶层重挂：祖先在树中的顶层条目，补建中间链挂回
        index = {}
        rebuild(top_items)
        for i, it in enumerate(top_items):
            chain = []
            d = os.path.dirname(norm(it["p"]))
            anchor = None
            while d != "/":
                if d in index and index[d] is not it:
                    anchor = index[d]
                    break
                chain.append(d)
                d = os.path.dirname(d)
            if anchor is None:
                continue
            parent = anchor
            for seg in reversed(chain):
                pn = index.get(seg)
                if pn is None:
                    pn = {
                        "id": node_id(seg),
                        "p": seg,
                        "n": os.path.basename(seg) or seg,
                        "c": "report",
                        "s": "report",
                        "note": "分类用中间目录，本身不建议直接删除。",
                        "mt": None,
                        "cnt": -1,
                    }
                    parent.setdefault("kids", []).append(pn)
                    index[seg] = pn
                parent = pn
            parent.setdefault("kids", []).append(it)
            top_items.pop(i)
            moved = True
            break


def propagate_b(nodes):
    """中间链路节点的 b 用 kids 之和回填（仅当缺失或明显偏小时）。"""
    for n in nodes:
        propagate_b(n.get("kids") or [])
        kids = n.get("kids") or []
        if kids:
            total = sum(k.get("b") or 0 for k in kids)
            if not n.get("b") or n["b"] < total:
                n["b"] = total
            if not n.get("mt"):
                mts = [k["mt"] for k in kids if k.get("mt")]
                if mts:
                    n["mt"] = max(mts)


def sort_kids(nodes):
    for n in nodes:
        kids = n.get("kids")
        if kids:
            kids.sort(key=lambda k: -(k.get("b") or 0))
            sort_kids(kids)


def main(argv):
    if len(argv) < 3:
        print(__doc__)
        return 2
    data_path = argv[1]
    with open(data_path) as f:
        data = json.load(f)
    top = data["items"]

    index, parents = {}, {}
    build_index(top, index, parents)
    before = len(index)

    for src in argv[2:]:
        for enode in load_roots(src):
            upsert(enode, index, parents, top)

    # 统一补全：所有节点都有 id 且 act 与 s 一致（含扫描底座原有节点）
    def walk(nodes):
        for n in nodes:
            if not n.get("id"):
                n["id"] = node_id(norm(n.get("p", "")))
            fix_act(n)
            walk(n.get("kids") or [])
    walk(top)

    full_reparent(top)
    propagate_b(top)
    sort_kids(top)

    # 幂等自检：同一物理路径不得有两个节点
    seen = {}
    def collect(nodes):
        for n in nodes:
            p = norm(n.get("p", ""))
            if p in seen:
                raise SystemExit("❌ 重复路径节点: %s" % p)
            seen[p] = n
            collect(n.get("kids") or [])
    collect(top)

    with open(data_path, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    print("✅ 合并完成：节点 %d → %d，顶层条目 %d → %s"
          % (before, len(seen), len(top), data_path))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

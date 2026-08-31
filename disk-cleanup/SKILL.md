---
name: disk-cleanup
description: Mac 磁盘/文件清理工作流。全面扫描本机缓存、临时文件、日志、开发工具缓存、微信等应用大目录的内部明细，生成一个可交互的 HTML 清理报告（现代 UI、逐层下钻、每项带安全分级），并启动本地小服务器让用户在网页上点击即可直接删除（默认进废纸篓可反悔）。用户说「清理文件 / 磁盘清理 / 垃圾文件太多 / 空间不足 / C盘满了（Mac语境）/ 清理缓存 / 生成清理报告」或想梳理电脑文件时使用本 skill。
---

# Mac 磁盘清理报告

## 这套流程做什么

1. **只读扫描**全盘垃圾位置（缓存/临时/日志/开发工具/下载/微信等大目录内部）。
2. **构建三件套**到输出目录：`电脑垃圾文件清理报告.html`（含逐层明细与安全分级）、`清理报告服务器.py`、`启动清理报告.command`。
3. **启动本地服务器**（优先 127.0.0.1:8648，端口占用时自动顺延），浏览器自动打开增强版页面：普通文件只移入废纸篓。

**环境要求**：macOS + python3（系统自带即可）；node 可选（构建时做 JS 语法校验，缺失自动跳过）。

## 安全铁律（必须遵守）

- 扫描阶段**只允许只读命令**（du/find/stat/ls/df/sw_vers/tmutil）。绝不主动删除、移动、修改任何用户文件。
- 删除**只能由用户**在增强版报告页面上点击完成；普通文件只移入 `~/.Trash`。静态页面不生成 `rm -rf`，网页端不提供“彻底删除”。
- `brew cleanup`、清空废纸篓是仅有的固定白名单命令型动作，会直接执行且不可恢复；确认弹窗必须单独列出，绝不自动勾选“清空废纸篓”。
- 分类保守：缓存/日志/临时 → `safe`；重下/重建有代价 → `caution`；含用户数据或拿不准 → `report`（只展示，不给删除按钮）。**聊天数据库、笔记、浏览器 Profile 一律 report。**
- `trash` 动作绝不允许指向 `/`、`/Users`、家目录、`~/Library/Logs` 根目录等过浅/受保护路径。build.py 与 server.py 都必须基于 `realpath` 独立校验，禁止 `..` 或符号链接绕过。
- **系统数据/元数据保护清单（build.py 与 server.py 各有一份 PROTECTED，改动须两处同步）**：钥匙串、Preferences、iCloud 云盘、iPhone 备份、Group Containers、苹果第一方登录态（HTTPStorages/WebKit 下 com.apple.*）、数据库文件（*.db/*.sqlite）、GoodNotes 笔记、浏览器 Profile、凭据目录（.ssh/.gnupg/.aws）、系统位置（/System、/usr、/Library、/private/var 的 db/vm/folders）——trash 命中即拒绝构建，服务器执行前还会二次拦截。禁止“先匹配允许规则就跳过全部保护”的全局豁免。
- `$TMPDIR` 与 `~/Library/Logs` 只展示汇总，不能提供根目录动作；只有明确、独立、可重建的日志子目录可设为 `safe + trash`。
- 报告数据必须来自**当次真实扫描**；构建时 build.py 会剔除已不存在的路径——用户上次已清理的条目不要手工加回。

## 工作流

### 1. 准备

```bash
OUT=~/磁盘清理报告-$(date +%Y%m%d)     # 或用户指定目录
SKILL=<本skill目录>
mkdir -p "$OUT"
```

### 2. 机械扫描（底座）

```bash
python3 "$SKILL/scripts/scan.py" "$OUT/data.json"
```

覆盖常见缓存/临时/日志/开发工具位置，自动对 >500MB 的大目录做一层下钻，微信按月明细（新版 xwechat_files 结构）也会自动带上。

### 3. 智能增强（用子 agent 并行做，质量的关键）

scan.py 的分类是保守猜测，必须增强后再构建。并行派 2–3 个**只读子 agent**：

- 一个深挖大头目录（微信容器、Chrome、Docker、GoodNotes、WPS、飞书、VS Code…即 data.json 里 `report` 级大项）：内部 du -d2~4，逐个子目录分类（cache/data/log/binary）+ 写初学者能懂的中文说明（是什么+删除影响）。
- 一个补元数据：为全部节点补 `mt`（最后修改日期）与 `cnt`（文件数），并扫描 scan.py 没覆盖的位置（WebKit、HTTPStorages、Group Containers、Saved Application State、DiagnosticReports 等）。
- 下载文件夹逐条列出（安装包/杂物/个人文档分清，个人文档标 report 且文案注明勿删）。

**数据完整性铁律**：① `caution` 必须带 `act`；② `report` 与 `agg` 绝不能带 `act`；③ `report` 必须有 `kids`、`sp` 或讲清原因的 `note`；④ `agg.b` 在构建时用 du 复测，失败只能保留旧值并注明，不能覆盖成 0；⑤ 最终文案不得出现“请 agent / 自动下钻 / 待复核”等占位语。

把子 agent 返回的 JSON 与 data.json 合并，用自带的 `scripts/merge.py`（勿手工重打）：

```bash
python3 "$SKILL/scripts/merge.py" "$OUT/data.json" 增强1.json 增强2.json ...
```

merge.py 按绝对路径幂等递归合并 `kids`（绝不整树覆盖，保住扫描底座的微信月份树）、把 trash 命中保护清单的条目自动降级 report、并做全树归位（子节点挂回其真实父目录节点、补建缺失中间链）消除并列重复计数。条目名称里**不要嵌入具体大小**（大小由 `b` 字段驱动显示）。

### 4. 构建 + 校验

```bash
python3 "$SKILL/scripts/build.py" "$OUT/data.json" "$SKILL/assets/report.template.html" "$OUT"
```

build.py 自动完成：id 唯一性、安全级别与动作互斥、固定命令白名单、realpath/浅路径保险栓、失效路径剔除、汇总项复测、占位文案检查、注入后 JSON 闭环、`node --check`。

### 5. 冒烟与安全测试（必做）

```bash
# 页面 JS 冒烟（DOM 桩）：提取 <script> 后 eval，验证 ITEMS 加载/保险栓/勾选逻辑/渲染不报错
# 服务器安全：起服务后依次验证——
curl -s http://127.0.0.1:8648/api/ping                                  # ok:true
curl -s -X POST http://127.0.0.1:8648/api/delete -d '{"id":"x"}'        # 403 bad token
# 用页面里的 TOKEN 验证：伪造 id→404、/api/open 非白名单→403、/api/du 白名单→只读成功
# 另测：report+act 构建失败；重复删除返回 already_absent；受保护/浅路径/符号链接全部拒绝；旧报告占 8648 时新报告换端口而不是打开旧页
python3 "$SKILL/scripts/self_test.py"  # 仅操作当前目录下的临时测试树，不触碰真实缓存
```

### 6. 启动并交付

```bash
cd "$OUT" && nohup python3 清理报告服务器.py > 服务器.log 2>&1 & disown
```

浏览器会自动打开实际监听地址。8648 被旧报告占用时，新报告在后续空闲端口启动，绝不复用旧页面。最后派一个**对抗式审查子 agent** 复查：安全分级、前后端 id 一致、危险路径、刷新后的已清理状态和触屏可见错误原因。发现问题修复后重新 build。

### 7. 向用户汇报

- 可直接删多少 / 谨慎多少 / 大头里藏着多少（报告顶部自动统计）。
- 三件套位置；以后双击 `启动清理报告.command` 即可再次使用；停止服务请关闭对应的启动终端或按 Control-C，不使用宽泛 `pkill`。
- 删除默认进废纸篓；「清空废纸篓」「brew 清理」等命令型条目会直接执行（页面弹窗有标注）。

## 常见情况

- **用户上次清理过**：scan/build 会自动剔除已消失路径，属正常；可在 meta.notes 里写一句「上次已清理约 X」。
- **端口被占**：服务器自动尝试后续端口，并打开当前报告；不得因为旧服务返回 `ok:true` 就打开旧报告。
- **数据过期/清理后刷新**：服务器把目标已不存在视为“此前已清理”，页面禁用该条目；要刷新大小与分类时仍应重跑第 2、4、6 步。
- **权限受限**：`~/.Trash`、`MobileSync/Backup` 常被 TCC 挡住（Operation not permitted），标「大小未知」并给用户开权限指引，不要反复重试。

## 模板设计规范（v5 羊皮纸风，改动模板前必读）

用户对视觉质量极其敏感，曾明确反馈"很乱很差、没有框架与条理、图片大大小小层次不齐"。v4 确立结构规则，v5（2026-08-25）把视觉层整体切换为 **Kami 羊皮纸风**（源自 tw93/Kami，完整规范在 `references/design-kami.md`，色板在 `references/tokens-kami.json`，改视觉前先读其 Color / Typography / Depth 三章）。

### 视觉层规则（Kami 羊皮纸，v5 新增）

1. **画布羊皮纸 `#f5f4ed`，卡片象牙白 `#faf9f5`，永不纯白**；所有灰必须是暖灰（R≈G>B，黄褐底色），禁止冷蓝灰（如 #f0f1f5、#98a0ad）。
2. **唯一装饰色 = 墨蓝 `#1B365D`**（链接、图标底、强调数字、构成条以外的点缀），覆盖面积 ≤5%。绿(safe)/褐(caution)/暖灰(report)/暖红(删除)是**语义色**不算装饰，但必须用暗哑的暖调版本（值见模板 `:root`），禁止荧光亮色。
3. **衬线扛标题与数字**：`--serif`（TsangerJinKai02，CDN 加载，离线回落 Songti SC）只用于 h1/h2/h3、区块标题、大小数字 `.size`、统计值 `.stat .v`，字重锁 500；正文与 UI 控件保持无衬线，标签字重 ≤600。
4. **禁渐变、禁硬投影、禁光斑**：深度只来自象牙白填充 + 耳语阴影 `0 4px 24px rgba(20,20,19,.05)`；按钮态用环形阴影 `0 0 0 1px`。深色块（pre.cmd、toast）用暖黑 `#141413` 而非冷蓝黑。
5. 无暗色模式（`prefers-color-scheme` 覆盖已删）：羊皮纸是唯一画布，这是 Kami 的立场，别加回来。

### 结构层规则（继承 v4，不得破坏）

1. **图标只用一套内联 SVG**（`IC` 对象，24 viewBox / stroke 2 / round cap），任何位置**禁止用 emoji 当图标**——emoji 在不同系统渲染大小不一，正是"图片大小不齐"的根源。新增图标走 `ic(path)` 工厂。
2. **数据行是严格 4 列网格** `--rowgrid: 22px minmax(0,1fr) 92px 264px`（勾选/内容/大小/操作），所有行和子项共用同一模板，保证大小数字与按钮右对齐到同一条竖线。改列宽改 `--rowgrid` 一处即可。
3. **统一组件尺寸**：徽章统一高（都有 `border:1px solid transparent` 防高低差）、按钮统一高 29px、图标钮 `.abtn` 29×29、子项内 26px。tab 里的计数只能写进 `.tc` span（tab 内有其它子元素，不能整改 textContent）。
4. **分组卡片统一解剖**：SVG 图标 + `SECTION NN` 编号 + 标题 + 右侧汇总胶囊 + 头部下方全宽 4px 构成条（绿/褐/灰）。编号由 render() 按出现顺序生成，空分类自动跳过。
5. **子项递归渲染**：`kidHtml()` 必须递归（微信数据是三层：条目→图片/视频/会话→按月份/会话），`toggleKids` 按 `kids-<id>` 通用查找天然支持任意深度；`.kid > .kids` 需 `grid-column:1/-1`。破坏递归会让"按月勾选删除"承诺落空（曾因不递归丢失 108 个月份项）。
6. 交付前用 headless Chrome 截图（hero/列表/展开明细/弹窗四个状态）自查一遍对齐与溢出；条件允许时用浏览器测 `getBoundingClientRect` 验证列对齐（所有 `.c-size` right、`.c-act` right 应各自唯一）。注意视觉模型会幻觉出"未对齐"——CSS 网格保证对齐时以几何测量为准。

## 文件清单

- `scripts/scan.py` — 只读扫描底座，产 data.json
- `scripts/merge.py` — 把增强数据幂等合并进 data.json（按路径递归合并、保护清单降级、全树归位）
- `scripts/build.py` — 校验 + 注入模板 + 产三件套
- `scripts/server.py` — 本地白名单服务器（源码，被 build.py 复制为 `清理报告服务器.py`）
- `scripts/self_test.py` — 安全回归测试（构建不变量、路径保护、幂等删除、端口隔离、Host/token）
- `assets/report.template.html` — 报告模板（占位符 `__SCAN_DATA__` / `__SCAN_DATE__` / `__SCAN_META__` / `__REPORT_TOKEN__`）
- `references/design-kami.md` — Kami 设计规范全文（视觉层规则的最终依据）
- `references/tokens-kami.json` — Kami 注册色板 token 表
- `references/LICENSE-kami` — 上游 tw93/Kami 的 MIT 许可全文（转发其文件的合规要求）

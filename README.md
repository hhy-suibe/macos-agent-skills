# macOS Agent Skills

给 AI 编程助手（Claude Code、ZCode、Codex CLI 等支持 SKILL.md 约定的工具）用的两个 Mac 技能。装好之后不用记命令，直接用中文对 AI 说需求：

* 「让天眼查直连，别走代理」→ [clash-rule](clash-rule/)
* 「帮我清理磁盘，先生成个清理报告」→ [disk-cleanup](disk-cleanup/)

## clash-rule：Clash Verge 分流规则管理

用 Clash Verge Rev 的人多半遇到过：某个国内网站突然要滑块验证或者打不开，原因是它被代理了。手动改配置有一堆坑：规则链文件三个键缺一个会被 Verge 静默忽略，运行时配置几十万字节，整体重排容易改坏。

这个 skill 让 AI 通过一个自带脚本完成所有操作（下例从仓库根目录出发；把脚本装进 PATH 后可直接写 `clash-rule`）：

```bash
clash-rule/scripts/clash-rule direct tianyancha.com    # 域名及全部子域直连
clash-rule/scripts/clash-rule proxy  openai.com        # 走代理
clash-rule/scripts/clash-rule reject  ads.example.com  # 屏蔽
clash-rule/scripts/clash-rule remove  tianyancha.com   # 撤销规则
clash-rule/scripts/clash-rule status                   # 诊断（global 模式架空、分组指错等常见病）
```

脚本同时写入订阅的规则链文件（订阅更新后规则仍保留）和运行时配置，然后自动热重载内核、断开这些域名的存量旧连接、打印生效验证。幂等、原子写入、改动前自动留 `.bak` 备份。

**环境**，macOS + Clash Verge Rev + python3（需 PyYAML）+ curl。代理分组默认名「🚀 节点选择」，和你的订阅不一致时用环境变量 `CLASH_RULE_PROXY_GROUP` 换。

## disk-cleanup：Mac 磁盘清理报告

扫描全盘常见垃圾位置（缓存、临时文件、日志、开发工具缓存、下载文件夹、微信等大目录内部），生成一个交互式 HTML 报告：每一项标了安全级别（绿=可直接删 / 黄=谨慎 / 灰=只讲是什么、不给删按钮），可以一层层下钻看明细。配套一个只监听 127.0.0.1 的小服务器，网页上点删除按钮就直接执行，默认进废纸篓，可以反悔。

安全设计是这个 skill 的核心：

* 扫描阶段只允许只读命令，AI 绝不主动删任何文件，删除只能由人在网页上点击
* 双层保护清单（构建时一层、执行时一层）：钥匙串、iCloud、iPhone 备份、聊天数据库、浏览器 Profile、.ssh 等凭据目录，命中即拒绝，哪怕报告数据配错也拦得住
* 每次删除都追加写日志，可回溯

**环境**，macOS + python3（系统自带即可）。node 可选，构建时用它校验 JS 语法，没有就自动跳过。

## 安装

前提：你的 AI 编程助手支持 skills 目录（Agent Skills / SKILL.md 约定），比如 Claude Code、ZCode。

```bash
git clone https://github.com/hhy-suibe/macos-agent-skills.git
cd macos-agent-skills

# 拷进你工具的用户级 skills 目录（以 Claude Code 为例；ZCode 是 ~/.zcode/skills）
mkdir -p ~/.claude/skills
cp -R clash-rule disk-cleanup ~/.claude/skills/
```

clash-rule 有一步可选：把脚本放进 PATH，终端里也能直接敲。macOS 默认 PATH 不含 `~/.local/bin`，第一次用需要先在 `~/.zshrc` 里加一行 `export PATH="$HOME/.local/bin:$PATH"`。

```bash
mkdir -p ~/.local/bin
cp clash-rule/scripts/clash-rule ~/.local/bin/ && chmod +x ~/.local/bin/clash-rule
```

不想装 skill 也没关系：两个 skill 里的脚本都是独立可用的命令行工具，SKILL.md 只是教会 AI 什么时候用、怎么用、哪些红线不能碰。

## 装好之后怎么用

对着 AI 说人话，比如：

* 「把 bilibili.com 改成直连」
* 「这个网站要滑块验证，帮我看看是不是被代理了」
* 「清理一下磁盘，先出个报告，删什么我自己决定」

## English Summary

Two macOS skills for AI coding agents that follow the SKILL.md convention (Claude Code, ZCode, etc.):

* **clash-rule**: manage Clash Verge Rev routing rules (direct / proxy / reject / undo / diagnose) through a battle-tested script. Writes to both the subscription merge file and runtime config, hot-reloads the core, drops stale connections, and verifies the result. Idempotent, atomic, with automatic backups.
* **disk-cleanup**: read-only disk scan that builds an interactive HTML cleanup report with safety tiers and drill-down details, plus a localhost-only server so the user can delete items from the web page (to Trash by default). A two-layer protected-paths blocklist guards system data and credentials at both build and execution time.

## License

[MIT](LICENSE)

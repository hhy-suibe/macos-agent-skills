# macOS Agent Skills

给 AI 编程助手（Claude Code、ZCode、Codex CLI 等支持 SKILL.md 约定的工具）用的两个 Mac 技能。装好之后不用记命令，直接用中文对 AI 说需求：

* 「让天眼查直连，别走代理」→ [clash-rule](clash-rule/)
* 「帮我清理磁盘，先生成个清理报告」→ [disk-cleanup](disk-cleanup/)

## clash-rule：Clash Verge 分流规则管理

代理开着，打开银行、学校图书馆或者某个国内网站，突然要滑块验证、要手机验证码，甚至直接打不开。你多半遇到过这种事。

原因通常不是网站坏了，而是它的流量被 Clash 送去了国外节点。网站一看访问者是外国 IP，就触发了风控。解决办法是告诉 Clash「访问这个网站时直连，别绕道」，也就是加一条分流规则。

加规则本身不难，难的是改对地方。手动改配置有三个坑：

* 有两层配置要同时改对：一层来自订阅（机场下发的），一层是正在运行的。只改运行层，订阅下次更新时你的规则就没了；只改订阅层，当下不生效。
* 正在运行的那层是个几十万字符的 YAML 大文件，格式极其讲究，手动改错一个缩进整个文件就废了。
* 就算都改对了，浏览器和网站之间的旧连接还挂着，不重连看不到效果。

这个 skill 让 AI 替你把坑全踩掉。你只管说人话，使用现场大概长这样：

> 你：「天眼查打开要滑块，帮我看看是不是被代理了」
> AI：（自动体检）查到 Clash 被切到了全局模式，你之前加的所有规则都不起作用。已切回规则模式 ✅ 天眼查已加直连 ✅ 验证生效

能交代给 AI 的事就这几类：「让 XX 直连」「XX 走代理」「屏蔽 XX 广告域名」「撤销上次给 XX 加的规则」「查查为什么我加的规则不生效」。

背后实际执行的是下面这些命令（从仓库根目录出发；装进 PATH 后可直接写 `clash-rule`）。想自己在终端敲，也一样用：

```bash
clash-rule/scripts/clash-rule direct tianyancha.com    # 这个域名和它所有子域名走直连
clash-rule/scripts/clash-rule proxy  openai.com        # 这个域名改走代理
clash-rule/scripts/clash-rule reject  ads.example.com  # 屏蔽（对付广告、追踪域名）
clash-rule/scripts/clash-rule remove  tianyancha.com   # 后悔了，撤销这条规则
clash-rule/scripts/clash-rule status                   # 体检：查为什么规则不生效
```

脚本背后做了什么不用记，说人话版就四条：

* **两层都写、写在安全的位置**，你的规则放在订阅之外，机场更新订阅冲不掉它
* **动手前先自动备份**，改坏了拿备份一还原就回来
* **改完立刻生效**，让正在运行的 Clash 马上重新加载，并掐断这个网站的旧连接，刷新页面就能看到效果
* **当场自证**，最后打印一行验证，告诉你这条规则是不是真的排进了生效名单

**环境**，macOS + Clash Verge Rev + python3（需 PyYAML，没有就 `pip3 install --user pyyaml`）+ curl（系统自带）。

两个常见问题：

* **proxy 命令报「分组不存在」？** 默认往「🚀 节点选择」这个组里送，中文订阅大多叫这个名。你的订阅分组名不一样时，先执行 `export CLASH_RULE_PROXY_GROUP="你的分组名"`。
* **加了规则网站还是不对劲？** 先跑 `status`。它专查几种疑难杂症：比如内核被切到了全局模式（所有分流规则整体失效，全部流量走一个节点），比如订阅的「全球直连」分组被指到了某个节点（所有本该直连的流量都被引去代理）。每查出一个问题，它都会附上对应的修复命令。

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

* **clash-rule**: proxy on, and a Chinese site suddenly demands CAPTCHAs? This skill lets your AI fix Clash Verge Rev routing with one command: make a site go direct / through the proxy / blocked, undo a rule, or run a health check that explains why a rule isn't taking effect (global-mode hijack, wrong group selection, etc.). The bundled script writes both config layers, hot-reloads the core, drops stale connections, and verifies the result. Idempotent, atomic, with automatic backups.
* **disk-cleanup**: read-only disk scan that builds an interactive HTML cleanup report with safety tiers and drill-down details, plus a localhost-only server so the user can delete items from the web page (to Trash by default). A two-layer protected-paths blocklist guards system data and credentials at both build and execution time.

## License

[MIT](LICENSE)

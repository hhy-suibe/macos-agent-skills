---
name: clash-rule
description: 管理 macOS 上 Clash Verge Rev 的分流规则——让某网站直连、走代理、屏蔽、撤销规则，以及诊断「某网站走了VPN/打开要滑块验证/被风控」类问题。用户提到 直连、不走代理、走代理、翻墙规则、Clash、Clash Verge、Verge、分流、加规则、订阅规则，或抱怨某国内网站被代理了时使用。一切操作通过本 skill 自带的 scripts/clash-rule 脚本完成，禁止手动编辑配置文件。
---

# Clash Verge 分流规则管理

适用于 macOS + Clash Verge Rev（其它代理客户端不适用）。管理分流规则一律调用本 skill 自带的脚本，下文以 `$CR` 代指其**绝对路径**（`<本skill目录>/scripts/clash-rule`；若已按仓库 README 装进 `~/.local/bin`，也可直接写 `clash-rule`）。脚本会同时写入①订阅的「编辑规则」链文件（订阅自动更新后仍保留）②运行时配置，然后自动热重载内核、断开该域名的存量旧连接、打印「生效验证」。它是幂等的，改动前自动留 `.clash-rule.bak` 备份。

**环境要求**：macOS、Clash Verge Rev、python3 + PyYAML（`python3 -c "import yaml"` 报错则先 `pip3 install --user pyyaml`）、curl（系统自带）。

**禁止手动编辑** `~/Library/Application Support/io.github.clash-verge-rev.clash-verge-rev/` 下的任何 yaml：规则链文件三个键（prepend/append/delete）缺一个会被 Verge 静默忽略；几十万字节量级的运行时配置做整体 YAML 重序列化风险高。脚本已经处理了这些坑。

## 任务 1：把某网站改为直连 / 走代理 / 屏蔽

1. 先确定**裸注册域**（DOMAIN-SUFFIX 自动覆盖所有子域）：
   - 用户给了网址 → 去掉协议、路径、`www.`，如 `https://www.tianyancha.com/company` → `tianyancha.com`。
   - 用户只给品牌名（如"天眼查"）→ 用 WebSearch 或 `curl -s --noproxy '*' -m 10 <猜的主页> | grep -oE 'https?://[a-zA-Z0-9.-]+' | sort | uniq -c | sort -rn` 找出页面引用最多的域名。属于该产品的辅助域名（追踪/CDN，如天眼查的 `tyc.io`）一并处理；拿不准归属的不要加。
2. 执行（一次可传多个域名）：
   ```
   $CR direct tianyancha.com    # 直连
   $CR proxy  example.com       # 走代理(默认组「🚀 节点选择」)
   $CR reject ads.example.com   # 屏蔽
   ```
   订阅的代理分组名不同时，用 `export CLASH_RULE_PROXY_GROUP=<分组名>` 覆盖默认值再执行 `proxy`。
3. 把「生效验证」结果转述给用户：行首 ✅ 即成功；⚠️ 表示未生效，如实报告。

## 任务 2：用户报告"国内网站被代理了 / 要滑块验证"

先诊断：`$CR status`
- 若输出含「内核处于 <global或direct> 模式」（脚本原文「内核处于 X 模式——所有分流规则被架空」）→ 这就是根因（**所有分流规则被整体架空**，全部流量走 GLOBAL 组当前节点；典型症状：国内网站返回 419/风控页）。执行 `$CR mode rule` 修复（自动同步内核/运行时文件/持久层三层，并断开 GLOBAL 链路的存量旧连接）。
- 若输出含「mode 三层不一致」→ 内核/运行时文件/持久层 mode 各不相同。加规则虽已被归一化保护，但 Verge 重启后以持久层为准，用 `$CR mode <目标值>` 对齐。
- 若输出含「分组当前是」且说的是「全球直连」分组、值不是 DIRECT（脚本原文「『全球直连』分组当前是 X——所有国内直连规则实际会被引到这个出口」）→ 根因是分组（订阅所有直连规则都汇到这个组）。执行 `$CR select 全球直连 DIRECT` 修复（组名可不带 emoji，脚本会自动匹配；「🚀 节点选择」「🎯 全球直连」是中文订阅的常见默认分组名，个别订阅不存在时该行只会显示问号，不影响其它诊断）。
- 否则按任务 1 给该域名加直连规则。

## 任务 3：撤销规则

`$CR remove tianyancha.com`（同时从链文件和运行时配置移除并热重载）。

## 其它

- 查看已有自定义规则：`$CR list`
- 切换内核出站模式：`$CR mode <rule|global|direct>`（三层同步+切回 rule 时断 GLOBAL 旧连接；脚本加规则时已自动防止把陈旧模式回灌内核）
- 切换任意分组选择：`$CR select <分组名> <选项名>`
- Clash 未运行时脚本会写好文件并提示「下次启动生效」——这是正常的，如实转述即可，不要尝试帮用户启动 Clash。
- 脚本只管理 DOMAIN-SUFFIX 规则；用户要 IP-CIDR、PROCESS-NAME 等其它类型时，先说明脚本不支持，再谨慎手工处理并让子 agent 对抗审查。
- 诊断「规则在但流量不对」类问题的终极证据是内核连接表：发一个经本机混合端口（见运行时配置 `mixed-port`）的请求，同时
  `curl --unix-socket <external-controller-unix 的值> -H "Authorization: Bearer <secret 的值>" http://localhost/connections`
  （这两个值都在 `~/Library/Application Support/io.github.clash-verge-rev.clash-verge-rev/clash-verge.yaml` 顶部）查该 host 的 `chains`（`['DIRECT']` 即真直连；含 `'GLOBAL'` 即被全局模式架空）和命中的 rule。

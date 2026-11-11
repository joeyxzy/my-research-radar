# Research Radar｜DeepSeek API 版｜每日技术简报 + 每周趋势追踪

为研究 VLA / Embodied AI / WAM / MLSys / GPU 推理系统的人设计。它从多种公开原始来源采集技术信号，**经过两阶段模型筛选与（尽可能的）正文补充后**发完整中文邮件，周报依据真实日报及跨周主题账本分析，而不是重新随机搜新闻。

> **状态**：这是可部署的代码模板，已通过 17 项离线模拟测试；**尚未使用你的 DeepSeek API Key、SMTP 凭据做端到端测试**。必须自行在私有仓库设置密钥后才能定时发送。GitHub Actions 是尽力调度，无法保证整点精确到达。

## ECNU 学生邮箱 SMTP 设置与验证（请先读）

**结论**：学生邮箱网页登录地址 `https://mail.stu.ecnu.edu.cn/` 是学校官方确认的，但学校 2026 年公开的**学生邮箱**说明没有确认具体 SMTP 域名、端口、客户端登录权限或授权码获取步骤。教职工邮箱官方公布的 `smtphz.qiye.163.com:994`（SSL）**仅适用于其教职工指南**；不要在未经验证的情况下声称该地址对学生邮箱有效。

1. 登录学生邮箱网页，在“设置 / 账号与安全 / 客户端设置”（实际名称可能不同）查看是否有 SMTP 登录权限和“生成客户端授权密码”。若有，开启并生成一个用于 Research Radar 的独立密码，切勿直接把学校统一身份认证密码上传到 GitHub。
2. 以学校学生邮箱界面提供的 SMTP 服务器、端口和加密方式为准；如果没有，联系 `its@ecnu.edu.cn` / 021-62233081，问“学生邮箱是否支持 GitHub Actions 云主机上的第三方 SMTP 发送？具体主机、端口、TLS 模式和应用授权密码在哪里获取？”
3. 在 GitHub `Settings → Secrets and variables → Actions → Secrets` 中创建 `SMTP_USER`、`SMTP_PASSWORD`、`TO_EMAIL`（均可用 `10235102432@stu.ecnu.edu.cn` 作前后两个邮箱值；密码使用**客户端授权密码**）。
4. 在同一页面 **Variables**（不是 Secrets）中设置 `SMTP_HOST`、`SMTP_PORT` 和可选 `SMTP_SECURITY`。只在学生端的说明或学校技术支持确认时，才填入相应地址；不要把学生邮箱的网页登录 URL 当作 SMTP 主机。`SMTP_SECURITY` 可取 `ssl` 或 `starttls`，默认 `auto` 会将端口 `465`、`994` 识别为隐式 TLS，其余端口使用 STARTTLS，永远不会明文认证。
5. 在 `Actions → Research Radar → Run workflow` 中选择 `report=smtp_test`（不需 DeepSeek API，不改归档），发一封测试邮件。先测通 SMTP，再运行真正的 `daily`。

如果学生邮箱没有 SMTP 权限，你仍然可以设置 `TO_EMAIL=10235102432@stu.ecnu.edu.cn`，用另一个支持 SMTP 的邮箱（如 QQ、163 或专用邮件服务）作为发件方。不能通过修改 Python 程序绕过学校服务器对 SMTP 的限制。

## 0. 默认日程（已配置，无需改）

- 每天 **09:00 北京时间**，发送 Daily Radar（含周日）
- 每周日 **20:00 北京时间**，发送 Weekly Review
- 采用 GitHub Actions 2026 年支持的 `schedule.timezone: Asia/Shanghai`。
- 两个定时任务均支持 `Actions > Research Radar > Run workflow` 手动触发；同时添加了 `report=smtp_test` 仅测试邮件发送，**不调用 DeepSeek API**，也可设置 `dry_run: true` 仅预览生成结果、不发送邮件及修改归档（**dry run 仍使用付费 LLM API**）。

## 1. 采集源和研究范围

**论文**：8 个 arXiv 类别：`cs.RO`, `cs.DC`, `cs.PF`, `cs.AI`, `cs.LG`, `cs.CV`, `cs.CL`, `cs.OS`；Hugging Face Daily Papers API（查询最近数天）。同一 arXiv 论文在 HF 和 arXiv 两处出现会合并。

**开源系统**：15 个 GitHub 仓库（Releases + 合并 PR），包括 OpenPI、LeRobot、Isaac GR00T、OpenVLA、vLLM、SGLang、FlashInfer、FlashAttention、PyTorch、Triton、TensorRT-LLM、CUTLASS、TorchAO、DeepSpeed 和 llama.cpp。不是只看发版；可以捕获没有发版但架构已经变化的 PR。

**博客与发现源**：HF Blog、NVIDIA Developer Blog、OpenAI News、PyTorch Blog、BAIR、SemiAnalysis、The Robot Report、3 个 Google News 定向 RSS。Google News 和行业新闻只能充当**发现线索**，不能直接当作一手技术证据。

全部可通过 `config.yaml` 修改。各站点 RSS/API 未来可能变动：程序会记录每个失败源，在日报末显示源异常数量；不会假装已经验证失效的站点。

**注意：** 模型优先根据论文摘要、开源项目说明、可抓取的 HTML 文本进行技术分析。部分论文没有 HTML 版本、部分博客禁止抓取，此时明确降级到 abstract/feed excerpt。它**不是**自动通读所有 PDF、全部 PR patch 或验证代码性能的 agent。

## 2. 部署（只需一次）

### 2.1 创建空的私有 GitHub 仓库

1. 打开 https://github.com/new ，仓库名例如 `my-research-radar`，选 **Private**。
2. 创建后把本 ZIP 的**所有内容**放在仓库根目录（不要多包一层 `research-radar-v2` 目录）。必须包含隐藏路径 `.github/workflows/research-radar.yml`。
3. 上传方式：推荐用 `git`，下面示例在解压后的项目根目录运行：

```bash
git init
git branch -M main
git add .
git commit -m "Init Research Radar"
git remote add origin git@github.com:YOUR_USERNAME/my-research-radar.git
git push -u origin main
```

如果 GitHub 仓库创建时已经初始化了 README，请先克隆该仓库，再把项目文件复制进去，而不要对非空远端直接 `git push`。

### 2.2 配置 API Key 和邮件发送凭据

在仓库 `Settings → Secrets and variables → Actions → Repository secrets` 设置：

| 密钥 | 填什么 | 备注 |
|---|---|---|
| `DEEPSEEK_API_KEY` | 在 https://platform.deepseek.com/api_keys 创建的 DeepSeek API Key | 与 ChatGPT 订阅无关；需要 DeepSeek API 余额 |
| `SMTP_USER` | 用于发信的完整邮箱地址 | 建议独立研究邮箱，也可个人 Gmail |
| `SMTP_PASSWORD` | SMTP **专用**授权凭据 | 不要填日常登录密码，不要粘贴到聊天里 |
| `TO_EMAIL` | 目标收件邮箱 | 可以等于 `SMTP_USER` |

对普通 Gmail 发件账号，默认 SMTP 为 `smtp.gmail.com:465`（SSL）；但对 `@stu.ecnu.edu.cn` 学生发件账号，程序会强制要求设置 `SMTP_HOST` 和 `SMTP_PORT`，避免把未确认的 ECNU 学生邮箱参数误设为 Gmail。Gmail 账户需要应用专用密码；ChatGPT 中连接 Gmail 不会自动把该授权传给 GitHub Actions。

如改用其他 SMTP 邮件服务，在 `Repository variables` 中设置 `SMTP_HOST`、`SMTP_PORT`，必要时设置 `SMTP_SECURITY`=`ssl` 或 `starttls`。支持 `465/994` 等隐式 TLS 端口及 `587` 等 STARTTLS 端口，前提是服务商明确允许对应方式和用户名/密码认证。

在 `Repository variables` 中按需设置（非必需）：

| 变量 | 默认值 | 意义 |
|---|---|---|
| `DEEPSEEK_MODEL` | `deepseek-v4-pro` | 日报和周报使用，启用 thinking（high） |
| `DEEPSEEK_SCREEN_MODEL` | `deepseek-flash` | 初筛与每周趋势账本，关闭 thinking 以节省成本 |
| `DEEPSEEK_BASE_URL` | `https://api.deepseek.com` | 官方 API 入口，通常无需修改 |

### 2.3 给 GitHub Actions 写入权限

仓库 `Settings → Actions → General → Workflow permissions` 选择 **Read and write permissions**（如果仓库设置允许），否则无法把生成的摘要写回私有仓库。

### 2.4 第一次测试

1. **先验证邮件**：打开 `Actions → Research Radar → Run workflow`，选择 `report=smtp_test`；它仅发送一封测试邮件，不花 DeepSeek API 费用，也不会更新历史记录。若学生 SMTP 尚未获批，测试会失败，不代表报告逻辑有问题。
2. **再测试模型和采集**：选择 `report=daily`, `dry_run=true`。检查 `Unit tests` 和 `Collect, analyze, and deliver` 日志。即使没有 SMTP 凭据，这一步也能检验模型、来源与报告内容。
3. 再运行一次 `report=daily`, `dry_run=false`；查看收件箱和垃圾邮件。成功后应出现 `archive/daily/YYYY-MM-DD.json` 和更新后的 `archive/seen.json`。
4. 在一周有足够日报积累后，选择 `report=weekly`, `dry_run=false`。每周更新 `archive/weekly/` 和 `archive/trends.json`。

初次手动在当天发送日报后，同一天的定时任务会检测已归档日报，**不重复发送**。如果邮件实际发送成功、但历史 `git push` 因仓库权限失败，则下一次可能重复发信，务必检查 Actions 日志。

### 2.5 建议：正式开启前检查这些路径

```text
.github/workflows/research-radar.yml
config.yaml
main.py
radar/collect.py
radar/report.py
radar/send.py
archive/seen.json
archive/trends.json
archive/daily/
archive/weekly/
tests/test_radar.py
```

## 3. 系统原理与可扩展性

1. **候选入池**：RSS、HF Daily Papers 和 GitHub 公共 API，根据时间窗口和类别收集，统一 ID 和日期，arXiv/HF 跨网站去重。
2. **平衡预筛**：核心领域 / 相邻领域 / 探索领域加权，同时保留多个来源的配额，避免全被 arXiv 或 GitHub PR 占据。
3. **模型初筛**：选择约 6–9 个最值得深挖的研究点；如果初筛模型失败，则降级为评分排序，日报里有 `selection_method` 可查。
4. **深挖**：对部分入选论文尝试读取 arXiv HTML，对博客尝试读取 HTML；获取不到会保留摘要并注明证据等级。
5. **日报**：分析机制、相对既有方法的变化、技术实验、可信度、局限、对机器人系统和 MLSys 的影响。`archive/daily` 保存入选材料和全文摘录的证据范围。
6. **防漏推**：只将**真正进入日报的材料**写入 `archive/seen.json`，未入选候选不会因为被扫描过而永久消失。去重索引保留 240 天。
7. **周报**：读取最近 7 天日报 + 之前 4 周的周报 + `archive/trends.json`，基于真实原文链接总结趋势、分歧、研究机会。
8. **趋势账本**：周报后更新 `archive/trends.json`，按稳定主题 slug 记录本周证据、方向、置信度、下一步研究问题。它是一份**证据的历史索引**，不是事实自动验证器。

## 4. DeepSeek 模型策略（默认已配置）

- API 地址：`https://api.deepseek.com`，使用 OpenAI SDK 的 Chat Completions 兼容接口。
- 初筛和趋势 JSON：`deepseek-flash`，使用非思考模式、JSON Output；避免较大的推理费用。
- 日报和周报：`deepseek-v4-pro`，启用 thinking（`high`）；程序只读取 `message.content`，不把 `reasoning_content` 写入邮件或归档。
- 如要全部使用经济型模型，在仓库 `Settings → Secrets and variables → Actions → Variables` 新建 `DEEPSEEK_MODEL=deepseek-flash` 即可。
- 模型名称以 https://api-docs.deepseek.com/zh-cn/ 为准。不要再使用已退役的 `deepseek-chat` 和 `deepseek-reasoner`。
- 两阶段分析会调用多次 API，计费和调用耗时取决于候选数量、正文长度、输出和 thinking 模式；建议首次先手动 dry-run 评估。

## 5. 常见问题

- **没收到邮件？** 先单独运行 `report=smtp_test`，查看 `SMTP-only delivery test` 日志；确认学生账号的 SMTP 权限、客户端授权密码、SMTP host/port/TLS 方式、垃圾箱。不要在日志里打印 Secrets。
- **周报没内容？** 必须先有日报归档；首次部署的第一个星期的周报可能只有部分天数，正常。
- **提醒邮件主题有结果但内容不够深？** 主要原因可能是原文只拿到摘要。可进一步加入 arXiv PDF 正文解析、OpenReview、PR patch 和作者博客技术报告，但需要更高的请求预算和来源质量控制。
- **费用？** GitHub 私有仓库使用 Actions 有计划配额，DeepSeek API 按 token 收费，SMTP 服务可能收费。实际成本随每天候选量、文章抓取量和模型选择变化；参考各平台官方计费页面。
- **RSS 挂了？** 程序会 warning；配置 `config.yaml` 删除、替换 feed URL 即可。
- **是否有严格 09:00 SLA？** 没有。GitHub Actions `schedule` 尽力启动，可能延迟。要求邮件准点送达需换稳定的托管调度平台。

## 6. 本地检查

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m pytest -q
# dry-run 仍然需要 DEEPSEEK_API_KEY（不需要 SMTP secrets）
export DEEPSEEK_API_KEY='YOUR_DEEPSEEK_KEY'
python main.py daily --dry-run
```

**安全提示**：仓库请保持 Private；`archive/` 中含私人研究关注点和可能较长的论文/博客片段。不要把凭据写进 `.env` 后误提交（`.gitignore` 已排除 `.env`）。不要把未授权的机密项目资料作为采集源。

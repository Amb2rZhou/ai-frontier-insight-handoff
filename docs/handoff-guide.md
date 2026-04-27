# AI Frontier Insight Bot — 内部交接文档

> 最后更新：2026-04-27
> 原维护者：Zhile Zhou (zhilezhou2026@u.northwestern.edu)
> 代码仓库：本仓库

---

## 一、这是什么

AI Frontier Insight Bot 是一个 **AI 前沿情报系统**，每天自动采集多源信息、用 LLM 提取关键信号、生成带洞察的日报，通过Hi群分发。

**现状**：v1 稳定运行 2 个多月（2026-02-25 至今），日报从未中断。跑在我的 Mac 上，launchd 定时触发。

**核心数据**：
- 每日采集 ~200+ 条原始信息（RSS + Twitter + GitHub + ArXiv + HuggingFace + Benchmarks）
- LLM 筛选出 10 条最有价值的信号 → 生成洞察
- 维护一个 60+ 页的 LLM-Wiki 知识库（公司/产品/技术/趋势时间线）
- 累计 8 期周报深度分析

---

## 二、Pipeline 原理

每天 09:30 自动执行的完整流程：

```
原始数据 (200+条)          LLM 筛选             LLM 分析              输出
┌──────────────┐     ┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│ RSS (27 feeds)│     │ 提取 10 条   │     │ 每条信号加上  │     │ 日报 markdown │
│ Twitter (200+)│────▶│ 最有价值信号  │────▶│ 洞察 + 启示  │────▶│ 日报 markdown │
│ GitHub (10)   │     │ 去重 + 过滤  │     │ 趋势总结     │     │ Git push     │
│ ArXiv (25)    │     └──────────────┘     └──────────────┘     │ → Seal 拉取   │
│ HuggingFace   │                                               └──────────────┘
│ Benchmarks    │
└──────────────┘
```

### Twitter 数据采集现状（重要）

Twitter 数据**不是通过 API 采集的**，而是用 Playwright 浏览器自动化爬取：

1. `x-monitor/` 是一个独立子系统，用 Playwright 无头浏览器登录 X 网页版
2. 定时打开预设的 X List 页面，滚动加载推文，解析 DOM 提取内容
3. 产出 JSON 文件存到 `data/x-monitor/{date}.json`
4. 主 pipeline 的 `src/collectors/twitter.py` **只是读取这些 JSON 文件**，本身不做网络请求

**为什么这么做**：之前没有申请 X API 的额度，所以用浏览器爬虫作为替代方案。

**已知问题**：
- 依赖本地浏览器环境，无法在无 GUI 的服务器上运行
- X 网页改版会导致 DOM 选择器失效
- 登录态过期需要手动重新登录
- 偶尔漏抓数据（页面加载不完整）

**v2 方案**：X API 已改为按量付费（~$66/月），应直接改用 API 采集，彻底替换 Playwright 方案。详见附录 D。

### 详细步骤

| Step | 代码位置 | 做什么 |
|------|---------|--------|
| 1 | `src/collectors/*.py` | 并行采集 6 类数据源，返回 `RawItem` 列表 |
| 2 | `src/analysis/signal_extractor.py` | 把所有原始信息丢给 LLM，提取 top 10 信号（含去重、论文过滤） |
| 3 | `src/analysis/insight_generator.py` | 对每条信号生成 insight（洞察）+ implication（启示）|
| 4 | `src/analysis/insight_generator.py` | 生成趋势摘要（5 条趋势要点） |
| 5 | `src/memory/manager.py` | 更新趋势追踪（trends.json），保存本周信号 |
| 6 | `src/wiki/updater.py` | 自动把今天的 insight 写入对应 wiki 页面的时间线 |
| 7 | `src/formatters/daily_markdown.py` | 格式化日报 markdown |
| 8 | `scripts/run_daily.sh` | Git commit + push 到 GitHub 仓库 |
| 9 | Seal（外部） | 每天从 GitHub 拉取新日报 → 写入 Redoc 空间 → webhook 发 Redoc 链接到企微群 |

### 信号筛选逻辑

核心 prompt 在 `prompts/signal_extraction.txt`。LLM 收到 ~200+ 条原始信息后：

1. **合并同一事件**的多条信息为一条 signal
2. 按 signal_strength (0-1) 打分，基于来源权威性、事件重要性、影响力
3. **跨天去重**：与最近 3 天已报信号的标题做相似度比对（word overlap ≥ 60%）
4. **论文过滤**：ArXiv 论文如果不是来自知名机构（Google/OpenAI/清华等），过滤掉
5. 取 top 10 输出

### LLM 调用

- `src/analysis/ai_client.py` 封装了所有 LLM 调用
- **当前模型**：DeepSeek V3（便宜，日均花费 < ¥1），备用 Anthropic Claude Haiku / Sonnet
- 优先用 `DEEPSEEK_API_KEY`，没有则 fallback 到 `ANTHROPIC_API_KEY`
- **v2 建议**：换成内部模型 API key，或直接让 Seal 等内部 agent 来执行 LLM 调用环节，不再依赖外部 API

### 分发流程（当前）

Pipeline 本身**不直接推送消息**，而是绕了一层 GitHub 中转。原因：Twitter 数据依赖 Playwright 爬虫，云端 IP 容易触发 X 的反爬机制，所以整个 pipeline 只能跑在本地 Mac 上；而 Seal（公司内部工具）无法访问本地文件，只能从 GitHub 拉取——因此形成了「本地生成 → push 到 GitHub → Seal 拉取」的迂回链路。

**v2 用 X API 后可以在云端跑 pipeline，就不再需要 GitHub 中转了。**

具体流程：

1. Pipeline 生成日报 markdown，`run_daily.sh` 自动 `git push` 到 GitHub 仓库
2. **Seal**（公司内部工具）定时从 GitHub 仓库拉取当天新增的日报文件
3. Seal 将内容写入指定的 **Redoc 空间**（内部文档平台）
4. Seal 通过 webhook 向 **Hi 群**发送 Redoc 文档链接

> 注：代码里的 `src/delivery/webhook.py` 和 `WEBHOOK_CHANNELS` 配置是早期直接推送方案的遗留，现已不使用。

**后续分发建议**：

v2 在云端跑 pipeline 后，不再需要 GitHub 中转。Seal 或其他 agent 可以直接读取本地产出的日报 markdown 文件，写入 Redoc 空间后通过 webhook 发链接到 Hi 群。整条链路变成：

```
云端 pipeline 生成日报 → Seal/agent 读取本地文件 → 写入 Redoc → webhook 发链接
```

也可以跳过 Redoc，直接用 webhook 推送日报全文（复用 `src/delivery/webhook.py`），取决于团队偏好。

---

## 三、代码结构

```
ai-frontier-insight/
├── config/
│   ├── settings.yaml          # 调度时间、模型选择、分析参数
│   ├── sources.yaml           # 所有数据源配置（RSS/Twitter/GitHub/ArXiv/HF）
│   └── drafts/                # 日报草稿 JSON（每天一个）
│
├── src/
│   ├── main.py                # CLI 入口，pipeline 编排
│   │                           命令：daily / send-daily / weekly / cleanup
│   │
│   ├── collectors/            # ── 数据采集层 ──
│   │   ├── base.py            # RawItem 数据类（所有 collector 的输出格式）
│   │   ├── rss.py             # RSS 并行采集（ThreadPoolExecutor, 90s 全局超时）
│   │   ├── twitter.py         # 读 x-monitor 产出的 JSON（非 API 直采）
│   │   ├── github_trending.py # GitHub Search API + Release 监控
│   │   ├── arxiv.py           # HuggingFace Daily Papers（非直连 ArXiv API）
│   │   ├── huggingface.py     # HF trending models/spaces
│   │   └── benchmarks.py      # 5 个 leaderboard 排行榜监控
│   │
│   ├── analysis/              # ── LLM 分析层 ──
│   │   ├── ai_client.py       # DeepSeek / Anthropic API 统一封装
│   │   ├── signal_extractor.py # Raw → Signals（去重、论文过滤）
│   │   └── insight_generator.py # Signals → Insights + Trends
│   │
│   ├── wiki/                  # ── Wiki 维护层 ──
│   │   └── updater.py         # ENTITY_MAP 关键词匹配 + 时间线插入
│   │
│   ├── memory/                # ── 持久化层 ──
│   │   └── manager.py         # 趋势/信号/benchmark 快照存取
│   │
│   ├── formatters/            # ── 输出格式化 ──
│   │   └── daily_markdown.py  # 日报 markdown 生成（支持分段发送）
│   │
│   ├── delivery/              # ── 推送层 ──
│   │   └── webhook.py         # RedCity webhook 封装
│   │
│   └── utils/                 # ── 工具 ──
│       ├── config.py          # YAML 配置加载
│       ├── draft.py           # 草稿存取、状态管理
│       ├── archive.py         # 历史数据归档 + 过期清理
│       ├── http.py            # robust_get()：requests + curl fallback
│       └── json_repair.py     # LLM 输出 JSON 修复（4 道容错）
│
├── prompts/                   # LLM prompt 模板（{变量} 占位符）
│   ├── signal_extraction.txt  # 信号提取 prompt
│   ├── insight_generation.txt # 洞察生成 prompt
│   └── trend_update.txt       # 趋势更新 prompt
│
├── data/
│   ├── daily/{date}/          # 每日产出：brief.json + sources.json + markdown
│   ├── weekly/                # 周报：W{n}.md + W{n}.json
│   └── x-monitor/            # Twitter 原始数据（x-monitor 子系统产出）
│
├── memory/
│   ├── trends.json            # 趋势追踪（23 条活跃趋势，含 key_events 时间线）
│   ├── weekly_signals.json    # 本周信号累积器（周报素材）
│   └── benchmark_snapshots.json # leaderboard 历史快照
│
├── wiki/                      # ── LLM-Wiki 知识库 ──
│   ├── index.md               # 索引页
│   ├── companies/ (16)        # OpenAI, Anthropic, Google, Meta, NVIDIA, xAI...
│   ├── products/ (12)         # Claude, GPT, Gemini, Qwen, Codex...
│   ├── technologies/ (5)      # Agent Frameworks, MCP, Computer Use...
│   ├── trends/ (18)           # AI Safety, Embodied AI, Open Source Models...
│   └── weekly-summaries/ (8)  # W10-W17 周报摘要
│
├── scripts/
│   └── run_daily.sh           # launchd 调用的入口脚本
│
├── x-monitor/                 # Twitter 抓取子系统（Playwright）
│   ├── monitor.py             # 浏览器自动化抓取 X List
│   ├── data/list_members.json # 追踪的 ~207 个 X 账号
│   └── data/pipeline/         # 每日推文快照
│
└── docs/
    └── handoff-guide.md       # 本文档
```

---

## 四、LLM-Wiki 设计理念

### 为什么不用 RAG

传统新闻 bot 只做信号推送，每条新闻是散点——无法回答「Meta 的 AR 战略发展脉络」这类纵向问题。RAG 检索的是文本片段，没有时间线和因果关系。

### LLM-Wiki 怎么工作

LLM 每天做两件事：
1. **写日报**（横向：今天发生了什么）
2. **更新 wiki 页面**（纵向：这个实体的历史演进）

每个实体页面有结构化的时间线，日积月累形成知识图谱。用户问「OpenAI 今年做了什么」，直接从 wiki 页面拉时间线回答，而非搜最近几天的新闻。

### 对比

| | RAG | LLM-Wiki |
|---|---|---|
| 存储 | 向量化文本片段 | 结构化 markdown 页面 |
| 查询 | 语义相似度检索 | 按实体/主题直接定位 |
| 时间线 | 无 | 每个实体按日期排列 |
| 交叉引用 | 无 | `[[wiki-link]]` 双向关联 |
| 维护方式 | 被动索引 | LLM 每天主动更新 |
| 可视化 | 无 | Obsidian 打开即可看知识图谱 |

### 实现

`src/wiki/updater.py` 中有一个 `ENTITY_MAP`（关键词 → wiki 页面的映射表），每天 insight 生成后自动匹配并追加时间线条目。新增实体需手动在 `ENTITY_MAP` 中添加映射。

---

## 五、v2 规划：交互 Lab 知识 Agent

### 目标形态

```
云端 Agent（内部服务器）
  ├── 数据采集：X API (付费) + RSS + ArXiv + GitHub + HuggingFace
  ├── LLM-Wiki：自动维护结构化知识库
  ├── 定时推送：日报/周报 → Hi RedCity webhook
  ├── 交互响应：@bot 提问/追问/查脉络/待办
  └── Wiki 可视化：Quartz 部署的 web 界面
```

### 与 v1 的关键变化

| 维度 | v1（现状） | v2（目标） |
|------|-----------|-----------|
| 运行环境 | 本地 Mac + launchd | 云端服务器 + cron/systemd |
| Twitter 数据 | x-monitor 浏览器抓取（不稳定） | X API 直接采购 |
| LLM 模型 | DeepSeek V3（外部 API） | 内部模型 API 或 agent 调用 |
| 交互能力 | 无，单向推送 | @bot 可交互（提问/追问/待办） |
| 知识管理 | trends.json + wiki 时间线 | 完整 LLM-Wiki + 向量索引辅助 |
| 覆盖领域 | AI 通用 | + AI 交互/产品/硬件（由新同事定义） |
| 前端基座 | 无 | OpenClaw 或类似 Agent 框架 |
| Wiki 查看 | 本地 Obsidian | Quartz 部署到内部域名 |

### 交互场景

| 场景 | 触发方式 | 行为 |
|------|---------|------|
| 提问 | @bot XX方向最近有什么进展？ | 查 wiki + 近期信号，综合回答 |
| 追问 | @bot 今天日报第3条展开讲讲 | 拉原始数据源，深入分析 |
| 脉络 | @bot XX产品的发展历程 | 从 wiki 产品页拉时间线 |
| 待办 | @bot 跟踪XX，有更新提醒我 | 写入 follow-up 列表，命中时主动推送 |

---

## 六、从 v1 到 v2 需要做什么

### Phase 1：云端迁移（最小可用）

- [ ] 内部服务器部署 Python 环境 + 依赖（`pip install -r requirements.txt`）
- [ ] 申请 X API 账号，重写 `src/collectors/twitter.py`（改为 API 直采）
- [ ] `ai_client.py` 改用内部模型 API（或让 Seal 等 agent 承接 LLM 调用）
- [ ] launchd → cron 或 systemd
- [ ] 配置分发：Seal 读取本地日报文件 → 写入 Redoc → webhook 发链接到 Hi 群
- [ ] 验证 pipeline 端到端运行

### Phase 2：数据源扩展

- [ ] 确定 lab 要覆盖的新领域（AI 交互/产品/硬件等）
- [ ] 添加对应的 RSS 源和 X 账号（参考附录 A、B）
- [ ] 设计筛选逻辑：多少条通用 AI、多少条新领域、如何平衡
- [ ] 调整 `prompts/signal_extraction.txt` 的 prompt

### Phase 3：Agent 交互层

- [ ] 部署 OpenClaw 或类似框架作为群聊 Agent 基座
- [ ] 对接Hi消息回调 API
- [ ] 实现 @bot 交互（wiki 查询 + RAG 补充）

### Phase 4：Wiki 可视化

- [ ] 用 Quartz 将 `wiki/` 生成静态网站
- [ ] 部署到内部域名

### 可直接废弃的 v1 组件

| 组件 | 原因 | 替代 |
|------|------|------|
| `x-monitor/` 子系统 | 依赖本地浏览器 | X API |
| `~/Library/LaunchAgents/com.ai-frontier-insight.*` | macOS 本地定时 | cron/systemd |
| `src/collectors/twitter.py` | 读本地 JSON 文件 | 新写 X API collector |
| `scripts/run_daily.sh` 中的 git push | 云端部署后 Seal 可直接读本地文件 | 不需要 |

### 可直接复用的核心代码

- `src/collectors/rss.py`, `github_trending.py`, `arxiv.py`, `huggingface.py`, `benchmarks.py`
- `src/analysis/` 全套（signal_extractor + insight_generator + ai_client）
- `src/wiki/updater.py`
- `src/formatters/`, `src/memory/`, `src/delivery/`
- `config/sources.yaml`, `config/settings.yaml`
- `wiki/` 60+ 页知识库（种子数据）
- `prompts/` 所有 prompt 模板

---

## 七、环境与配置

### 环境变量（`.env` 文件）

| 变量 | 说明 |
|------|------|
| `DEEPSEEK_API_KEY` | DeepSeek API 密钥（当前主力，日均 < ¥1） |
| `ANTHROPIC_API_KEY` | Anthropic Claude API（备用） |
| `WEBHOOK_CHANNELS` | RedCity webhook channel keys，JSON 格式 |

### 本地运行命令

```bash
cd ~/ai-frontier-insight

# 完整日报 pipeline
python -m src.main daily

# 发送日报（正式频道）
python -m src.main send-daily

# 发送到测试频道
python -m src.main send-daily --alert-only

# 清理旧数据
python -m src.main cleanup
```

### 当前定时任务

```
~/Library/LaunchAgents/com.ai-frontier-insight.daily.plist   # 09:30 采集+分析+发测试
```

实际流程：`run_daily.sh` → `python -m src.main daily` → `send-daily --alert-only` → `git push`

### Git 注意

全局 git config 配了 `http.proxy=127.0.0.1:7897`，梯子不开 push 会失败。绕过方式：
- `git -c http.proxy= -c https.proxy= push`
- 或改 remote 为 SSH

---

## 八、已知问题

1. **RSS 源失效**：`The Information` (403)、`ARC Prize` (404) 已不可用。定期检查 RSS 是否正常返回。
2. **去重不完美**：标题 word overlap ≥ 60% 才判重。同一事件换角度报道（如「X 发布 Y」vs「Z 集成 Y」）可能漏判。
3. **ENTITY_MAP 是静态的**：新公司/产品出现后需要手动在 `src/wiki/updater.py` 的 `ENTITY_MAP` 加关键词映射。
4. **周报是手动触发的**：目前用 Claude Code 的 skill 生成，未自动化。
5. **x-monitor 不稳定**：依赖 Playwright 浏览器自动化，偶尔抓不到数据。v2 必须换 X API。

---

## 附录 A：X 账号列表（~220 个）

以下是当前 X List 中追踪的账号，按类别整理。v2 采购 X API 时提供给服务商。

**文件位置**：`x-monitor/data/list_members.json`（完整 JSON）

### AI 公司官方

```
@OpenAI, @OpenAIDevs, @OpenAINewsroom, @ChatGPTapp,
@AnthropicAI, @claudeai,
@GoogleDeepMind, @GoogleAI, @GoogleAIDevs, @GoogleAIStudio, @GoogleLabs, @GoogleUK,
@AIatMeta, @MetaEngineering,
@xai,
@MistralAI,
@NVIDIAAI, @NVIDIARobotics,
@perplexity_ai,
@cursor_ai,
@huggingface,
@deepseek_ai,
@Alibaba_Qwen,
@ollama,
@__tinygrad__,
@arena (LMArena),
@ArtificialAnlys,
@theworldlabs,
@UnslothAI,
@openclaw,
@moltbook,
@NanoBanana (Google Gemini 图像模型),
@GeminiApp,
@allen_ai (Ai2),
@PyTorch, @TensorFlow,
@code (VS Code),
@surface (Microsoft Surface),
@azure
```

### AI 领域创始人 / CEO

```
@sama (Sam Altman, OpenAI CEO),
@DarioAmodei (Anthropic CEO),
@demishassabis (DeepMind CEO),
@mustafasuleyman (Microsoft AI CEO),
@satyanadella (Microsoft CEO),
@alexandr_wang (Meta AI / Scale AI founder),
@AravSrinivas (Perplexity CEO),
@amanrsanger (Cursor founder),
@hardmaru (Sakana AI CEO),
@brian_armstrong,
@levie (Box CEO),
@ilyasut (SSI),
@miramurati (ex-OpenAI CTO),
@PalmerLuckey (Anduril/Oculus founder),
@emostaque (Stability AI founder),
@ClementDelangue (HuggingFace CEO),
@RichardSocher (You.com CEO),
@Austen (GauntletAI founder)
```

### 顶级研究员

```
@geoffreyhinton, @Yoshua_Bengio, @ylecun,
@goodfellow_ian, @SchmidhuberAI, @HochreiterSepp,
@JeffDean, @koraykv,
@karpathy,
@johnschulman2, @woj_zaremba, @merettm,
@polynoamial (Noam Brown, o1/o3),
@alexalbert__ (Anthropic),
@janleike (Anthropic alignment),
@AmandaAskell (Anthropic),
@ibab (ex-xAI co-founder),
@chelseabfinn (Stanford, Physical Intelligence),
@svlevine (UC Berkeley, Physical Intelligence),
@drjimfan (NVIDIA Robotics),
@chrmanning (Stanford NLP),
@_jasonwei (Meta),
@ZoubinGhahrama1 (Google DeepMind VP),
@OriolVinyalsML (Google DeepMind VP),
@sedielem (Google DeepMind),
@giffmana (Meta, ex-OpenAI/DeepMind),
@shengjia_zhao (Meta),
@shaneguML (Google DeepMind, Gemini/Veo),
@LiamFedus (Periodic Labs, ex-OpenAI),
@lm_zheng (Meta, LMSYS co-founder),
@laterinteraction (Omar Khattab, MIT, DSPy),
@srush_nlp (Cursor, ex-Cornell),
@gneubig (CMU, OpenHands),
@zicokolter (CMU, GraySwanAI),
@natolambert (Ai2, RLHF),
@tri_dao (Princeton, FlashAttention),
@boazbaraktcs (Harvard),
@SebastienBubeck (OpenAI, ex-Microsoft),
@fchollet (Keras 创始人),
@dpkingma (Google),
@drfeifei (Stanford),
@andrewyng (Coursera/deeplearning.ai),
@jeremyphoward (fast.ai),
@rasbt (Sebastian Raschka),
@tdietterich (Oregon State),
@sarahookr (Adaption AI, ex-Cohere/Google),
@MattNiessner (TU Munich, Synthesia),
@ESYudkowsky,
@mmitchell_ai,
@MelMitchell1,
@GaryMarcus,
@docmilanfar (Google),
@Nils_Reimers (Cohere, SBERT 作者)
```

### 内容策展 / 媒体 / 投资

```
@_akhaliq (HF daily papers),
@ai__pub,
@omarsar0 (DAIR.AI),
@emollick (Wharton),
@lexfridman,
@dylan522p (SemiAnalysis),
@saranormous (Conviction VC),
@GavinSBaker (Atreides),
@stevenbjohnson,
@mervenoyann (HuggingFace),
@osanseviero (Google DeepMind),
@nielsrogge,
@risingsayak (HuggingFace),
@arankomatsuzaki
```

### 更多账号

```
@JustinLin610, @khoomeik, @DeryaTR_, @_mohansolo,
@alexgraveley, @thsottiaux, @Everlyn_ai, @mathemagic1an,
@hausman_k, @MatttThompsonn, @nickaturley, @PetarV_93,
@tobyphln, @sleepinyourhat, @littmath, @TLOgg,
@sirbayes, @OfficialLoganK, @math_rachel, @wightmanr,
@iamtrask, @chrszegedy, @CSProfKGD, @SashaMTL,
@nandodf, @thom_wolf, @fidjissimo, @jackclarksf,
@julien_c, @ctnzr, @ch402, @gdb, @hendrycks,
@npew, @nickcammarata, @mmbronstein, @miles_brundage,
@deliprao, @mitchellh, @a16z, @mattt
```

---

## 附录 B：待扩展的 AI 交互 / 穿戴 / 硬件数据源

以下源已调研验证可用，但**未启用**（留给新同事根据 lab 需求选择性添加）。

### RSS Feeds

| 名称 | URL | 领域 |
|------|-----|------|
| UploadVR | `https://www.uploadvr.com/feed/` | XR/VR 媒体 |
| Road to VR | `https://www.roadtovr.com/feed/` | XR/VR 媒体 |
| AR Insider | `https://arinsider.co/feed/` | AR 行业分析 |
| Glass Almanac | `https://glassalmanac.com/feed/` | 智能眼镜专题 |
| Auganix | `https://www.auganix.org/feed/` | AR/MR 技术 |
| KGOnTech | `https://kguttag.com/feed/` | 光学/显示技术深度 |
| Wareable | `https://www.wareable.com/feed` | 可穿戴设备 |
| Apple Newsroom | `https://www.apple.com/newsroom/rss-feed.rss` | Apple 官方 |
| Meta Newsroom | `https://about.fb.com/news/feed/` | Meta 官方 |
| Snap Newsroom | `https://newsroom.snap.com/feed` | Snap 官方 |
| Reddit AR | `https://www.reddit.com/r/augmentedreality/.rss` | AR 社区 |
| Reddit AR/MR/XR | `https://www.reddit.com/r/AR_MR_XR/.rss` | XR 社区 |
| Reddit Smart Glasses | `https://www.reddit.com/r/smartglasses/.rss` | 智能眼镜社区 |
| 36Kr | `https://36kr.com/feed` | 中文科技（质量参差，慎用） |

### X 账号（穿戴/AR 领域）

```
@XREAL_Global, @RokidGlobal, @rayneo_global,
@brilliantlabsAR, @Spectacles, @PICOXR,
@getVITURE, @EvenRealities,
@boztank (Meta CTO),
@anshelsag (XR 分析师),
@OscarFalmer (Smart Glasses Guide),
@RtoVR, @UploadVR, @ARealityEvent (AWE)
```

### GitHub Repos（穿戴/AR 开源）

```
brilliantlabsAR/frame-codebase
brilliantlabsAR/noa-assistant
BasedHardware/OpenGlass
```

---

## 附录 C：关键文件清单

交接时需要传给新同事的文件：

| 文件/目录 | 说明 | 是否在 Git 仓库里 |
|----------|------|-------------------|
| 整个 `ai-frontier-insight/` 仓库 | 完整代码 + 数据 + wiki | ✅ GitHub private repo |
| `.env` | API keys（DEEPSEEK / ANTHROPIC / WEBHOOK） | ❌ 需单独传 |
| `x-monitor/data/list_members.json` | X 账号完整列表（207 个） | ✅ 在仓库里 |
| `wiki/` | LLM-Wiki 知识库（60+ 页） | ✅ 在仓库里 |
| `memory/trends.json` | 趋势追踪数据 | ✅ 在仓库里 |
| `data/weekly/` | 8 期周报 | ✅ 在仓库里 |
| 本文档 | 交接说明 | ✅ `docs/handoff-guide.md` |
| v2 设计文档 | Agent 架构设计 | ❌ 在 `~/.clawd/work/wiki/projects/afi-v2-lab-agent.md` |

### Git 仓库获取

```bash
git clone <本仓库地址>
```

需要先将新同事的 GitHub 账号添加为 collaborator。

---

## 附录 D：X API 采购信息

**当前规模**：~220 个账号，每天拉一次推文

### 定价（2026-02 起改为按量付费）

X API 已于 2026 年 2 月取消固定月费制（Basic/Pro 仅限老用户续费），新用户统一使用 **Pay-Per-Use**：

| 操作 | 单价 | 备注 |
|------|------|------|
| 读取第三方推文 | $0.005/条 | timeline/search 每次返回 ≤20 条 |
| 读取自己的数据（Owned Reads） | $0.001/条 | 2026-04-20 起生效 |
| 发推（Write） | $0.015/条 | 含 URL 的推文 $0.20/条 |
| 月上限 | 200 万条读取 | 超过需走 Enterprise |
| 去重 | 同一条推文 24h UTC 窗口内多次请求只计 1 次 | |

**成本估算**：
- ~220 账号 × ~2 条/天 = ~13,200 条/月
- $0.005 × 13,200 ≈ **$66/月**（实际因去重机制更低）
- 预充值 credits，用多少扣多少，无固定费用

**注意事项**：
- 新用户注册即进入 Pay-Per-Use，开发者门户充值 credits 即可使用
- Likes/Quote-Posts/Following 等写入操作已从 self-serve 移除
- 还有 xAI credits 返利计划（累计消费最高返 20% 的 Grok API credits）

Sources: [X Dev Community 公告](https://devcommunity.x.com/t/x-api-pricing-update-owned-reads-now-0-001-other-changes-effective-april-20-2026/263025), [Pay-Per-Use 发布公告](https://devcommunity.x.com/t/announcing-the-launch-of-x-api-pay-per-use-pricing/256476)

### 需要的 endpoint

- `GET /2/lists/:id/tweets` — 按 List 拉推文
- `GET /2/users/by` — 批量把 username 转为 user ID（API 用 user ID）

**List ID**：`2026486577304842549`（原维护者 X 账号名下，需确认设为 Public 或自建新 List）

现有账号列表以 username 格式存储，API 那边可以自行转换。如果需要用新账号自建 List，可通过 `POST /2/lists` + `POST /2/lists/:id/members` 批量创建。

# Wiki Schema (Layer 3)

> 本文件定义 wiki 的结构规范。LLM 和 updater 脚本在维护 wiki 时必须遵循此 schema。

## 架构总览

```
Layer 1: Raw Sources (不可变)
  data/daily/{date}/brief.json    ← 每日信号（含 sources[] URL）
  data/daily/{date}/sources.json  ← 原始采集数据
  data/x-monitor/{date}.json      ← X/Twitter 原始数据

Layer 2: Wiki (LLM 维护)
  wiki/companies/*.md             ← 实体页面
  wiki/products/*.md
  wiki/technologies/*.md
  wiki/trends/*.md
  wiki/weekly-summaries/*.md

Layer 3: Schema (本文件)
  wiki/schema.md                  ← 定义页面结构、字段规范、引用规则
```

## 引用规则（Layer 1 ↔ Layer 2）

每条时间线条目必须标注来源，格式：
```
- **YYYY-MM-DD**: 事件标题 `[来源名](URL)` `[来源名2](URL2)`
```

来源信息从 `data/daily/{date}/brief.json` 的 `insights[].sources[]` 字段提取。

## 实体类型

### Company

```yaml
frontmatter:
  title: string        # 公司名
  type: company
  created: YYYY-MM-DD
  updated: YYYY-MM-DD
  tags: [string]
  aliases: [string]    # 别名（如 "DeepMind", "Google AI"）

sections:
  - 简介（1-2 句话）
  - "## Key Facts"      # 可选：CEO、总部、核心产品
  - "## Timeline"       # 按日期降序，每条带来源引用
  - "## Related"        # 可选：[[wiki-link]] 列表
```

### Product

```yaml
frontmatter:
  title: string        # 产品名
  type: product
  company: string      # 所属公司（wiki-link 格式）
  created: YYYY-MM-DD
  updated: YYYY-MM-DD
  tags: [string]

sections:
  - 简介（1-2 句话，含所属公司 wiki-link）
  - "## Timeline"
  - "## Related"
```

### Technology

```yaml
frontmatter:
  title: string        # 技术名
  type: technology
  created: YYYY-MM-DD
  updated: YYYY-MM-DD
  tags: [string]

sections:
  - 简介
  - "## Timeline"
  - "## Related"
```

### Trend

```yaml
frontmatter:
  title: string        # 趋势名
  type: trend
  trajectory: accelerating | stable | fading
  created: YYYY-MM-DD
  updated: YYYY-MM-DD
  tags: [string]

sections:
  - 简介
  - "## Timeline"
  - "## Related"
```

### Weekly Summary

```yaml
frontmatter:
  title: string
  type: weekly-summary
  week: YYYY-Wnn
  date_range: "YYYY-MM-DD ~ YYYY-MM-DD"
  theme: string
  created: YYYY-MM-DD

sections:
  - 主题与核心论点
  - 关键信号列表（带 wiki-link）
```

## 交叉引用规范

- 使用 `[[page-name|显示文本]]` 格式
- 每个 section 内每个实体只链接首次出现
- 不链接自身页面
- page-name 使用文件名（不含 .md）

## 关系类型

| 关系 | 方向 | 示例 |
|------|------|------|
| develops | Company → Product | OpenAI → GPT |
| competes_with | Company ↔ Company | OpenAI ↔ Anthropic |
| built_on | Product → Technology | Claude Code → MCP |
| tracked_by | Trend ← Signal | AI Safety ← "Anthropic drops safety pledge" |

关系通过 wiki-link 和 Timeline 条目隐式表达，不单独存储。

## 页面生命周期

1. **创建**：新实体首次出现在 daily signal 中时，由 updater 自动创建
2. **更新**：每日 pipeline 通过 ENTITY_MAP 匹配，追加时间线条目（带来源）
3. **归档**：30 天无新条目的页面标记为 inactive（暂未实现）

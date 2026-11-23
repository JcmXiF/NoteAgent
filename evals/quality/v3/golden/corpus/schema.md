# Wiki Schema — NOTEAGENT 默认

## 三层结构

| 层 | 目录 | 说明 |
|----|------|------|
| 原始资料 | `raw/sources/` | 不可变源文件 |
| Wiki | `wiki/` | LLM 生成与维护的 Markdown |
| 规则 | `purpose.md` / `schema.md` | 方向与页面类型约定 |

## 页面类型

| type | 目录 | 用途 |
|------|------|------|
| source | `wiki/sources/` | 资料摘要页 |
| entity | `wiki/entities/` | 具名对象（人、系统、产品、命名算法实现等） |
| concept | `wiki/concepts/` | 抽象概念、定义、定理、方法、性质 |
| query | `wiki/queries/` | 待研究问题 |
| comparison | `wiki/comparisons/` | 对比分析 |
| synthesis | `wiki/synthesis/` | 综合总结 / 主线 |
| overview | `wiki/overview.md` | 全局概要（由应用维护） |

## 理论 / 课程笔记指引（D-020）

默认 entity/concept 更贴「对象世界」。对线性代数、概率、RL 理论等课程笔记：

1. **优先 concept**：定义、定理、性质、推导对象、抽象方法（如「最小二乘」「正交投影」）落 `wiki/concepts/`。
2. **少造 entity**：不要把「向量」「矩阵」「特征值」等普通数学术语硬标成 entity。仅当源文强调**具名系统 / 产品 / 人物 / 特定命名算法实现**（如 PageRank、Jacobi 方法作专名条目）时用 `wiki/entities/`。
3. **强化综合面**：多源矛盾、主线串联、开放问题 → `comparison` / `synthesis` / `query`；避免为凑类型拆碎页。
4. **type 服务导航**：标错可人工改 type / 挪目录；检索与回答应以标题、正文、双链、`sources[]` 为准，不要把 type 当真理边界。
5. **页面类型目录在代码里**：换领域时改 `noteagent.wiki.page_types`（init / index / overview / generation 共用），并同步编辑本文件的说明。不要只改这一份 Markdown 却指望分类自动跟上。

## Frontmatter

```yaml
---
type: concept
title: 标题
created: YYYY-MM-DD
updated: YYYY-MM-DD
tags: []
related: []
sources: ["raw/sources/示例.md"]
---
```

- 每个内容页必须以 `---` 开闭 YAML；缺开头 `---` 会被写盘层尝试修复。
- `sources` 使用相对**项目根**的 source identity，不用绝对路径；多源 merge 时由应用 union，模型不必重写完整列表。
- 正文交叉引用优先 `[[concepts/page-slug]]`（带目录前缀），避免只用中文标题当链接目标。

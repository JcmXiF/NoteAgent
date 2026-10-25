---
name: write-wiki
description: 用户要规划或编译本次笔记时使用。先 propose_topic_map，必须停在人闸；ingest_confirmed 只写 clips.json 里已勾选的 write，不能自己发明 confirmed 列表。
---

# 从 Chat 写 wiki（过人闸）

目标是走现成主题闸：规划 ≠ 成页。不要当文件编辑器，不要写 `raw/`。

## 怎么做

1. 没有画框集合时，不要调用 `write`。请人先在原文上画框，收入候选列表。
2. 用户要规划 / 看本次写哪些页时，调用 `write` `action=propose_topic_map`。可带 `intent`（一句粗意图，例如「这批导论」）；**不填也可以**，此时从已选集合归纳篇目。它只更新 `clips.json` 里的主题图，**不写 wiki**。不要因为 intent 为空就叫人先起一个笔记标题。
3. 规划之后必须停下。请人在候选栏勾选「写 / 更新」「跳过」「仅在正文提及」，或直接点「按勾选编译」。
4. 只有人已经勾选并落盘之后，才调用 `write` `action=ingest_confirmed`。工具会忽略你编造的 confirmed 列表，只编译 clips.json 里 `action=write` 的页。
5. 若 observation 带 `needs_human`，最终回答说明还差哪一步，不要假装已经写盘。
6. 写完后用 `[[slug]]` 点出更新了哪些页；资料摘要保留 `sources/` 一级。

## 不要做的

- 不要把搜索卡片、fetch 预览或模型散文当成资料写进 wiki。
- 不要为未勾选的相关概念建页或写预习笔记。
- 不要调用 shell，也不要直接改磁盘上的 markdown。
- 不要在同一轮里规划完立刻 `ingest_confirmed`，除非 observation 已经显示存在落盘的 write 勾选。

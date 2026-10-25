---
name: query-wiki
description: 回答当前项目 wiki 里的问题时使用。先看 index，不够用 search scope=wiki 找页，再 wiki_read 打开正文；不要编造页面，也不要读 raw。
---

# 查当前项目 wiki

目标是根据已经编译好的笔记回答，不是去网上搜，也不是改 wiki。

## 怎么做

1. 先读本轮请求里的 `wiki/index.md`。列页、打招呼、空库，index 够就直接答。
2. 名字对不上 index 时，调用 `search` 且 `scope=wiki`，按 slug / 标题子串找页。命中后再 `wiki_read`。
3. 需要正文时调用 `wiki_read`，参数用 index 或搜索命中里的 `[[slug]]`。一次可以开多页，但不要超过预算。
4. 打开后仍不够，再 `wiki_read` 别的命中页，或在回答里说明缺哪一页。
5. 提到页面时用 `[[slug]]`，不要只写标题；资料摘要保留 `sources/` 一级。
6. 规程不清楚时才调用 `skill` 再读本文件；不要每轮都加载。
7. 用户明确要网上资料、或 wiki 没有对应页时，才 `search` `scope=web`。卡片不是正文，不要当笔记引用。
8. 用户问 wiki 健康 / 断链清单时，才 `run` `job=doctor`。它只报告，不改文件。
9. 用户明确要求打开某个公开 URL 时，才 `run` `job=fetch`。预览不是 raw，也不是 wiki；不要因为搜索卡片就去 fetch。
10. 用户要规划或编译本次笔记时，改用 `write-wiki` 技能；查 wiki 正文仍走本规程，不要在问答里调用 `write`。

## 不要做的

- 不要假装读过或写过 `raw/`。
- 不要发明 wiki 里没有的页面。
- 不要把思维过程写进最终回答。
- 不要把网页 snippet 或 fetch 预览写成 wiki 正文。

# 编码标准：测试替代不了的判断

这份文档只写**判断规则**：机械可查的规则放到 source-scan 测试里，这里不重复。
每条都指向一个已经踩过或评审过的坑，以及背后的 ADR。

## 写作任务（Task）

- **所有 Task 写都走 `TaskWriter`。** 内容写用 `commit_content`（递增 rev、清掉绑定旧 rev 的 approval/request）；op 状态、cancel、resume 标记、interrupted 用 `commit_log`（不递增 rev）。不要直接改 `Task` 再落盘。
- **下一内容 rev 只有一个来源：`TaskWriter.next_rev(task)`。** request、approval、失败 completion、`resume_rev` 都从它派生；service 里出现手写的 `task.rev + 1` 就是回退。
- **批准不是一句“同意”。** 它绑定 frameId、canonical PageSpec、rev 与 `pageDigest`；frame、spec、rev 或页面内容任一变化都使批准失效。`approve` 只由人的路由调用，agent 永远没有 approve 或 save_page 工具。
- **锁序是页锁 → Task 锁。** executor 在锁外 compose，最终保存在页锁内重读持久化 op、approval/frame/spec/rev 与页面 digest，再写页并写回 Task；人工保存的 `edit` 也在页锁内记录。不要反过来先拿 Task 锁再等页锁。
- **保存期间到达的 cancel 是合法结果。** 最终保存发现 op 已取消时，接受已写页面并记录结果，不要假装没写、也不要回滚页面。
- **resume 是否已投递，看 session 里的 `task_op_result`（按 opId 去重）。** `resumeAttempt` 只记尝试次数；append 前崩溃可以重试，session 删除等永久失败记 `resumeError` 且不再重试。agent 最终只看到一份 delta。

## 类型与形状

- **类型化不能改变 wire 形状。** 新增内存类型时，`*_to_dict` / `*_from_dict` 是唯一序列化缝；task.json、路由返回和工具 meta 的键保持原样，旧文件必须可读。需要迁移就单独开票，不要夹带。
- **边界校验要按对象的真实身份来。** 例如材料 id 是 `raw/web/...` 路径，含斜杠与点，不能用 task id 的 `is_task_id` 校验；校验过严会把所有真实数据静默跳过。
- **读不出的一行，不要拖垮整份状态。** 与 `_frames` 一致：单行解析失败就跳过并保留 Task 可读；整份 Task 损坏才走 `task.unreadable`，且不自动重建。

## 测试

- **通过 `AppDeps` 注入（ADR-0035）。** 测试替身从应用边界进入；不要 monkeypatch `TaskService` 的方法或模块级函数，否则测的是实现而不是行为。
- **在最高 seam 断言行为。** Task 的往返、视图与工具 meta 用模型 / API / 工具 seam；不要为了检查内部结构去读私有字段。
- **新增形状时先写往返测试。** 一个 round-trip 测试同时锁住键、默认值与裁剪；这比逐字段断言更难被实现细节带走。

## 文档与决策

- **代码、ADR、契约文档同一次改。** 改了写入语义就更新对应 ADR 与 `docs/tool-contract.md` / `docs/interface-transport.md`；`docs/` 是 gitignored 的本地文档，评审时必须显式检查它们。
- **ADR 只记录难以回退、且未来读者会问“为什么”的决策。** 惯例与流程写进这份标准，不占 ADR。
- **本地语义 guard：** `tests/check-docs.py` 会校验 ADR 索引有文件、Task 工具表与注册名一致、observation 白名单与代码一致；本地 docs 不在 CI 里，CI 会跳过这些检查。

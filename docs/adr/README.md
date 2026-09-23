# 架构决策记录（ADR）索引

本目录记录 novel-deconstruct 中**以后改起来很贵**的决策。每个 ADR 是一份不可变的历史记录：
决策被推翻时不删旧文，而是新增一个 ADR 并在旧文顶部标注 Superseded by。

## 格式

每份 ADR 包含：**状态**（Proposed / Accepted / Deprecated / Superseded）、**上下文**（约束与事实）、
**决策**、**后果**（含主动放弃的方案）。文件命名：`NNNN-kebab-case-title.md`，编号只增不复用。

## 索引

| 编号 | 标题 | 状态 |
|---|---|---|
| [0001](0001-record-architecture-decisions.md) | 记录架构决策 | Accepted |
| [0002](0002-deterministic-splitting-and-fake-first.md) | 确定性切分与 Fake-first 双轨 | Accepted |
| [0003](0003-kanban-flow-and-hybrid-pr-policy.md) | 持续流 Kanban 与混合 PR 纪律 | Accepted |
| [0004](0004-rag-tiers-and-adoption-gates.md) | RAG 分层与引入门（L0–L3，凭 Recall 金标解冻） | Accepted |

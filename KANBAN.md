# KANBAN · 持续流看板

> 流程依据：[ADR-0003](docs/adr/0003-kanban-flow-and-hybrid-pr-policy.md)
> 拉动规则：**Backlog → Doing（WIP 上限 1）→ Review → Done**；没有 Issue 编号的工作不进入 Doing。
> 远程仓库未启用前，本文件是看板真源；GitHub 仓库启用后，按下列编号逐一创建 Issue 并迁移到 GitHub Projects，编号保持不变。

## Backlog（待开始，按优先级排序）

| # | 标题 | 用户故事（一句话） | 关键验收 | 标签 |
|---:|---|---|---|---|
| #7 | 启用 GitHub 远程仓库 | 作为维护者，我要把本地仓库推到 GitHub 并启用 CI 与分支保护 | `gh repo create` + 推送；main 分支保护必需 CI；Issues 按本看板编号创建；CI 徽章上 README | type/chore priority/high |
| #8 | PyPI 发布准备 | 作为使用者，我想 `pip install novel-deconstruct` 而不是克隆源码 | 核名 ndecon/novel-deconstruct；tag 触发 release workflow；sdist+wheel 校验；版本与 CHANGELOG 一致 | type/chore priority/medium |
| #4 | Stage 4 设定与角色档案 | 作为使用者，我想自动汇总世界观/金手指/角色（含功能定位） | 同名不自动合并；别名归一带置信度；硬事实可 grep 回原文 | type/feature priority/low |
| #9 | 本地 ruff/pre-commit 启用 | 作为维护者，我要在网络恢复后让本地 lint 与 CI 一致 | pip 源恢复；pre-commit install；存量代码 ruff 清零 | type/chore priority/low |

## Icebox（不承诺排期，触发条件满足才进 Backlog）

- #6 Stage 5 汇总报告 / Stage 6 文风分析（依赖 #3/#4 完成后的真实产物验证）
- #10 短篇拆书管道（长篇流程连续自用 4 周后另开，绝不与长篇混库）
- #11 持续学习层（拆书卡片库/跨书检索/与本人稿件差距对照）
- #12 GUI / TUI（CLI 被证明阻碍真实使用后再议）
- #13 向量检索（先证明 BM2V0 的 Recall 不足，才允许引入）

## Doing（WIP = 1）

- （空）

## Review

- （空）

## Done

| # | 标题 | 完成证据 | 日期 |
|---:|---|---|---|
| #1 | v0.1 竖切：split/analyze/Fake/双产物 | 21 测试全绿；戏神 40 章 dogfood 40/40 一致；v0.1.0 tag 待打 | 2026-09-22 |
| #5 | 仓库工程流程（Issue/PR 模板、CI、ADR、Kanban、社区文件） | 首次提交即含全套流程 | 2026-09-22 |
| #2 | OpenAI 兼容 provider + 证据锚定 | 36 测试全绿（含 8 个 MockTransport 契约场景）；429 重试/401 速败/非法枚举拒绝/无证据情节点丢弃均有测试 | 2026-09-23 |
| #3 | Stage 3 确定性跨章聚合 | 41 测试全绿；戏神 40 章 1600 情节点 dogfood 产出节奏.md/aggregation.jsonl；统计可从 JSONL 完全重算 | 2026-09-23 |

# KANBAN · 持续流看板

> 流程依据：[ADR-0003](docs/adr/0003-kanban-flow-and-hybrid-pr-policy.md)
> 拉动规则：**Backlog → Doing（WIP 上限 1）→ Review → Done**；没有 Issue 编号的工作不进入 Doing。
> **GitHub 远程已启用（2026-09-23）：https://github.com/good-saltfish/novel-deconstruct**
> Issue 编号已与本看板锁定（#1–#13 已在 GitHub 创建）；后续本文件仅作速览，以 GitHub Issues/Projects 为准。

## Backlog（待开始，按优先级排序）

| # | 标题 | 用户故事（一句话） | 关键验收 | 标签 |
|---:|---|---|---|---|
| #14 | 首次发布：配置 PyPI trusted publisher 并发布 v0.1.0 | 作为使用者，我要 pip install novel-deconstruct | 需账号持有者在 PyPI 配 OIDC publisher；打 v0.1.0 tag；验证 pip 安装。工程侧已就绪（见 #8） | type/chore priority/high |
| #16 | 章节正文生成与章节自评（面板第二轮） | 作为作者，我想在确认的细纲上生成章节正文并得到自评 | 依赖 #15 骨架；正文为候选可编辑；引用细纲/人设一致性校验；openai-compat 面板接入 | type/feature priority/high |
| #17 | L1 一致性 RAG：bigram BM25 + Recall@10 金标基线 | 作为作者，我希望生成章节时自动带回本书前文的摘要/角色/伏笔，保证前后一致 | 见 ADR-0004：纯 Python 中文 bigram BM25 离线可跑；只索引本书不索引参考书原文；确定性上下文包（前章摘要/角色卡/活跃伏笔/叙事债）；CC0 金标集 + Recall@10 JSON 报告；#16 消费该上下文包 | type/feature priority/high |
| #4 | Stage 4 设定与角色档案 | 作为使用者，我想自动汇总世界观/金手指/角色（含功能定位） | 同名不自动合并；别名归一带置信度；硬事实可 grep 回原文 | type/feature priority/low |
| #9 | 本地 ruff/pre-commit 启用 | 作为维护者，我要在网络恢复后让本地 lint 与 CI 一致 | ruff 0.16.8 已装、存量零告警；pre-commit install 待执行（HTTPS git 需走 SSH 改写） | type/chore priority/low |

## Icebox（不承诺排期，触发条件满足才进 Backlog）

- #6 Stage 5 汇总报告 / Stage 6 文风分析（依赖 #3/#4 完成后的真实产物验证）
- #10 短篇拆书管道（长篇流程连续自用 4 周后另开，绝不与长篇混库）
- #11 持续学习层深化（#15 已实现最薄的"聚合数字注入"；跨书卡片库/检索/差距对照仍留 backlog）
- #12 GUI/TUI（#15 已交付基础本地 Web 面板；仅当面板被证明不足再升级形态）
- #13 向量混合检索（L2；按 [ADR-0004](docs/adr/0004-rag-tiers-and-adoption-gates.md) 引入门：先证明 #17 L1 BM25 的 Recall@10 不足，才允许解冻）

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
| #7 | GitHub 远程仓库启用 | 仓库公开、SSH over 443 推送、五个 CI 检查全绿（3.10/3.11/3.12+ruff+hygiene）、main 分支保护、#1–#13 Issue 编号锁定 | 2026-09-23 |
| #8 | PyPI 发布准备 | PyPI 核名可用；sdist+wheel 构建与 twine 校验通过；全新 venv 装 wheel 后 CLI 冒烟通过；CI package job + Release workflow（OIDC，手动可仅构建演练）；docs/releasing.md。首次实际发布见 #14 | 2026-09-23 |
| #15 | 本地项目面板：拆书入库+结构化创作 | `ndecon panel` 回环 Web 面板；导入自动拆书不存原文；五部件骨架 Fake 生成+draft/confirmed 分层；参考书聚合数字注入；56 测试全绿；真机四路径冒烟；静态资源入 wheel 并 CI 断言 | 2026-09-23 |

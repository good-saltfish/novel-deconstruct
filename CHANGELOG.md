# Changelog

本项目遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/) 规范，
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [Unreleased]

### Added

- 仓库工程流程：Issue/PR 模板、GitHub Actions CI、pre-commit、ADR、Kanban 看板、CONTRIBUTING。
- **OpenAI 兼容 Provider（#2）**：`--provider openai-compat`，支持 `NOVEL_DECON_API_KEY`/`OPENAI_API_KEY`、自定义 `OPENAI_BASE_URL` 与 `--model`；JSON 结构化输出、429/5xx 指数退避重试、错误四分类（配置/HTTP/响应/Schema）。
- **证据锚定器**：模型只给 quote 文本，字符偏移由本地三级匹配（精确→NFKC 空白滑窗→去标点滑窗）唯一定位；无法定位的情节点丢弃计数，绝不伪造证据。
- Provider 诊断披露：HTTP 重试次数、丢弃情节点、截断章节、未锚定可选 quote。
- 版本化 prompt（`oc-v0`），受控词表与枚举同源。
- **Stage 3 跨章聚合（#3）**：纯确定性统计——逐章主导基调与基调分布、爽点章节与章距（均值/最大空窗）、全书主题分布、角色出场矩阵（章号去重+提及计数）；产物 `剧情/节奏.md` + `data/aggregation.jsonl`，可从 chapters.jsonl 完全重算。
- **开源发布（#7）**：仓库公开于 GitHub；CI 五检查全绿（Python 3.10/3.11/3.12 + ruff + 仓库卫生）；main 分支保护；Issue #1–#13 编号锁定；CI 徽章上 README。行长按中文全角宽度设为 120。

## [0.1.0] - 2026-09-22

### Added

- 确定性中文章节切分器：阿拉伯/中文数字（含两、千、万）、章/回/节、二合一大章；preamble 保留；章号重复与不连续告警。
- Stage 0 章节索引（正文字数 + SHA-256 内容指纹）。
- Stage 2 逐章结构化摘要：情节点（基调 10 值 / 主题标签 12 值受控词表）+ 章内字符偏移证据。
- Stage 1 黄金三章报告（学习层 takeaways 强制留空，工具不代笔）。
- Provider 协议与确定性 FakeProvider（离线、可重放，标记 `rule:fake`）。
- Markdown + JSONL 双写产物（JSONL 为真源，Markdown 可重建）。
- CLI：`ndecon split`、`ndecon analyze --provider fake`、`version`、`doctor`。
- 21 个测试（切分/模型纪律/端到端契约/CLI 冒烟）。
- dogfood 验证：《我不是戏神》前 40 章切分边界/标题/行号/字数与人工核验表 40/40 一致。

[Unreleased]: https://example.com/compare/v0.1.0...HEAD
[0.1.0]: https://example.com/releases/tag/v0.1.0

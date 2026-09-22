# Changelog

本项目遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/) 规范，
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [Unreleased]

### Added

- 仓库工程流程：Issue/PR 模板、GitHub Actions CI、pre-commit、ADR、Kanban 看板、CONTRIBUTING。

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

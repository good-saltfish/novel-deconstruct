# novel-deconstruct（ndecon）

[![CI](https://github.com/good-saltfish/novel-deconstruct/actions/workflows/ci.yml/badge.svg)](https://github.com/good-saltfish/novel-deconstruct/actions/workflows/ci.yml)
[![License: Apache-2.0](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](#)

本地优先的**中文网文拆书 CLI**：喂给它一本小说的文本文件，它自动完成章节切分、逐章结构化摘要与黄金三章拆解，产出人读的 Markdown 与机读的 JSONL。模型可插拔——没有 API key 时用内置确定性 Fake Provider 也能跑通整条管道与测试。

> 状态：v0.1 开发中。v0.1 只覆盖"章节切分 + Stage 0 索引 + Stage 2 逐章摘要 + Stage 1 黄金三章报告"；跨章聚合、设定关系、文风分析在 backlog。

## 为什么做它

拆书是网文作者最重要的学习手段，但手工拆一本长篇以小时计。本项目把拆书流程中**确定性高、可结构化**的部分工程化：

- 章节边界由纯代码切分（绝不让 LLM 决定边界，保证可复现）；
- LLM 只负责语义摘要，输出受受控词表与证据字段约束；
- 每条情节点都带章节内字符偏移与原文 quote，可回原文定位；
- 所有产物双写：JSONL 是落盘真源，Markdown 可随时重建。

## 隐私与版权

- 默认完全本地运行；调用远程模型需要你显式配置自己的 API key。
- 版权正文不会被内置进分发包、不会随遥测上传（项目无遥测）。
- 仓库测试夹具只包含 CC0 自造文本。

## 安装（开发模式）

```powershell
git clone <your-fork>
cd novel-deconstruct
D:\Python\Python310\python.exe -m pip install -e ".[dev]"
D:\Python\Python310\python.exe -m pytest
```

## 快速开始

```powershell
# 1) 只切章节（纯本地，不需要模型）
ndecon split 我的小说.txt --out .out/mybook

# 2) 完整拆解（v0.1 用内置 Fake，离线可跑）
ndecon analyze 我的小说.txt --out .out/mybook --provider fake

# 3) 真实语义拆解（OpenAI 兼容接口，使用你自己的 key）
#    base_url 默认 https://api.openai.com/v1，可指向任何兼容端点
setx NOVEL_DECON_API_KEY "sk-..."          # 或 OPENAI_API_KEY
ndecon analyze 我的小说.txt --out .out/mybook --provider openai-compat --model gpt-4o-mini
```

产物：

```
.out/mybook/
├── 概要.md                # 章节索引与全书概要
├── 章节/
│   ├── 第0001章_摘要.md       # 逐章结构化摘要
│   └── 第0001章_深度拆解.md   # 黄金三章报告（仅前 3 章）
├── 剧情/
│   └── 节奏.md            # Stage 3：基调曲线/爽点章距/主题分布/角色矩阵
└── data/
    ├── chapters.jsonl     # 机读真源：每章一行
    ├── reports.jsonl      # 黄金三章结构化报告
    └── aggregation.jsonl  # 跨章确定性统计（可从 chapters.jsonl 重算）
```

## 设计纪律

1. **确定性外壳**：切分、枚举、偏移、hash 由代码保证；模型不能自由发挥字段值。
2. **候选不代笔**：拆书产物是"记录层"候选，"为什么有效/可迁移技巧"留给使用者自己写。
3. **证据可追溯**：quote + 字符偏移 + content_hash，程序可验证 quote 在同 hash 原文中的位置。
4. **可重放**：每条产物带 provider / prompt_version / schema_version。

## 参与开发与工程流程

本项目按标准 GitHub 开源流程运作：

- 看板（持续流 Kanban，Backlog/Doing/Review/Done）：[KANBAN.md](KANBAN.md)
- 开发环境、分支纪律、Conventional Commits、DoD：[CONTRIBUTING.md](CONTRIBUTING.md)
- 架构决策记录：[docs/adr/](docs/adr/README.md)
- 版本变更：[CHANGELOG.md](CHANGELOG.md)
- 发布手册（PyPI Trusted Publishing）：[docs/releasing.md](docs/releasing.md)
- 行为准则：[CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)；安全问题：[SECURITY.md](SECURITY.md)

## 致谢与方法论来源

本项目的拆书产物 schema 与受控词表，参考了个人工作流中的 story-long-analyze 方法论（章节边界表、情节点结构、基调/主题标签词表）。本仓库全部代码为 clean-room 独立实现，不含其任何文件。

## 许可证

Apache-2.0

# Changelog

本项目遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/) 规范，
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [Unreleased]

### Changed

- **本地 pre-commit 启用（#9）**：`.pre-commit-config.yaml` 改为 `repo: local` + `language: system` 直接调用本机 ruff 0.16.8，不再 clone GitHub（适配断网/SSH-only 环境）；pre-commit 4.6.2 已安装并 `pre-commit install`，`pre-commit run --all-files` 通过；CONTRIBUTING 安装说明同步。

### Added

- **L0.5 学习型写作知识库（#21，ADR-0005）**：新增 `ndecon.kb`——扫描本地写作资料目录，方法论/教学/素材（md/txt/docx，docx 用 stdlib zipfile 零依赖、GBK 回退）确定性切块建 bigram BM25 索引，落盘工作区 `kb/index.json` + `manifest.json`（逐文件收录/排除原因审计）；版权小说原文多重硬排除（`原文/` 目录、书名-作者命名、前40章/单章/采集缓存、txt >1MB、章节标题启发式），启发式对自研分析类文件名跳过（真实 dogfood 修正误伤）。CLI `ndecon kb index/search`；骨架生成（Fake/openai-compat/面板）自动检索注入带来源的"写作方法论参考"，正文生成不接入；无索引时完全降级。真实资料库实测：收录 254 文件/33142 块、排除 114 文件。新增 22 个测试（共 129 全绿）。
- 调研报告《RAG 用于长篇写作：开源项目与学术论文全景》（docs/research/2026-09-25-rag-writing-landscape.md）：10 个开源项目（星标/许可证一手核验）+ 12 篇论文详录、横向对比、对 L0–L3 决策的外部验证与缺口建议（写后投影/矛盾检测/L2 技术栈佐证）。
- 仓库工程流程：Issue/PR 模板、GitHub Actions CI、pre-commit、ADR、Kanban 看板、CONTRIBUTING。
- **OpenAI 兼容 Provider（#2）**：`--provider openai-compat`，支持 `NOVEL_DECON_API_KEY`/`OPENAI_API_KEY`、自定义 `OPENAI_BASE_URL` 与 `--model`；JSON 结构化输出、429/5xx 指数退避重试、错误四分类（配置/HTTP/响应/Schema）。
- **证据锚定器**：模型只给 quote 文本，字符偏移由本地三级匹配（精确→NFKC 空白滑窗→去标点滑窗）唯一定位；无法定位的情节点丢弃计数，绝不伪造证据。
- Provider 诊断披露：HTTP 重试次数、丢弃情节点、截断章节、未锚定可选 quote。
- 版本化 prompt（`oc-v0`），受控词表与枚举同源。
- **Stage 3 跨章聚合（#3）**：纯确定性统计——逐章主导基调与基调分布、爽点章节与章距（均值/最大空窗）、全书主题分布、角色出场矩阵（章号去重+提及计数）；产物 `剧情/节奏.md` + `data/aggregation.jsonl`，可从 chapters.jsonl 完全重算。
- **开源发布（#7）**：仓库公开于 GitHub；CI 五检查全绿（Python 3.10/3.11/3.12 + ruff + 仓库卫生）；main 分支保护；Issue #1–#13 编号锁定；CI 徽章上 README。行长按中文全角宽度设为 120。
- **PyPI 发布准备（#8）**：包名 novel-deconstruct 核验可用；补全发布元数据（classifiers/项目链接/关键词，PEP 639 SPDX 许可证表达式）；`python -m build` 产出 sdist+wheel，twine 校验通过；全新 venv 安装 wheel 后 `ndecon` 入口冒烟通过；CI 新增 package 构建校验 job；Release workflow 支持 tag 触发 OIDC 可信发布与手动仅构建演练；发布手册见 docs/releasing.md。
- **本地项目面板（#15）**：`ndecon panel` 启动仅绑定 127.0.0.1 的 Web 工作台（stdlib http.server，零前端框架）；导入小说自动跑拆书管道入库（只存源路径与聚合 JSON，不复制原文）；新建长篇可挂参考书，FakeCreator 一键生成题材定位/首卷纲要/前 10 章细纲/主角人设/金手指（强制限制代价），学习注入仅传参考书聚合数字统计；模型产出为 draft、用户保存为 confirmed；新增 workspace/creation/panel 三个模块与 15 个测试（共 56 个全绿），面板静态资源打入 wheel 并经 CI 断言。
- **ADR-0004 RAG 分层与引入门（#17）**：确立 L0 聚合数字（现状）→ L1 中文 bigram BM25 + 确定性上下文包（#17 启动，须随附 CC0 金标集与 Recall@10 基线）→ L2 向量混合（#13 冻结，金标不达标才解冻，限 sqlite-vec/numpy 且离线回退 L1）→ L3 实体图谱事件投影（#4 之后）的分层架构；通用引入门要求先有评测再加依赖、Fake-first 离线回退、学习型检索只出聚合数字不出原文。
- **章节正文生成与结构化自评（#16）**：`creation.chapters` 新增章节生成器（prompt 版本 `cw-v0`），唯一输入为 #17 的 `ContextPack`——本书定位/人设/金手指+本章细纲+前章摘要+BM25 回顾+开放伏笔，参考书与源路径在结构上无法进入 prompt；FakeChapterWriter 离线确定性占位（明确横幅标记不可直接使用，含前情承接/伏笔照应），OpenAICompatChapterWriter 复用拆书 JSON 通道；同步产出结构化自评（钩子强度/信息密度 1-5、是否贴合细纲、偏差点清单），只报告不改写。面板每章卡片新增生成（Fake/openai-compat）/查看/编辑/确认；正文落盘 `projects/<id>/manuscripts/chNNN.md`（候选/确认分层，编辑保存回候选、空正文 422），元数据与自评进 project.json；删除项目递归清理。新增 11 个测试（共 107 个全绿）。
- **L1 一致性检索（#17）**：新增 `ndecon.retrieval` 模块——中文 bigram 分词（NFKC 归一，零词典零模型）、确定性 Okapi BM25（k1/b 固定、受控词表查询加权、JSON 可往返重算）；索引器只接受创作项目且只收已确认骨架部件 + 本书已写章节摘要，参考书类型级拒入、`SourceRef.quote` 结构性不读取；伏笔台账（只登记显式章尾钩，固定阈值字面重合判回收，不臆造）；`ContextPack` 确定性组装前章摘要（written 优先、outline 退化）+ top-k 相关回顾（排除本章）+ 开放伏笔。CC0 自造 12 章金标语料 + 16 条查询，**Macro Recall@10 基线 0.875**（14 条字面查询全中，2 条纯语义改写落空——bigram 已知短板，作为 #13 解冻依据），基线报告提交入库且 CI 逐字段比对重算结果；新增 `ndecon reindex` 命令与工作区产物白名单写入；共 96 个测试全绿。

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

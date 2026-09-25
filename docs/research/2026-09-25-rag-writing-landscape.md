# RAG 用于长篇写作：开源项目与学术论文全景报告

- 调研日期：2026-09-25
- 调研人：项目维护者（good-saltfish）
- 调研方法：GitHub Search API（星标/许可证/描述为当日实时数据）+ README 一手阅读 + arXiv API 摘要核验；未采信二手榜单文章的转述
- 调研动机：为 [ADR-0004](../adr/0004-rag-tiers-and-adoption-gates.md) 的 RAG 分层（L0–L3）与 #4/#13/#16 后续决策提供外部证据
- 数据口径：星标为 2026-09-25 抓取快照，会随时间变化；许可证字段取自 GitHub API 的 `license.spdx_id`

---

## 0. 执行摘要

**这个领域已经从"prompt 工程"收敛为一个稳定的工程范式：**

```
作者确认设定/大纲
      │
      ▼
逐章生成 ◄──── 检索组装：角色卡 / 世界观 / 前章摘要 / 相关回忆 / 开放伏笔
      │
      ▼
章节提交（CHAPTER COMMIT）
      │
      ├─► 派生摘要（summaries）
      ├─► 派生结构化状态（角色状态 / 关系 / 物品）
      ├─► 更新检索索引（BM25 / 向量，增量）
      ├─► 更新记忆（伏笔台账 / 弧线节点 / 读者认知）
      └─► 审稿（自检清单 / 独立审稿 Agent / 矛盾检测）
      │
      ▼
作者审阅确认（模型建议不得直接成为权威状态）
```

四条在多个独立项目中重复出现的共识：

1. **模型产出必须过人审闸门**——3000★ 的 AI-Novel-Writing-Assistant、1100★ 的 AI-Novel-Writer 与我们的 draft/confirmed 分层殊途同归；
2. **检索分两路**：关键词/全文检索（FTS/BM25）做无依赖兜底，向量检索做语义召回——AI-Novel-Writer 明确"未配 embedding 时降级 SQLite FTS"，与我们 L1→L2 的引入门一致；
3. **结构化状态（角色/伏笔/弧线）是独立于向量库的一等公民**——goink 用 31 个工具让 Agent 自主维护，webnovel-writer 用五路派生；纯语义相似度无法回答"伏笔回收了没有"这类状态查询；
4. **"写后维护"是当前竞争焦点**——写完一章自动触发状态回灌与审稿，是头部项目共同的最新投入方向，也是 ndecon 当前的功能缺口。

**对 ndecon 的位置判断**：我们的 L0（聚合数字注入）→ L1（bigram BM25 + 确定性 ContextPack）→ 章节生成消费上下文，与主流范式同构且更保守（证据锚定、版权类型级隔离、无 key 可跑）。缺口在写后自动投影（L3 CHAPTER_COMMIT）与跨章矛盾检测（SCORE 式）。

---

## 1. 开源项目详录

### 1.1 写作工作台型（流程编排 + 人审闸门）

#### ① ExplosiveCoderflome/AI-Novel-Writing-Assistant — 3.0k★，NOASSERTION

- 仓库：https://github.com/ExplosiveCoderflome/AI-Novel-Writing-Assistant
- 技术栈：LangGraph（Agent 编排）+ Express/Prisma + Qdrant（向量库）+ SQLite（默认存储）
- 定位：面向"完全不懂写作的新手"的 AI Native 整本生产系统，从一句灵感走到完整小说
- RAG/记忆做法（一手 README 核验）：
  - **拆书结果、知识库文档、写法引擎都作为可召回长期资产**，通过 RAG 回灌到规划/续写/正文三个阶段；
  - 拆书产物入 **facets 索引**，召回内容包含"拆书结论"而非原文；
  - 索引流式并行（Embedding 与 Qdrant 写入并发可调）、**chunk hash 去重**防重建产生重复向量、**retrieval trace 后端**可追踪"为什么命中"；
  - 默认 SQLite 即可跑通主链，**需要 RAG 时才接 Qdrant**（与我们"分层付费"同构）；
  - token 预算守卫、章节证据回溯。
- 生产链：方向 → 世界 → 角色 → 卷战略/卷骨架 → 节奏/拆章 → 章节执行（写作→审计→修复→状态回灌）→ 整本生产任务（可暂停可恢复）
- 衍化：配套 [ani-book-skill](https://github.com/ExplosiveCoderflome/ani-book-skill)（Codex 本地工作区 skill 形态）、漫画工作台（角色形象一致性）
- 与 ndecon 对照：
  - 同：拆书结论（而非原文）入检索、默认存储与 RAG 解耦、章节链可恢复；
  - 异：它是重栈（Qdrant + LangGraph + Prisma），我们是 stdlib + pydantic；它学习型检索直接吃拆书全文结论，我们只允许聚合数字（更保守的版权姿态）。
- **许可证 NOASSERTION：不可直接引用代码，只能学思想。**

#### ② EthanYoQ/AI-Novel-Writer — 1.1k★，GPL-3.0

- 仓库：https://github.com/EthanYoQ/AI-Novel-Writer
- 形态：Windows/macOS 桌面工作台；模型 BYOK（Ollama 本地或自定义 OpenAI 兼容端点）
- 创作流程：前提 → 角色 → 世界观 → 章节蓝图 → 草稿 → 审稿 → 修稿 → 定稿
- 与人审/连续性相关的关键设计：
  - **Proposal 收件箱**：模型生成的建议先回填本地编辑表单，**只有用户明确审核应用，权威项目状态才改变**（V2 的核心机制）；
  - **连续性材料按来源分级**：作者填写的角色资料 / 模型提炼的动态状态 / 旧项目未知来源信息三者不混同，后续写作优先使用带来源的定稿原文；
  - **分层章节材料**：本章任务、尚未发生的计划、定稿历史、候选稿分别呈现；相关原文保留相邻段落，以承接"伤因/否定/物品转交"等跨句信息；
  - 生成失败恢复：可见正文存为"恢复候选"，非正式草稿，源蓝图变化后失效；
  - **检索降级**：可导入参考文本做知识库；**未配置 embedding 时仍可用 SQLite FTS 全文检索**；
  - 剧情树是**可重建的只读快照，不取代作者事实**；章节定稿不自动改写文风配置。
- 与 ndecon 对照：Proposal 闸门 = 我们 draft/confirmed 的同构设计；"只读派生快照不取代作者事实" = 我们 ADR-0004 中"JSONL 真源、图谱只是派生视图"；FTS 降级 = 我们 L1 无 embedding 兜底。是外部项目中与我们哲学最接近的一个。
- GPL-3.0：只能 clean-room 学思想。

#### ③ iLearn-Lab/NovelClaw — 375★，MIT（许可证可兼容）

- 仓库：https://github.com/iLearn-Lab/NovelClaw（有中文 README，在线平台 colong-idea-studio.cloud）
- 技术栈：Python 3.10+ / FastAPI
- 定位原话："不是一次性提示词封装，而是把长篇创作整理成一个可检查的写作工作区：sessions、storyboard、manuscript 视图、角色与世界观界面，以及可编辑的 memory bank"
- 记忆与状态：
  - manuscript / world / character / **memory bank 全部落盘工作区且可人工编辑**；
  - 显式状态机：`global_outline`（全局大纲落盘）→ `chapter_outline_ready` → `chapter_plan`（本章写作计划）→ `chapter_length_plan`（目标长度及来源）→ `memory_snapshot`（记忆快照刷新）→ `character_setting / world_setting`（设定记忆回写）；
  - 工作面分工：Chat 推进会话、Runs 观察 worker.log/progress.log、Manuscript/Storyboard/Memory Banks 做续写与审阅；
  - 核心判断：**长篇质量不只取决于模型能否写出文本，更取决于写作、审阅与记忆控制是否被组织成持续工作空间**。
- 与 ndecon 对照：memory bank 可编辑 ≈ 我们伏笔台账/ContextPack 的人工可改方向；其状态机比我们细（长度计划、记忆快照），可作为 #4/L3 的流程参考。
- **MIT：可直接参考代码（仍需保留许可声明）。**

#### ④ heider-x/vela — 574★，GPL-3.0

- 仓库：https://github.com/heider-x/vela
- 定位：隐私优先、本地优先的 AI 写作 IDE（网文向），BYOK（OpenAI/DeepSeek/Gemini/Claude/Ollama/智谱）
- 架构：better-sqlite3（关系型）+ 轻量向量引擎；世界观/角色档案含**跨章节动态状态追踪**；大纲→起草→智能重写→自动审阅全流程与本地 RAG 知识库融合；支持**模型智能分流**（DeepSeek 写大纲、Claude 润色、本地模型做隐私审查）
- 与 ndecon 对照："写作/润色/检索分模型"的分流思路可借鉴（我们目前每阶段单 provider）；SQLite + 轻量向量与 ADR-0004 的 L2 限定栈一致。
- GPL-3.0：只能 clean-room 学思想。

#### ⑤ FlickeringLamp/ai-novelist — 224★，MIT

- 仓库：https://github.com/FlickeringLamp/ai-novelist
- 定位：vibecoding 写作领域尝试，功能含 function calling、RAG、MCP、skills、人在回路（自称"写作界小 cursor"）
- 检索模式明确区分两种：
  - **两步 RAG**：调用 AI 前先以用户消息检索相关信息，连同上下文一并返回；
  - **Agentic RAG**：给 AI 两个知识库工具，由模型自主决定是否查询。
- 向量库：ChromaDB
- 与 ndecon 对照：我们的 ContextPack 是"两步 RAG"（确定性组装，模型无查询权）；Agentic RAG 是 L3 之后可考虑的能力（让模型主动查伏笔/角色），但与"确定性、可重算"红线有张力，引入需谨慎评估。
- MIT：可参考代码。

### 1.2 Agent 记忆引擎型（结构化状态是核心）

#### ⑥ lingfengQAQ/webnovel-writer — 约 7.2k★（2026-09-23 数据），GPL-3.0

- 仓库：https://github.com/lingfengQAQ/webnovel-writer
- 核心架构：章节提交后**五路派生视图**——state / index / summaries / memory / vector，下一章生成前多路组装
- 检索：Qwen3-Embedding-8B + Jina rerank（重栈）
- 行业地位：目前中文网文写作 Agent 中星标最高、架构最完整的参考实现
- GPL-3.0：只能学思想；重嵌入栈与我们离线红线冲突，不直接采用。

#### ⑦ YILING0013/AI_NovelGenerator — 约 6.1k★（2026-09-23 数据），AGPL-3.0

- 仓库：https://github.com/YILING0013/AI_NovelGenerator
- ChromaDB 向量库；大纲→分章→生成→评审的多 Agent 链路
- AGPL-3.0：网络服务也触发开源传染性，仅作思想参考。

#### ⑧ sigpanic/goink — 375★，AGPL-3.0（技术路线与 ndecon 最接近）

- 仓库：https://github.com/sigpanic/goink
- 形态：桌面 AI 写作系统（Tauri/WebKit），SQLite 单机
- 问题意识原话："通用 AI 写长篇，到第五章忘了主角叫什么，到第三十章要手动翻前文找伏笔"
- 核心机制（一手 README）：
  - **31 个结构化工具**，LLM Agent 自主决定调用哪个/传什么参数——不是固定 pipeline，而是在对话中查角色、查伏笔、读写正文、更新状态；
  - 结构化创作状态：角色档案（性格/能力/背景）、**有向关系图**（"张三对李四师徒但暗中提防"与"李四对张三敬重但有所隐瞒"是两条独立记录，**关系变化保留历史**）、**伏笔台账**（每条伏笔记录目标回收章节与重要程度，临期提醒、超时未回收标异常）、3–5 条并行弧线（节点链关联目标章节，写完自动推进）、地点关系、读者认知；
  - **写后自动注入维护清单**：写完一章系统强制 Agent 逐项自查（角色变化/伏笔回收/弧线推进/读者认知刷新），"不会忘了维护"；
  - **独立审稿子 Agent**：从头审读章节内容与系统状态的一致性，发现问题写进对话，主 Agent 当场修正；
  - **本地语义搜索**：bge-small-zh-v1.5（int8 量化）ONNX 本地推理 + **sqlite-vec** 向量索引 + MMR 去冗余重排；写完章节后台增量索引，无网络。
- 与 ndecon 对照：
  - goink 的伏笔台账（目标回收章 + 临期/超时提醒）是我们 `foreshadow.py` 的自然增强方向（我们目前只有开启章+实际回收章，无计划回收章）；
  - sqlite-vec + 本地量化 bge 的生产组合，**与 ADR-0004 对 L2 的限定栈（sqlite-vec/numpy、禁常驻服务、离线回退）完全一致**，#13 解冻时可直接照此 A/B；
  - "写后维护清单 + 独立审稿 Agent" = 我们建议的 L3 CHAPTER_COMMIT 具体形态。
- AGPL-3.0：只能 clean-room 学思想。

#### ⑨ EternityJune25/ComoRAG — 347★，MIT，AAAI 2026 Poster

- 仓库：https://github.com/EternityJune25/ComoRAG
- 论文：[arXiv:2508.10419](https://arxiv.org/abs/2508.10419)
- 学术原型开源：认知启发的有状态长篇叙事推理 RAG；传统 RAG 的"无状态单步检索"无法捕捉长篇中动态演变的实体关系，ComoRAG 在推理卡壳时进入**迭代循环**：生成探测性查询→检索→与动态记忆工作区整合→巩固，类似人脑"获取新证据 + 巩固旧知识"的认知过程
- 与 ndecon 对照：它解决"读懂长篇并推理"（分析侧），我们解决"写长篇时保持一致"（创作侧）；其**动态记忆工作区 + 探测查询迭代**模式可启发 L3：生成前若发现矛盾信号，允许二次检索求证而非一次组装定稿。
- MIT：可参考代码。

#### ⑩ ZulutionAI/RaCig — 10★，Apache-2.0

- 仓库：https://github.com/ZulutionAI/RaCig
- RAG-based Character-Consistent Story **Image** Generation：用 RAG 保证故事图像生成中的角色一致性
- 与 ndecon 关系：文本无关，但"角色一致性资产锚定"思路与 AI-Novel-Writing-Assistant 的角色形象图引用一致；若未来做衍生视觉工坊可参考。Apache-2.0 与我们许可证兼容。

### 1.3 生态观察：中文"小说+RAG"内容的错配

掘金/CSDN 等平台 2026 年的中文高热教程，绝大多数是**把小说当知识库做问答**（典型链路：《天龙八部》EPUB → LangChain RecursiveCharacterTextSplitter（500 字/50 字重叠）→ Milvus → 语义 QA），解决的是"读懂/问答既有小说"，而非"辅助创作新小说"。

- 这意味着**"一致性型创作 RAG"在中文开源实操内容中仍是空白带**，成熟玩家集中于上述十余个项目；
- 但这些教程沉淀了可复用的工程常识：按章节天然边界加载、段落语义边界切分、chunk 重叠防断句、复合主键（bookId_chapterNum_chunkIndex）、Collection 名不一致导致静默空召回等——#13 做切块时直接适用。

---

## 2. 学术论文详录

### 2.1 故事生成专用 RAG / 记忆

#### P1. GROVE: A Retrieval-augmented Complex Story Generation Framework with A Forest of Evidence

- arXiv：https://arxiv.org/abs/2310.05388（EMNLP 2023 Findings）
- 作者：Zhihua Wen, Zhiliang Tian, Wei Wu 等
- 问题：条件故事生成中，过细的 prompt 会限制创造力；如何既满足目标条件又增加情节复杂度与可信度
- 方法：
  1. 按目标条件构建**范例故事检索库**，few-shot 召回人类优秀片段；
  2. **"asking-why"提示方案**：对生成内容反复追问"为什么"，抽出多层背景动机，构成"证据森林（forest of evidence）"；
  3. 迭代挑选最契合的证据链融入故事。
- 与 ndecon 关系：这是**学习型检索（我们的 A 类）**的学术形态——直接检索他人作品片段。我们的版权红线（只注入聚合数字、不注入原文/quote）比它保守；其"asking-why 挖动机背景"可用于我们 Stage 1 黄金三章的分析提示设计（分析侧，不涉版权注入）。

#### P2. SCORE: Story Coherence and Retrieval Enhancement for AI Narratives

- arXiv：https://arxiv.org/abs/2503.23512（2025-03 提交，2025-09 已 v6，持续修订）
- 作者：Qiang Yi 等 21 人（产业界大团队）
- 问题：LLM 长篇叙事的一致性与情感深度难以维持
- 方法：
  - **追踪关键物品状态（key item statuses）**；
  - 生成**集次摘要（episode summaries）**；
  - 用 RAG 检索相关集次，**主动检测并解决叙事不一致**（detect & resolve，而非只预防）；
  - 在多个 LLM 生成的故事上实验，一致性与稳定性显著优于 GPT 基线。
- 与 ndecon 关系：**最高相关的论文之一**。我们 ContextPack 是"预防式注入"（写之前喂相关材料），SCORE 补上"事后检测矛盾"一环。可落地为新功能：章节生成后，对物品/角色状态断言做跨章比对，产出矛盾报告交作者裁决（只报告不自动改，与我们自评纪律一致）。

#### P3. ComoRAG: A Cognitive-Inspired Memory-Organized RAG for Stateful Long Narrative Reasoning

- arXiv：https://arxiv.org/abs/2508.10419（AAAI 2026 Poster；MIT 代码 347★，见 1.2⑨）
- 核心思想：叙事推理不是一次性检索，而是"新证据获取 × 旧知识巩固"的动态循环；动态记忆工作区 + 探测查询 + 迭代巩固
- 与 ndecon 关系：为 L3 的"多轮求证检索"提供学术依据；但其面向阅读理解，迁到创作侧需重新设计。

#### P4. Guiding Generative Storytelling with Knowledge Graphs

- arXiv：https://arxiv.org/abs/2505.24803（2025-05；正式发表于 International Journal of Human–Computer Interaction, 2026 年第 42 卷）
- 作者：Zhijun Pan（伦敦艺术大学）、Charismatic.ai 团队
- 方法：知识图谱辅助的长篇叙事管线——KG 初始化 → 上下文/场景生成 → **KG 增量更新**；提供**编辑模式（Edit Mode）**让用户直接修改图谱来引导故事；15 人用户研究（自建 prompt、生成、两阶段对比）证明叙事质量显著提升
- 与 ndecon 关系：直接支持 ADR-0004 的 **L3（实体图谱 + CHAPTER_COMMIT 投影）**；其"用户编辑 KG 引导生成"与我们"结构化状态可人工修改"理念一致；HCI 用户研究的实验设计（同作者两阶段开关对比）可作为我们日后做功能效果评测的模板。

#### P5. Long Story Generation via Knowledge Graph and Literary Theory

- arXiv：https://arxiv.org/abs/2508.03137（2025-08，北京交通大学）
- 问题：大纲式多阶段生成的两大病——遗忘旧大纲导致**主题漂移**、情节枯燥逻辑松散
- 方法：多智能体 Story Generator；
  - **记忆存储双组件**：长期记忆（识别最重要记忆防主题漂移）+ 短期记忆（保留每轮最新大纲）；
  - 基于叙事学理论的"主题-障碍"框架引入不确定因素与评价标准生成大纲；
  - 计算前情相似度，**用 KG 整合新节点内容**增强吸引力；
  - Writer/Reader 模拟对话，根据反馈修订。
- 与 ndecon 关系：长/短期记忆分层对应我们"前章摘要（短）+ BM25 全书召回（长）"；"计算前情相似度"正是 BM25/向量召回要做的事；读者模拟是独立审稿 Agent 的另一种形态。

#### P6. CreAgentive: An Agent Workflow Driven Multi-Category Creative Generation Engine

- arXiv：https://arxiv.org/abs/2509.26461（2025-09，四川大学/北师大；代码 https://github.com/Austinggg/CreAgentive）
- 核心抽象：**Story Prototype**——题材无关的、基于知识图谱的叙事表示，用语义三元组编码角色/事件/环境，把"故事逻辑"与"文风实现"解耦
- 三阶段：初始化（叙事骨架）→ 生成（长短期目标引导多智能体对话实例化 Prototype）→ 写作（产出含倒叙/伏笔等高级结构的多类型文本）
- 宣称效果：通用骨干模型生成上千章、质量稳定、**成本 <$1 / 100 章**；10 项叙事指标的二维评测框架
- 与 ndecon 关系："叙事逻辑与文风解耦"为我们的"骨架五部件 → 正文"分层提供了学术命名；其 10 指标评测框架（质量×长度）可参考以扩充我们章节自评的维度。

#### P7. StoryWriter: A Multi-Agent Framework for Long Story Generation

- arXiv：https://arxiv.org/abs/2506.16445（2025-06，清华等）
- 三智能体：outline agent（事件化大纲，含角色与事件间关系）→ planning agent（细化事件并规划每章写哪些事件，保证叙事交织）→ writing agent（**基于当前事件动态压缩故事历史**后生成并反思）
- 与 ndecon 关系：writing agent 的"按当前事件动态压缩历史"与 ContextPack 的 top-k + 前章摘要是同一问题的两种解法（它们让模型压缩，我们确定性组装——我们更可控可测）；planning agent 的"每章事件分配"对我们细纲 schema（目前每章一个 core_event）是潜在增强。

### 2.2 通用写作助手 RAG

#### P8. Pearl: Personalizing LLMs Writing Assistants with Generation-Calibrated Retrievers

- arXiv：https://arxiv.org/abs/2311.09180（Google，EMNLP 2023）
- 方法：检索作者**历史文档**个性化写作；训练一个"生成校准"检索器——检索分数与"用了该文档后下游生成是否更贴合作者偏好"成比例（scale-calibrating KL 散度目标）；含训练数据选择方法（识别真正需要个性化的请求）
- 与 ndecon 关系：学习型检索不必只来自参考书——**作者自己的历史定稿**是零版权风险的个性化语料（我们的 B 类一致性检索已覆盖自己正在写的书；未来可扩展到作者跨作品风格库）。

#### P9. DeepWriter: A Fact-Grounded Multimodal Writing Assistant Based On Offline Knowledge Base

- arXiv：https://arxiv.org/abs/2507.14189（2025-07，蚂蚁集团）
- 问题：金融/医疗/法律专业写作缺领域知识且幻觉；在线 RAG 多次检索不一致、网页内容不可靠
- 方法：**精选离线知识库** + 任务分解 → 大纲生成 → 多模态检索 → 逐节撰写并反思；层级化知识表示提升检索
- 与 ndecon 关系："离线精选知识库优于在线搜索"从专业写作角度再次验证我们的离线红线；其"大纲先行→逐节生成→反思"与我们"细纲→正文→自评"同构。

#### P10. A Survey on LLMs for Story Generation（综述）

- PDF：https://pdfs.semanticscholar.org/231c/763120f4624fd203a6d0991acd7de3cb53a4.pdf
- 作者：Maria Teleki 等（Texas A&M）
- 分类学：按**主要作者身份**二分——(i) LLM 独立生成；(ii) **作者辅助（Author Assistance）：人类是主作者，LLM 协同**
- 配套资源：https://github.com/mariateleki/Awesome-Story-Generation
- 对 ndecon 的定位：我们明确属于第二类，且综述指出该方向缺口为大规模数据集/指标、开源小模型、推理时控制方法——我们的 CC0 金标 + Recall 基线正属于"开源评测资产"这一稀缺品类。

### 2.3 邻近技术（非写作专用，但被写作系统广泛借用）

- **HippoRAG**（OSU/斯坦福，2024）：模拟海马体，知识图谱 + 个性化 PageRank，单步完成多跳检索，比迭代检索（IRCoT）成本低一个数量级。中文解读见 CSDN 等。对 L3 图谱检索的遍历算法有参考价值。
- **LongRAG**（Waterloo，[arXiv:2406.15319](https://arxiv.org/abs/2406.15319)）：把检索单元从 100 词段落放大到 4K token，降低检索器负担、提升召回，交给长上下文模型阅读。启示：我们章节级文档（整章摘要一个 doc）本身就是"长检索单元"思路，不必急于细切 chunk。
- **CRAG（Corrective RAG，[arXiv:2401.15884](https://arxiv.org/abs/2401.15884)）**：检索质量评估后决定纠错/回退，可作为 L2 置信路由的通用模式。

---

## 3. 横向对比（写作项目关键维度）

| 项目 | 检索栈 | 记忆/状态模型 | 写后自动维护 | 人审闸门 | 离线无 key | 许可证 |
|---|---|---|---|---|---|---|
| **ndecon（我们）** | bigram BM25（L1），向量冻结（L2） | 五部件骨架 + 伏笔台账 + ContextPack | ❌（缺口） | ✅ draft/confirmed | ✅ Fake 全链路 | Apache-2.0 |
| webnovel-writer | Qwen3-Embed-8B + Jina rerank | 五路派生 state/index/summaries/memory/vector | ✅ | 部分 | ❌ 重模型 | GPL-3.0 |
| AI_NovelGenerator | ChromaDB | 多 Agent 状态 | ✅ 评审链 | 部分 | ⚠️ | AGPL-3.0 |
| AI-Novel-Writing-Assistant | Qdrant（可选，默认 SQLite） | 世界/角色/伏笔/时间线/质量债 | ✅ 审计→修复→回灌 | ✅ | ⚠️ 接模型 | NOASSERTION |
| AI-Novel-Writer | embedding 可选，**降级 SQLite FTS** | 角色状态/剧情树（只读快照）/故事线 | ✅ 审稿修稿定稿 | ✅✅ Proposal 收件箱 | ✅ Ollama | GPL-3.0 |
| NovelClaw | memory bank（可编辑） | 显式状态机 + storyboard | ✅ 可观测运行 | ✅ 工作区人控 | ⚠️ | **MIT** |
| goink | **bge-small ONNX + sqlite-vec + MMR** | 31 工具 + 有向关系图 + 伏笔/弧线/读者认知 | ✅✅ 强制清单 + 独立审稿 Agent | ✅ 工具结果人可见 | ✅ 全本地 | AGPL-3.0 |
| vela | sqlite + 轻量向量 | 角色跨章动态状态 | ✅ 自动审阅 | ✅ | ✅ Ollama | GPL-3.0 |
| ai-novelist | ChromaDB | 两步 RAG / Agentic RAG | ⚠️ | ✅ 人在回路 | ⚠️ | **MIT** |
| ComoRAG（学术） | 动态记忆工作区 + 迭代探测检索 | 认知记忆模型 | N/A（阅读理解） | N/A | ✅ | **MIT** |

---

## 4. 对 ndecon 的决策映射

### 4.1 已被外部证据验证的决策（继续坚持）

| ndecon 决策（ADR/Issue） | 外部对应证据 |
|---|---|
| 候选/确认分层，模型不得直接改权威状态（#15） | AI-Novel-Writer Proposal 收件箱；NovelClaw 可编辑工作区；CreAgentive/P4 的人引导 KG |
| L1 纯词法检索先行、无 embedding 可跑（#17） | AI-Novel-Writer 无 embedding 降级 SQLite FTS；AI-Novel-Writing-Assistant 默认 SQLite、RAG 可选 |
| 金标 Recall@10 基线作为加层引入门（#17） | 综述指出开源方向缺评测资产；各项目普遍无公开检索评测 |
| JSONL/确认态为真源，图谱只能是派生视图（ADR-0004 L3） | AI-Novel-Writer"剧情树是可重建只读快照，不取代作者事实" |
| 参考书只注入聚合数字，不注入原文（版权红线） | 比 GROVE 直接 few-shot 他人作品更保守，规避版权风险；头部项目多未做此隔离 |
| 章节级"长检索单元"（整章摘要一个 doc） | LongRAG 证明长检索单元降低检索器负担、提高召回 |
| 证据锚定：模型给 quote、本地定偏移（#2） | AI-Novel-Writing-Assistant 的"章节证据回溯/retrieval trace"是同方向产品化 |

### 4.2 识别出的缺口与建议（按性价比排序）

1. **写后自动投影（L3 CHAPTER_COMMIT，最高优先）**
   - 依据：goink 强制维护清单、AI-Novel-Writing-Assistant 状态回灌、P4/P5/P7 的 KG/记忆更新；
   - 建议形态：#16 章节确认后确定性跑"角色状态变化 / 伏笔回收 / 弧线推进"检查，产出**候选变更**进收件箱，作者应用才生效（沿用 Proposal 闸门，不自动改写）；
   - 伏笔 schema 增补 `planned_resolve_order`（计划回收章）+ 临期/超时提醒（goink 已验证好用）。

2. **SCORE 式跨章矛盾检测（中优先，可与 1 合并）**
   - 依据：P2 SCORE；goink 独立审稿子 Agent；
   - 建议形态：章节自评之外新增"一致性审查"：关键物品/角色状态断言比对，矛盾只报告、作者裁决。

3. **L2 向量层解冻时的确定技术栈（已获生产佐证）**
   - bge-small-zh-v1.5（int8 ONNX 本地推理）+ sqlite-vec + MMR 重排 + 写完增量索引（goink 生产组合）；
   - 仍须先过 ADR-0004 引入门：L1 在扩充后的金标上 Recall@10 被证明不足；切块参考中文教程经验（章节边界、段落切分、chunk 重叠、hash 去重）。

4. **长期：作者自有语料的个性化检索（低优先）**
   - 依据：P8 Pearl（检索作者历史文档）；零版权风险，可在跨作品工作区成熟后做。

### 4.3 明确不学的部分

- Qdrant/Milvus/Chroma 常驻服务与 8B 嵌入重模型（违反离线红线与单人运维约束）；
- Agentic RAG（模型自主决定查什么）在 L1/L2 阶段不引入——破坏确定性可重算，L3 之后再评估；
- GROVE 式直接检索他人作品片段入 prompt（版权红线永不放松）。

---

## 5. 许可证合规清单

| 许可证 | 项目 | 对 ndecon（Apache-2.0）的含义 |
|---|---|---|
| MIT | NovelClaw、ai-novelist、ComoRAG、RaCig（Apache-2.0） | 可参考/复用代码，保留版权与许可声明即可 |
| GPL-3.0 | AI-Novel-Writer、vela、webnovel-writer | 只能 clean-room 学思想，禁止拷代码进 Apache 项目 |
| AGPL-3.0 | AI_NovelGenerator、goink、knowrite | 同上，网络使用也触发传染，严格隔离 |
| NOASSERTION | AI-Novel-Writing-Assistant 等 | 视同无许可，不引用代码 |
| 论文 | 全部 arXiv 预印本 | 思想/方法可自由借鉴，引用时标注出处 |

---

## 6. 来源清单（均于 2026-09-25 访问核验）

**开源仓库**

1. https://github.com/ExplosiveCoderflome/AI-Novel-Writing-Assistant
2. https://github.com/EthanYoQ/AI-Novel-Writer
3. https://github.com/iLearn-Lab/NovelClaw
4. https://github.com/heider-x/vela
5. https://github.com/FlickeringLamp/ai-novelist
6. https://github.com/lingfengQAQ/webnovel-writer
7. https://github.com/YILING0013/AI_NovelGenerator
8. https://github.com/sigpanic/goink
9. https://github.com/EternityJune25/ComoRAG
10. https://github.com/ZulutionAI/RaCig
11. https://github.com/mariateleki/Awesome-Story-Generation（综述配套列表）

**论文**

1. GROVE — https://arxiv.org/abs/2310.05388（EMNLP 2023 Findings）
2. SCORE — https://arxiv.org/abs/2503.23512（2025，v6 2025-09）
3. ComoRAG — https://arxiv.org/abs/2508.10419（AAAI 2026 Poster）
4. Guiding Generative Storytelling with Knowledge Graphs — https://arxiv.org/abs/2505.24803（IJHCI 2026）
5. Long Story Generation via Knowledge Graph and Literary Theory — https://arxiv.org/abs/2508.03137
6. CreAgentive — https://arxiv.org/abs/2509.26461（代码 https://github.com/Austinggg/CreAgentive）
7. StoryWriter — https://arxiv.org/abs/2506.16445
8. Pearl — https://arxiv.org/abs/2311.09180（EMNLP 2023）
9. DeepWriter — https://arxiv.org/abs/2507.14189
10. A Survey on LLMs for Story Generation — Semantic Scholar PDF（Texas A&M）
11. LongRAG — https://arxiv.org/abs/2406.15319
12. CRAG — https://arxiv.org/abs/2401.15884

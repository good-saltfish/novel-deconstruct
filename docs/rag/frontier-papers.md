# 前沿 RAG 论文与综述 · 落地研判手册

> 用途：为 novel-deconstruct 的 RAG 能力（见 `docs/adr/0004-rag-tiers-and-adoption-gates.md`，检索分 L0–L3 四层、逐层付费、凭金标证据解冻）提供选型依据。
> 目标读者：落地工程师 + 每日学习文档作者。
>
> **核验说明（请务必阅读）**
> - 标注【已核验】的条目：标题/编号/年份/核心主张摘要均来自 arXiv 摘要原文（本任务用 `web_fetch` 直连 `arxiv.org/abs/<id>` 逐篇核验）。
> - 标注【转引·已核验于项目报告】的条目：来自 `docs/research/2026-09-25-rag-writing-landscape.md`，该报告此前已一手核验，本任务转引并据此撰写。
> - 标注【未核实】的条目：记忆/二手来源，未经 arXiv 原文核验，不得当作事实。
> - web_search 在本环境不可用（401），故全部以 arXiv 摘要为据；RAG-Fusion 为博客非 arXiv 论文，其编号标注【未核实 arXiv 编号】。
> - 项目约束红线（不可突破）：无 API key / 断网必须可跑、确定性可重算、版权隔离（A 类学习型只出聚合数字不出原文；B 类一致性只索引本书）。任何"需常驻服务/需 key/需联网"的方案都只能在 L2+ 触发条件下考虑，且须满足回退。

---

## 目录
1. [检索底座：稠密 / 稀疏 / 混合 / 后期交互](#1-检索底座)
2. [查询优化：HyDE / Query2Doc / 改写 / RAG-Fusion](#2-查询优化)
3. [重排：cross-encoder 与 LLM 重排的收益边界](#3-重排)
4. [上下文压缩与「长上下文 vs RAG」](#4-长上下文-vs-rag)
5. [RAG 评估：RAGAS / ARES / 金标集与 Recall@k](#5-评估)
6. [Agentic / Self-RAG / Corrective RAG](#6-agentic)
7. [GraphRAG 与结构化检索](#7-graphrag)
8. [模块化 RAG 综述](#8-模块化)
9. [中文场景检索特殊性](#9-中文特殊性)
10. [落地优先级总表（给 captain 的决策速查）](#10-落地总表)

---

## 1. 检索底座

### 1.1 ColBERT（后期交互 late interaction 奠基）
- **标题**：ColBERT: Efficient and Effective Passage Search via Contextualized Late Interaction over BERT
- **arXiv**：2004.12832【已核验】
- **年份**：2020（SIGIR；作为后期交互基石列入，非近两年的"前沿"但被几乎所有混合检索论文引用，必读）
- **一句话核心主张**：查询与文档各自独立编码，再在 token 级做细粒度 MaxSim 交互，兼顾 BERT 级精度与可离线预计算文档表示。
- **方法论要点**：query/document 分别过 BERT 得到 token 嵌入；检索时对 query 每个 token 取与 doc 各 token 最大相似度再求和（MaxSim）；文档端表示可离线建好，在线只算 query。
- **可迁移到 novel-deconstruct 的具体点**：L2「向量混合检索」可借鉴"文档端预计算、在线轻量交互"的思路，降低在线成本；token 级匹配对中文 bigram 也有启发（多粒度匹配）。
- **不可迁移的原因**：原始 ColBERT 依赖 BERT 编码器（需模型权重，违反"无 key 可跑/确定性"红线）；我们 L1 是零模型的 bigram BM25，要先过 L2 解冻门槛才能上编码器。
- **落地判定：有触发条件再做**（L2 解冻时，作为"多向量检索"候选，且必须 sqlite-vec/numpy 本地、无 key 退回 L1）。理由：精度高但强依赖模型，与当前零模型底座不兼容。

### 1.2 M3-Embedding（稠密/稀疏/多向量统一）
- **标题**：M3-Embedding: Multi-Lingual, Multi-Functionality, Multi-Granularity Text Embeddings Through Self-Knowledge Distillation
- **arXiv**：2402.03216【已核验；注意早期流传的 2402.03232 为误传编号，正确为 2402.03216】
- **年份**：2024（v5 更新于 2025-12，覆盖到 2025）
- **一句话核心主张**：单个模型同时支持稠密、稀疏、多向量三种检索功能，覆盖 100+ 语言、最长 8192 token，靠自知识蒸馏统一训练。
- **方法论要点**：统一架构输出三种表示；自知识蒸馏让多任务互相提升；长文本切分+聚合支持 8K 上下文。
- **可迁移到 novel-deconstruct 的具体点**：一旦 L2 解冻，M3 的"一模型三检索合一"正好对应我们的"稠密+稀疏+后期交互混合"需求，且多语言对中文友好；长文本支持契合章节级长检索单元。
- **不可迁移的原因**：需加载模型权重（无 key 不可跑）；中文虽友好但仍依赖外部模型，须满足 L2 触发的"金标 Recall@10 不足"前提并带 L1 回退。
- **落地判定：有触发条件再做**（L2 解冻后的首选稠密/多向量底座候选）。理由：能力最契合，但资产与红线约束使其不能现在上。

### 1.3 LongRAG（长检索单元）
- **标题**：LongRAG: Enhancing Retrieval-Augmented Generation with Long-Context LLMs
- **arXiv**：2406.15319【转引·已核验于项目报告 `docs/research/2026-09-25-rag-writing-landscape.md`】
- **年份**：2024
- **一句话核心主张**：把检索单元从 ~100 词段落放大到 4K token，降低检索器负担、提升召回，再交给长上下文模型阅读。
- **方法论要点**：粗粒度长文档单元 + 长上下文 reader。
- **可迁移到 novel-deconstruct 的具体点**：我们"整章摘要作为一个 doc"（`context.py` 中 `SUMMARY_DOC_PREFIX` + `OUTLINE_DOC_PREFIX` 各成 doc）本质就是长检索单元思路——**不必急于把章节切碎成细 chunk**。这是与现有架构契合度最高的论文之一。
- **不可迁移的原因**：reader 侧依赖长上下文 LLM；且长单元会降低细粒度定位精度，需配合 `SNIPPET_LIMIT=120` 的确定性截断与 BM25 细粒度回看。
- **落地判定：现在做（架构印证，非新代码）**。理由：其结论已与我们的章节级 doc 设计一致，作为"不细分 chunk"的设计佐证写入 ADR 与学习文档即可，无新增依赖。

---

## 2. 查询优化

### 2.1 HyDE（假设文档嵌入，零样本稠密检索）
- **标题**：Precise Zero-Shot Dense Retrieval without Relevance Labels
- **arXiv**：2212.10496【已核验】
- **年份**：2022（ICLR 2023；作为查询优化基石列入）
- **一句话核心主张**：让 LLM 先生成"假设答案文档"，对其编码后再检索，无需任何相关标签即可显著提升零样本稠密召回。
- **方法论要点**：query → LLM 生成伪文档 → 编码器嵌入伪文档 → 向量检索；用"文档形态"对齐检索空间。
- **可迁移到 novel-deconstruct 的具体点**：对"无字面改写查询"（用户/系统用剧情意图而非原文词检索）有帮助——生成假设剧情文档再检索可弥合词汇鸿沟。
- **不可迁移的原因**：生成伪文档需 LLM（有 key/有成本），且生成是**非确定性**的，违反"确定性可重算"红线；L1 零模型阶段不可用。
- **落地判定：有触发条件再做**（L2 解冻且引入 LLM 编排层后，作为查询侧增强；须保证离线缓存/可重算）。理由：收益明确但撞红线，不能现在做。

### 2.2 Query2Doc（伪文档查询扩展）
- **标题**：Query2Doc: Query Expansion with Large Language Models
- **arXiv**：2303.07678【已核验；EMNLP 2023】
- **年份**：2023
- **一句话核心主张**：用 LLM 为原始 query 生成一段伪文档，拼回 query 做扩展，BM25 提升 3–15% 且免微调。
- **方法论要点**：LLM 生成伪文档 → 拼接原 query → 交由原检索器（BM25/稠密）检索。
- **可迁移到 novel-deconstruct 的具体点**：可直接套在 **L1 的 bigram BM25** 上做查询扩展（生成剧情伪文档扩充查询词面），无需改索引；对中文"无字面改写查询"尤其有用。
- **不可迁移的原因**：依赖 LLM 生成（有 key + 非确定性）；扩展词若引入噪声会稀释 bigram 精确匹配，需评估。
- **落地判定：有触发条件再做**（当 L1 BM25 出现召回缺口、且已引入 LLM 编排层时，作为低成本增强尝试；须缓存生成结果保证可重算）。

### 2.3 Rewrite-Retrieve-Read（可训查询改写）
- **标题**：Rewrite-Retrieve-Read: Question Rewriting for Effective Query Reformulation
- **arXiv**：2305.14283【已核验；EMNLP 2023】
- **年份**：2023
- **一句话核心主张**：把"查询改写"做成可训练的小模型模块，用检索反馈（RL/奖励）让改写器对齐下游检索效果。
- **方法论要点**：小 rewriter 模型改写 query → 检索 → 用答案质量/检索指标作奖励微调 rewriter。
- **可迁移到 novel-deconstruct 的具体点**：若将来要系统化解决"中文无字面改写查询"，可训一个轻量改写器（而非每次调大模型），成本可控。
- **不可迁移的原因**：需训练数据 + 训练流程，超出当前零模型/零依赖底座；且训练本身依赖标注或强 LLM 裁判。
- **落地判定：不做（短期）**。理由：投入产出比低，且可由 Query2Doc（调 LLM）或人工规则先顶上；列入 L3 或路线图候选。

### 2.4 RAG-Fusion（多路查询 + 倒数排名融合）
- **标题**：RAG-Fusion: A New Take on Retrieval-Augmented Generation
- **来源**：Adrian H. Rasmussen 博客（非 arXiv 论文）【未核实 arXiv 编号；方法论为社区公认实践】
- **年份**：2024（博客）
- **一句话核心主张**：用 LLM 把用户 query 扩展成多个子查询分别检索，再用 **Reciprocal Rank Fusion（RRF）** 融合多路结果，弥补单 query 视角偏差。
- **方法论要点**：query → LLM 生成 N 个改写查询 → 各自检索 → RRF（按排名倒数加权）融合。
- **可迁移到 novel-deconstruct 的具体点**：**RRF 融合公式本身零模型、纯算法**，可直接用于 L2「向量 + BM25 多路融合」（`0004-rag-tiers` 已点名 RRF）。多路查询生成则可在有 LLM 编排层后叠加。
- **不可迁移的原因**：多路查询生成需 LLM；但 RRF 融合步骤与模型无关，可独立落地。
- **落地判定：现在做（仅 RRF 融合部分） / 有触发条件再做（多路查询生成部分）**。理由：RRF 是我们 L2 融合的指定算法，纯确定性、无依赖，应现在固化进融合层；多路 LLM 查询生成待 L2 解冻。

---

## 3. 重排

### 3.1 Cross-Encoder 重排（通用方法论，非单一新论文）
- **标题/编号**：经典范式（Nogueira & Cho, 2019 MonoT5/跨编码器重排）；本任务将其作为"重排收益边界"的参照【未核实单篇 arXiv 编号，作为公认方法陈述】
- **年份**：2019 起（基础方法）
- **一句话核心主张**：把 query+doc 拼接后过一次编码器做相关性打分，精度远高于双Encoder，但无法离线预计算、只能对 top-k 重排。
- **方法论要点**：对检索 top-k（如 100）逐对 (q,d) 过 cross-encoder 得分数重排。
- **可迁移到 novel-deconstruct 的具体点**：L2 融合后、取 top-k 进 `ContextPack` 前，加一层 cross-encoder 重排可显著提 Recall/精度——尤其弥补 bigram BM25 的位置/语义偏差。
- **不可迁移的原因**：需模型权重（有 key）；对 top-k 全量过编码器，成本随 k 线性增长；非确定性。
- **落地判定：有触发条件再做**（L2 解冻且金标显示"召回够但精度/排序差"时）。理由：重排是"最后一步精修"，应在检索底座稳定后。

### 3.2 RankRAG（单 LLM 统排名与生成）
- **标题**：RankRAG: Unifying Context Ranking with Retrieval-Augmented Generation in LLMs
- **arXiv**：2407.02485【已核验】
- **年份**：2024
- **一句话核心主张**：指令微调单个 LLM 同时承担"上下文排序"与"答案生成"两职，仅混入少量排序数据即可超越专用排序模型。
- **方法论要点**：同一 LLM 经指令微调，既能给检索上下文打相关性分（pointwise/listwise 风格），又能生成答案；跨域泛化强（生物医学零样本可比 GPT-4）。
- **可迁移到 novel-deconstruct 的具体点**：若未来上 LLM 编排层，"检索排序"与"生成"共用一个微调 LLM 可省一个模型；对跨书/跨题材的"相关性打分泛化"有启发。
- **不可迁移的原因**：需指令微调 LLM（训练 + 权重 + 强算力），与零模型红线冲突；且微调数据从何而来（需金标）在我们这里尚缺。
- **落地判定：不做（短期）**。理由：依赖微调与权重，远超当前底座；其"统一排序+生成"思想可留作 L3 长期参考。

### 3.3 重排的收益边界（综合研判）
- **核心结论（综合多篇 + 项目约束）**：重排的典型收益在 **"检索召回已够、但 top-k 排序噪声大"** 时最显著；对纯 BM25 bigram（我们 L1）而言，先做"受控词表加权"（`CONTROLLED_TERM_BOOST=2.0`）和"伏笔台账字面重合回收"（`RESOLVE_THRESHOLD=0.6`）比上 cross-encoder 更划算且无依赖。
- **落地判定**：L1 阶段用**规则重排（受控词加权 + 伏笔/细纲邻近度）**；cross-encoder/LLM 重排列入 L2 触发条件。

---

## 4. 长上下文 vs RAG

### 4.1 RAG vs Long-Context（含 Self-Route）
- **标题**：Retrieval Augmented Generation or Long-Context LLMs? A Comprehensive Study and Hybrid Approach
- **arXiv**：2407.16833【已核验；EMNLP 2024 industry】
- **年份**：2024
- **一句话核心主张**：资源充足时长上下文（LC）平均优于 RAG，但 RAG 成本显著更低；提出 Self-Route 按模型自反在 RAG/LC 间路由。
- **方法论要点**：在多个数据集 + 3 个最新 LLM 上系统对比；Self-Route 用 LLM 自反判断该走 RAG 还是 LC。
- **可迁移到 novel-deconstruct 的具体点**：**印证我们的 L0–L3 分层付费设计**——不是"RAG 还是长上下文"二选一，而是按证据/预算路由。Self-Route 的"自反路由"思想可对应 L2 的置信路由（与 CRAG 同脉）。
- **不可迁移的原因**：Self-Route 本身需 LLM 自反判断（有 key）；但其"路由"范式可抽象为规则版（如按金标召回率阈值路由）。
- **落地判定：现在做（范式印证，写进 ADR/学习文档）**。理由：其结论支撑既有分层架构，无需新代码。

### 4.2 Lost in the Middle（长上下文位置偏差）
- **标题**：Lost in the Middle: How Language Models Use Long Contexts
- **arXiv**：2307.03172【已核验；TACL 2023】
- **年份**：2023
- **一句话核心主张**：长上下文中相关信息放在**中间**时模型表现显著下降，放在**首尾**最强。
- **方法论要点**：多文档 QA + key-value 检索实验，扫描相关信息位置。
- **可迁移到 novel-deconstruct 的具体点**：直接指导 `build_context_pack` 的**片段排布顺序**——把最相关的 BM25 命中、开放伏笔放在包的首尾，降低"中间遗忘"风险；也提示 `SNIPPET_LIMIT=120` 的截断应保留首尾关键信息。
- **不可迁移的原因**：无（纯观察结论，零依赖）。
- **落地判定：现在做（写进 context 组装规范与学习文档）**。理由：零成本、可直接改进 ContextPack 排布。

---

## 5. 评估

### 5.1 RAGAS（无参考自动化评测）
- **标题**：RAGAS: Automated Evaluation of Retrieval Augmented Generation
- **arXiv**：2309.15217【已核验；v2 更新于 2025-04】
- **年份**：2023
- **一句话核心主张**：无需金标参考即可自动评测 RAG，覆盖检索相关性、忠实度、答案相关性三（多）维。
- **方法论要点**：ref-free 指标（如 faithfulness 用 LLM 判生成是否忠于上下文；context relevance 用 LLM 判检索上下文相关度）。
- **可迁移到 novel-deconstruct 的具体点**：可作 L1→L2 解冻的**离线评测基线**（尤其忠实度/答案相关性）；但注意其"检索相关性"判据依赖 LLM 裁判。
- **不可迁移的原因**：faithfulness/relevance 指标需 LLM 裁判（有 key + 非确定性）；与"确定性可重算"红线冲突，只能离线评测用，不能进在线路径。
- **落地判定：有触发条件再做**（作为离线评测台，配合金标集；不进在线）。理由：评测价值高，但需 key，且须与"金标 Recall@k"口径互补。

### 5.2 ARES（合成数据训轻量裁判 + PPI）
- **标题**：ARES: An Automated Evaluation Framework for Retrieval-Augmented Generation
- **arXiv**：2311.09476【已核验；NAACL 2024】
- **年份**：2023
- **一句话核心主张**：用合成训练数据微调轻量 LM 作裁判，评 context relevance / answer faithfulness / answer relevance 三维，并用预测区间（PPI）+ 少量人工标注控制误差。
- **方法论要点**：LLM 生成合成正/负例 → 微调小裁判模型 → PPI 统计置信区间。
- **可迁移到 novel-deconstruct 的具体点**：**"合成数据训裁判 + PPI 置信区间"** 思路可用于我们"金标集不足"的困境——用合成正负例扩大小裁判，再用少量人工金标校正。
- **不可迁移的原因**：需训练小模型 + 合成数据流水线（有 key/算力）；裁判模型非确定性。
- **落地判定：不做（短期）**。理由：评测基建投入大；先用 RAGAS（现成）或纯规则金标口径过渡。

### 5.3 金标集构建与 Recall@k 口径（项目落地口径）
- **口径定义（本任务给出，非单一论文）**：
  - **Recall@k**：在本书已写章节/细纲的"应召回 doc 集合"上，top-k 命中比例。我们 L2 解冻门槛即 **"L1 金标 Recall@10 不足"**（见 `0004-rag-tiers`）。
  - **金标集来源**：优先用 **本书自身结构**做弱金标——每章细纲 `core_event/opening_hook/ending_hook` 应召回其"前章相关回顾"与本草开放伏笔；这天然零标注、确定性、版权安全（B 类只索引本书）。
  - **构建原则**：① 只用本书内数据（B 类，避免 A 类版权雷区）；② 确定性可重算（同一书同结果）；③ 与 RAGAS/ARES 的 LLM 裁判口径互补——LLM 裁判管"质量"，金标 Recall@k 管"是否漏召"。
- **可迁移到 novel-deconstruct 的具体点**：直接定义 L1/L2 的**量化解冻阈值与评测脚本**（确定性、零 key）。
- **不可迁移的原因**：无（纯口径设计）。
- **落地判定：现在做**。理由：是 L1→L2 解冻的硬门槛，必须先用零依赖方式把金标与 Recall@k 跑起来（具体脚本实现属下游任务，本任务只定口径）。

---

## 6. Agentic / Self-RAG / Corrective RAG

### 6.1 Self-RAG（自适应检索 + 自反思）
- **标题**：Self-RAG: Learning to Retrieve, Generate, and Critique through Self-Reflection
- **arXiv**：2310.11511【已核验】
- **年份**：2023
- **一句话核心主张**：引入反射 token，让 LM 自适应决定"是否检索、检索什么、生成是否够好"，并自评生成质量。
- **方法论要点**：训练带反思 token 的 LM，在推理时按需 retrieve / critique。
- **可迁移到 novel-deconstruct 的具体点**：**"按需检索"与"自批判"思想**对应我们 L2 的置信路由——当 BM25 命中不足/伏笔未回收时触发更深检索或回退。
- **不可迁移的原因**：需训练反思 token 模型（权重 + 训练）；原版强依赖模型。
- **落地判定：有触发条件再做（思想借鉴，规则化）**。理由：反射范式可抽象成"规则化置信路由"（如命中数 < 阈值则触发），不必上训练模型。

### 6.2 Corrective RAG（CRAG）
- **标题**：Corrective Retrieval Augmented Generation
- **arXiv**：2401.15884【转引·已核验于项目报告；v3 更新于 2024-10】
- **年份**：2024
- **一句话核心主张**：轻量检索评估器评判检索置信度，触发纠错/回退/网页扩展，并用 decompose-then-recompose 重组。
- **方法论要点**：T5 级小评估器打"置信度" → 高/中/低三档 → 高则直接用、中则再检索精修、低则回退知识库或网页。
- **可迁移到 novel-deconstruct 的具体点**：**最契合我们 L2「置信路由」通用模式**——评估器判断"本次 BM25 检索够不够"，不够就走向量检索（L2）/图谱（L3）或回退 L1。decompose-then-recompose 对"复杂多线索章节"也有启发。
- **不可迁移的原因**：原版评估器需 T5 权重（有 key）；"网页扩展"分支违反版权隔离（不能去抓外部网文）。
- **落地判定：有触发条件再做（规则化评估器）**。理由：其"置信度触发纠错"是 L2 解冻后的核心范式，但评估器先做成规则版（如基于 Recall@k 阈值/伏笔回收率），不急上模型。

### 6.3 Agentic RAG（综述性范式）
- **标题/范围**：Agentic RAG = 用 agent/多步推理编排检索—生成循环（如多跳、工具调用、自反思）；本任务作为范式归纳，无单一绑定论文【未核实单篇编号】
- **年份**：2024 起（范式）
- **一句话核心主张**：把 RAG 从"一次性检索"升级为"agent 多步：规划→检索→反思→再检索"的闭环。
- **可迁移到 novel-deconstruct 的具体点**：我们 **伏笔台账（`foreshadow.py`）本身就是一种"状态化 agent 记忆"**——跨章追踪开放伏笔并在合适章节回收，可视作轻量 agentic RAG。
- **不可迁移的原因**：完整 agentic 需 LLM 多步编排（有 key + 非确定性）；当前零模型底座只能做规则态 agentic。
- **落地判定：有触发条件再做（规则态先落地，LLM 态后上）**。理由：伏笔台账已具备 agentic 雏形，先把规则态做扎实。

---

## 7. GraphRAG 与结构化检索

### 7.1 GraphRAG（实体图 + 社区摘要）
- **标题**：From Local to Global: A Graph RAG Approach to Query-Focused Summarization
- **arXiv**：2404.16130【已核验；Microsoft，v2 更新于 2025-02】
- **年份**：2024
- **一句话核心主张**：用 LLM 抽取实体/关系建图，再做社区检测并生成社区摘要，使 RAG 能回答"全局性/总结性"问题，显著优于传统 RAG。
- **方法论要点**：实体关系抽取 → 知识图谱 → Leiden 社区检测 → 每社区 LLM 摘要 → 查询时自底向上聚合。
- **可迁移到 novel-deconstruct 的具体点**：**直接对应我们 L3「实体图谱 + CHAPTER_COMMIT 投影」**。人物关系网、阵营、伏笔因果链正适合用图表示；社区摘要思想可用于"全书梗概/角色弧光"类查询。
- **不可迁移的原因**：图构建与社区摘要强依赖 LLM（有 key + 非确定性 + 成本）；且"全局摘要"可能触碰 A 类版权（出原文聚合）——须限定 B 类只索引本书、只出聚合数字。
- **落地判定：有触发条件再做（L3 解冻时）**。理由：与 L3 设计同构，是 L3 的主要参考；但须严守版权隔离与回退。

### 7.2 HippoRAG（海马体式个性化 PageRank 多跳）
- **标题**：HippoRAG: Neurobiologically Inspired Long-Term Memory for Large Language Models
- **来源/编号**：OSU/斯坦福 2024【转引·已核验于项目报告 `docs/research/2026-09-25-rag-writing-landscape.md`；arXiv 编号【未核实】】
- **年份**：2024
- **一句话核心主张**：模拟海马体，用知识图谱 + 个性化 PageRank 单步完成多跳检索，成本比迭代检索（IRCoT）低一个数量级。
- **方法论要点**：图谱 + Personalized PageRank 做单步多跳传播。
- **可迁移到 novel-deconstruct 的具体点**：L3 图谱检索的**遍历/传播算法**可直接借鉴 PPR——"从某角色节点出发，沿关系边扩散召回相关章节"，比多轮 LLM 迭代便宜得多。
- **不可迁移的原因**：图谱构建依赖 LLM 抽取（有 key）；PPR 本身零模型但需先有图。
- **落地判定：有触发条件再做（L3 图谱遍历算法候选）**。理由：算法便宜且契合，但前置条件是 L3 图谱已建。

---

## 8. 模块化 RAG 综述

### 8.1 Modular RAG（可重配模块/算子）
- **标题**：Modular RAG: Transforming RAG Systems into LEGO-like Reconfigurable Frameworks
- **arXiv**：2407.21059【已核验】
- **年份**：2024
- **一句话核心主张**：把 RAG 拆成可重配的模块/算子（检索、生成、重排、内存…），支持 linear / conditional / branching / looping 多种流程模式。
- **方法论要点**：模块化抽象 + 流程编排原语；统一描述 Naive/Advanced/Agentic RAG。
- **可迁移到 novel-deconstruct 的具体点**：**为我们 L0–L3 分层 + 触发条件解锁提供了"模块化"语言**。可把 L1(outline/summary/BM25/foreshadow) 与未来 L2(L2 retriever/RRF/rerank) / L3(graph/PPR) 显式建模为可插拔算子，conditional(置信路由)/looping(伏笔回收迭代) 模式正对应 Self-RAG/CRAG/Agentic。
- **不可迁移的原因**：无（是框架性综述，零依赖）。
- **落地判定：现在做（架构语言，写进 ADR/学习文档）**。理由：统一描述现有与规划能力，零成本，强烈建议作为团队 RAG 词汇表。

---

## 9. 中文场景检索特殊性

> 本类不绑定单一论文，而是把前述方法在**中文网文/小说创作**场景下的特殊约束系统化（结合项目 `tokenize.py` 的 bigram 方案与多篇英文论文的迁移边界）。
- **分词/bigram 的必要性**：中文无空格，word-level 稠密检索易丢字面；我们 `tokenize.py` 用 **NFKC 归一 + 汉字 bigram 滑窗 + 英文数字串小写**，零词典零模型零网络，确定性——这正是对"中文无字面改写查询"的第一道防线。
- **无字面改写查询（词汇鸿沟）**：用户/系统常以"剧情意图"而非原文词检索（如"男主黑化那段" vs 原文"瞳孔里最后一丝光熄灭"）。缓解路径：
  1. **规则/受控词表**（现在做）：`CONTROLLED_TERM_BOOST=2.0` 对 Tone/ThemeTag 加权，用结构化标签桥接意图与字面。
  2. **查询扩展**（触发条件）：Query2Doc/HyDE 生成伪文档补足词面（需 LLM）。
  3. **伏笔台账字面回收**（`RESOLVE_THRESHOLD=0.6` bigram 重合比例）：用确定性的字面重合判定"伏笔是否已被回收"，是中文无改写场景下的廉价语义近似。
- **稠密检索对中文的坑**：通用英文 embedding 对中文网文语域偏移大；M3-Embedding 虽多语言但仍是通用域，需在**本书域**内验证（金标 Recall@k）。故 L2 上稠密前必须先有金标门槛。
- **可迁移总结**：中文特殊性 ⇒ **"bigram 稀疏保底 + 受控词加权 + 伏笔字面回收"作为 L1 零依赖三件套**；稠密/改写/重排全部推到 L2+ 触发条件，且每步带 L1 回退。
- **落地判定：现在做（三件套已部分实现，补规范）/ 触发条件再做（稠密与改写）**。

---

## 10. 落地总表

| # | 论文/方法 | 类别 | 落地判定 | 一句话理由 |
|---|-----------|------|----------|-----------|
| 1.1 | ColBERT | 后期交互 | 触发条件再做 | 精度高但强依赖编码器，撞红线 |
| 1.2 | M3-Embedding | 稠密/稀疏/多向量 | 触发条件再做 | L2 首选底座，但需模型权重 |
| 1.3 | LongRAG | 长检索单元 | **现在做（印证）** | 章节级 doc 已同构，零新增依赖 |
| 2.1 | HyDE | 查询优化 | 触发条件再做 | 需 LLM，非确定性 |
| 2.2 | Query2Doc | 查询扩展 | 触发条件再做 | 可套 L1 BM25，但需 LLM |
| 2.3 | Rewrite-Retrieve-Read | 可训改写 | 不做（短期） | 训练投入大，规则/LLM 可顶 |
| 2.4 | RAG-Fusion (RRF) | 多路融合 | **现在做(RRF)/触发(多路生成)** | RRF 纯算法零依赖，应固化进 L2 |
| 3.1 | Cross-Encoder 重排 | 重排 | 触发条件再做 | top-k 精修，需模型 |
| 3.2 | RankRAG | 统一排序+生成 | 不做（短期） | 需微调，超底座 |
| 3.3 | 重排收益边界 | 研判 | **现在做（规则重排）** | 受控词加权+伏笔回收先上 |
| 4.1 | RAG vs Long-Context | 路由 | **现在做（印证）** | 支撑 L0–L3 分层付费 |
| 4.2 | Lost in the Middle | 位置偏差 | **现在做** | 直接改 ContextPack 首尾排布 |
| 5.1 | RAGAS | 评测 | 触发条件再做 | 离线评测台，需 LLM 裁判 |
| 5.2 | ARES | 评测 | 不做（短期） | 裁判训练投入大 |
| 5.3 | 金标 Recall@k 口径 | 评测 | **现在做** | L1→L2 解冻硬门槛，零依赖 |
| 6.1 | Self-RAG | Agentic | 触发条件再做（规则化） | 反射范式先做成规则路由 |
| 6.2 | CRAG | 纠错路由 | 触发条件再做（规则化） | L2 置信路由通用模式 |
| 6.3 | Agentic RAG | 范式 | 触发条件再做 | 伏笔台账已是轻量 agentic |
| 7.1 | GraphRAG | 图谱 | 触发条件再做（L3） | 与 L3 同构，需 LLM 建图 |
| 7.2 | HippoRAG | 图谱遍历 | 触发条件再做（L3） | PPR 遍历算法便宜，前置需图 |
| 8.1 | Modular RAG | 综述 | **现在做（架构语言）** | 统一 L0–L3 模块化词汇 |
| 9 | 中文特殊性 | 场景 | **现在做（三件套）/触发** | bigram+受控词+伏笔回收保底 |

**统计**：条目 21 个（≥12 要求满足）；覆盖全部要求分类。其中【已核验】arxiv 直连核验 14 篇（含 ColBERT/Modular 等），【转引·已核验于项目报告】3 篇（LongRAG/CRAG/HippoRAG），【未核实】标注 3 处（RAG-Fusion 非 arXiv 编号、Cross-Encoder 单篇编号、Agentic RAG 单篇编号）。

**给 captain 的下一步建议**：
1. 现在可零依赖落地的（印证/规范类）：LongRAG 同构、RAG-vs-LC 路由、Lost-in-the-Middle 排布、金标 Recall@k 口径、Modular RAG 词汇、RRF 融合、规则重排、中文三件套规范——这些不产生新代码依赖，建议先以"学习文档 + ADR 补充"固化。
2. L2 触发条件（需 key/模型，带 L1 回退）：M3/ColBERT 稠密混合、Query2Doc/HyDE 查询扩展、cross-encoder 重排、CRAG/Self-RAG 规则化置信路由、RAGAS 离线评测。
3. L3 触发条件：GraphRAG 建图、HippoRAG-PPR 遍历（严守版权隔离）。
4. 不做（短期）：Rewrite-Retrieve-Read 训练、RankRAG 微调、ARES 裁判训练——投入大且被更简单方案覆盖。

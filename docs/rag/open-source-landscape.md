# RAG 开源生态全景与可借鉴性评估

- 调研日期：2026-10-05
- 调研人：rag-oss-scout（AgentTeams 自动任务 t2）
- 数据口径：**星标 / 许可证 / 主语言 / 最近推送时间均通过 GitHub REST API 于 2026-10-05 实时抓取**，下文如实记录，会随时间变化（星标为当日快照）。架构要点与"可借鉴组件"为分析性判断，其中未经本轮 API 复核的实现细节已标注 **【未核实】**。
- 配套文档：本文是通用 RAG 基础设施全景，与写作向调研 [`../research/2026-09-25-rag-writing-landscape.md`](../research/2026-09-25-rag-writing-landscape.md) 互补——那篇聚焦"小说创作型 RAG 项目 + 论文"，本文聚焦"通用框架 / 引擎 / 评测 / 组件库"，并以**「能否在不新增重依赖的前提下借鉴」**为唯一筛选镜头。
- 调研方法：GitHub API 一手抓取元数据 + 仓库描述 + 公开 README/论文核验；未采信二手榜单文章转述。

---

## 0. 本项目的硬约束（所有评估以此为准）

摘自 [`../pyproject.toml`](../pyproject.toml)、[`../roadmap.md`](../roadmap.md)、[`adr/0004-rag-tiers-and-adoption-gates.md`](adr/0004-rag-tiers-and-adoption-gates.md)：

1. **Python 3.10+**，运行时依赖**仅** `pydantic>=2` / `typer>=0.12` / `httpx>=0.27`；其余均为 dev 依赖（pytest 等）。
2. **L2 向量检索被限定为 `sqlite-vec` 或纯 `numpy`**，且**禁止任何常驻向量服务**（Milvus / Qdrant / Chroma server / 外部 embed 服务等）。无 key、断网可跑、CI 零模型额度是红线。
3. 检索分层逐层付费、凭金标 Recall@10 证据解冻（ADR-0004）；新依赖只能以**可选 extras** 引入，默认安装保持零重依赖。
4. 许可证 Apache-2.0：MIT/Apache-2.0 组件可参考或复用（保留声明）；GPL/AGPL 只能 clean-room 学思想；NOASSERTION 视为无许可、不引用代码。

> 一句话结论：**绝大多数"重型 RAG 框架/引擎/向量库"都违背约束 1–3，不能引入；本项目真正可借鉴的是它们的"思想 + 算法配方"，以及少量已获 ADR 背书的 L2 组件（sqlite-vec）。具体可零依赖自研的组件见第 5 节。**

---

## 1. 项目详录（19 个，含 5 个向量库作为"禁止项"对照）

> 字段约定：`★` = 2026-10-05 星标；`活跃度` 以 `pushed_at` 与 `open_issues` 综合判断；`借鉴判定` ∈ {可直接借代码, 仅借思想, 禁止, L2  sanctioned 组件}。

### 1.1 编排框架（全重栈，仅借思想）

#### ① LangChain — langchain-ai/langchain
- 链接：https://github.com/langchain-ai/langchain ｜ ★147,451 ｜ MIT ｜ Python ｜ 活跃（2026-10-05 推送）
- 定位：**通用 LLM / Agent 编排框架**（含 `langchain-core` / `langchain-community` / `langchain` 多包）
- 架构要点：Runnable 抽象 + LCEL 链式组合；`Retriever`/`Document`/`VectorStore` 接口成为事实标准；海量第三方集成（百余个向量库、模型、工具）【未核实：集成数量级】
- 可借鉴：其**检索接口契约设计**（query→retrieved docs→rerank 的清晰输入输出类型）值得学；`RetrievalQA` / `MultiQueryRetriever`（查询改写多路召回）是教科书式模式
- 依赖重量：**重**。核心即引 `pydantic` + `httpx` 等，但 `langchain-community` 会拉入 OpenAI、Requests、SQLAlchemy 等大量可选依赖，**远超本项目 3 依赖约束**
- 借鉴判定：**禁止引入**；仅借"多查询改写 + 融合"思想（见第 5 节自研）

#### ② LlamaIndex — run-llama/llama_index
- 链接：https://github.com/run-llama/llama_index ｜ ★52,411 ｜ MIT ｜ Python ｜ 活跃（2026-10-01）
- 定位：**以"数据索引 + 检索"为核心的 RAG 框架**（自称 document processing platform）
- 架构要点：`Index` 体系（VectorStoreIndex / SummaryIndex / TreeIndex / KeywordTableIndex）；`Node` 为最小检索单元（文档切分→Node→嵌入）；`ResponseSynthesizer` 召回后组装；`BM25Retriever` 与 `KeywordTableIndex` 提供**无嵌入的关键词召回路径**
- 可借鉴：**Node 抽象（带 parent/child 关系的检索单元）** 与**分层索引思想**（先粗检索再精读）对 ndecon 的章节级"长检索单元"有参考；其 `BM25Retriever` 证明关键词检索是向量之外的标准一等公民
- 依赖重量：**重**。主包拉入大量可选 loader / embedder / LLM 适配，且重依赖 `nltk`/`tiktoken`/`numpy` 等【未核实：具体传递依赖】
- 借鉴判定：**禁止引入**；仅借 Node/分层索引/关键词召回思想

#### ③ Haystack — deepset-ai/haystack
- 链接：https://github.com/deepset-ai/haystack ｜ ★26,648 ｜ Apache-2.0 ｜ Python ｜ 活跃（2026-10-05）
- 定位：**组件化 RAG / Agent 编排框架**（pipeline 由 typed 组件拼装）
- 架构要点：`Document`/`Pipeline`/`Component` 强类型组合；原生 `InMemoryBM25Retriever`（**零嵌入即可跑**）与 `EmbeddingRetriever` 并存；`Ranker` 组件做再排序；强调"显式控制检索/路由/记忆/生成"【未核实：最新 v2 组件命名】
- 可借鉴：**其"组件即类型化节点、检索与重排可插拔"的设计哲学** 与 **InMemoryBM25Retriever 作为默认无嵌入兜底的工程取向**——与 ndecon "L1 纯词法先行、无 embedding 可跑"完全同构
- 依赖重量：**重**（同样拉入众多适配依赖）
- 借鉴判定：**禁止引入**（Apache-2.0 可借代码，但依赖溢出）；设计哲学与 L1 兜底策略直接印证本项目现有路线

#### ④ txtai — neuml/txtai
- 链接：https://github.com/neuml/txtai ｜ ★12,991 ｜ Apache-2.0 ｜ Python ｜ 活跃（2026-10-04）
- 定位：**一体化语义搜索 + LLM 编排框架**（含向量库、Embeddings、RAG 流水线）
- 架构要点：自带的 `Embeddings` 索引支持 SQL + 向量混合；可只用语义搜索子模块；**支持关键词（BM25）+ 向量混合检索**【未核实：混合检索 API 细节】
- 可借鉴：混合检索"同一索引既算 BM25 又算向量、再用 RRF/加权融合"的实现思路；其轻量一体化定位比 LangChain 更接近本项目气质（但仍超出依赖约束）
- 依赖重量：**中-重**（核心依赖 `numpy`/`onnxruntime`/`hnswlib` 等）【未核实】
- 借鉴判定：**禁止引入**；借"同索引双路 + 融合"思路，自研实现见第 5 节

### 1.2 端到端引擎 / 平台（重栈，仅作对照，禁止）

#### ⑤ Dify — langgenius/dify
- 链接：https://github.com/langgenius/dify ｜ ★157,865 ｜ **NOASSERTION**（许可证未声明，视同无许可）｜ TypeScript + Python ｜ 活跃（2026-10-05）
- 定位：**低代码 Agent / RAG 应用平台**（含前端、后端、工作流编排、模型管理）
- 架构要点：完整 Web 平台 + 工作流 DSL + RAG 知识库管线；支持多种向量库后端
- 可借鉴：其**知识库"召回-重排-引用溯源"的产品化形态**与**工作流可视化编排**概念；但作为平台与本项目 CLI 形态不兼容
- 依赖重量：**极重**（前后端全栈 + 数据库 + 向量库）
- 借鉴判定：**禁止引入、且 NOASSERTION 不引用代码**；仅借产品形态概念

#### ⑥ RAGFlow — infiniflow/ragflow
- 链接：https://github.com/infiniflow/ragflow ｜ ★91,688 ｜ Apache-2.0 ｜ Go ｜ 活跃（2026-10-04）
- 定位：**深度文档理解（DeepDoc）驱动的 RAG 引擎**
- 架构要点：**基于文档版式/表格理解的"质量优先切分"** 是其核心差异点；任务模板化 RAG；自带 ESL 重排等【未核实：内部检索实现】
- 可借鉴：**"先理解版式再切分"的切块思想**——对 ndecon 的章节级确定性切分是上游经验的补集（本项目已用章节天然边界，但可吸收其"语义边界 + 表格/对话结构保护"规则）
- 依赖重量：**极重**（Go 后端 + 模型服务 + 多依赖）
- 借鉴判定：**禁止引入**；仅借"质量优先切分"思想（切块规则落 #13 时参考）

#### ⑦ GraphRAG — microsoft/graphrag
- 链接：https://github.com/microsoft/graphrag ｜ ★36,224 ｜ MIT ｜ Python ｜ 活跃（2026-10-05）
- 定位：**图基 RAG 系统**（微软，源自论文 "From Local to Global"）
- 架构要点：两阶段 **LLM 抽取实体/关系 → 构建知识图谱 → 社区检测（Leiden）→ 社区摘要**；检索分 Local（实体邻域）与 Global（社区摘要）两类
- 可借鉴：**"实体抽取 + 图 + 社区摘要"的全局性查询模式**；其"先离线建图、查询时只做轻量遍历"符合"无常驻服务"精神。但对 ndecon 而言属于 L3（实体图谱 + CHAPTER_COMMIT 投影）远未来事项
- 依赖重量：**重**（核心依赖 `datashaper`/`networkx`/`tiktoken`/`numpy` 等，且建图需 LLM 调用）【未核实】
- 借鉴判定：**禁止引入**；仅借 L3 图谱检索的"社区摘要 + 邻域遍历"算法思想

#### ⑧ LightRAG — HKUDS/LightRAG
- 链接：https://github.com/HKUDS/LightRAG ｜ ★39,983 ｜ MIT ｜ Python ｜ 活跃（2026-10-03，EMNLP2025）
- 定位：**轻量图 RAG**（图提取 + 双级检索：低层（具体实体）+ 高层（主题/摘要））
- 架构要点：比 GraphRAG 更"轻"，**图与向量共用同一存储**、增量更新；`query_type` 区分 `local`/`global`/`hybrid`；支持多种 LLM/ embedder 后端
- 可借鉴：其**"图抽取 + 向量复用同一存储、检索时 local/global 双路"的精简设计**，以及**增量更新图谱**思路，比 GraphRAG 更贴近本项目的"派生视图 + JSONL 真源"理念
- 依赖重量：**重**（依赖 `networkx`/`numpy`/`tiktoken` 及 embedder 适配）【未核实】
- 借鉴判定：**禁止引入**；仅借 L3 精简图检索思想

### 1.3 向量库（⚠️ 全部触碰"禁止常驻服务"红线，作为禁止项对照）

> 这三类是 ndecon roadmap 明确禁止的形态（常驻服务 / 重嵌入栈）。列出是为了**量化"我们放弃的是什么、换来什么"**，并在 #13 万一解冻论证时作为反例。

#### ⑨ Milvus — milvus-io/milvus
- 链接：https://github.com/milvus-io/milvus ｜ ★46,317 ｜ Apache-2.0 ｜ Go ｜ 活跃（2026-10-05）
- 定位：**云原生分布式向量数据库**（ANN 检索，HNSW / DiskANN）
- 借鉴判定：**禁止**（需独立常驻服务 + etcd/对象存储，运维成本与单人维护红线冲突）。ADR-0004 已点名 Milvus 一类禁止。

#### ⑩ Qdrant — qdrant/qdrant
- 链接：https://github.com/qdrant/qdrant ｜ ★34,931 ｜ Apache-2.0 ｜ Rust ｜ 活跃（2026-10-05）
- 定位：**向量搜索引擎 / 数据库**（Rust 实现，支持 HNSW + 标量/二进制量化 + 混合检索）
- 可借鉴：其**混合检索（sparse + dense 同库融合）**与**量化压缩**的产品化设计值得 L2 解冻时参考；但其"常驻服务"形态本身禁止
- 借鉴判定：**禁止常驻部署**；仅借"稀疏+稠密同库融合"思想（可借 sqlite-vec + numpy 自研等价物，见第 5 节）

#### ⑪ Chroma — chroma-core/chroma
- 链接：https://github.com/chroma-core/chroma ｜ ★29,438 ｜ Apache-2.0 ｜ Rust（核心）/ Python（客户端）｜ 活跃（2026-10-04）
- 定位：**面向 AI 应用的嵌入式/服务端向量数据库**
- 借鉴判定：**禁止常驻服务**；其"嵌入式内存模式"虽可进程内跑，但仍是独立重依赖栈，与 L2 限定栈（sqlite-vec/numpy）不符。写作向调研中的 AI-Novel-Writing-Assistant 即用它做可选 RAG。

### 1.4 评测（可借指标定义与脚本范式，重栈则需自研轻量版）

#### ⑫ Ragas — vibrantlabsai/ragas（原 explodinggradients/ragas）
- 链接：https://github.com/vibrantlabsai/ragas ｜ ★15,929 ｜ Apache-2.0 ｜ Python ｜ **活跃度偏低**（最近推送 2026-02-24）
- 定位：**LLM 应用评测框架**（RAG 指标的业界事实标准）
- 架构要点：`context_precision` / `context_recall` / `faithfulness` / `answer_relevancy` 四核心指标；基于 LLM-as-judge + 必要嵌入；支持语法树/LLM 双路径【未核实：最新指标集】
- 可借鉴：**指标定义（尤其是 `context_recall` 与 `faithfulness` 的判定协议）** 直接可映射为 ndecon 的"金标 Recall@10 + 幻觉自检"；但**完整 Ragas 依赖 `langchain`/`numpy`/`datasets` 等重栈，且 faithfulness 需调用 LLM**——不能直接引入
- 依赖重量：**重**（且评测指标本身常需外部 LLM/embed 额度）
- 借鉴判定：**仅借指标定义**；nddecon 已有 CC0 金标 + Recall@10 冻结入 CI 的做法优于"每次依赖 LLM 评测"，应坚持"确定性金标优先、LLM 评测可选"的本地化改造（见第 5 节自研评测脚本）

#### ⑬ FlashRAG — RUC-NLPIR/FlashRAG
- 链接：https://github.com/RUC-NLPIR/FlashRAG ｜ ★3,585 ｜ MIT ｜ Python ｜ 活跃（2026-10-01，WWW2025 Resource）
- 定位：**RAG 研究工具箱**（统一实现检索器/重排器/生成器 + 内置 30+ 数据集评测）
- 架构要点：`RAGPipeline` 模块化（retriever → reranker → generator）；内置大量即取即用的基准数据集与评测脚本；强调"可复现研究"
- 可借鉴：**其"把检索/重排/生成拆成可插拔模块 + 自带可复现评测"的工程结构** 是很好的本地金标评测脚本范本；自带数据集可用来 au**gment** 本项目 CC0 fixture 的多样性【未核实：数据集许可是否可商用】
- 依赖重量：**中-重**（依赖 `transformers`/`sentence-transformers`/`faiss` 等做真实检索实验）
- 借鉴判定：**仅借结构范式**；评测脚手架形态可借鉴，重依赖不引入

#### ⑭ AutoRAG — Marker-Inc-Korea/AutoRAG
- 链接：https://github.com/Marker-Inc-Korea/AutoRAG ｜ ★5,111 ｜ **NOASSERTION** ｜ TypeScript ｜ 活跃（2026-10-05）
- 定位：**自动化 RAG 管线搜索 / 优化**（在给定数据上自动选最佳检索-生成组合）
- 架构要点：把 RAG 组件当超参，用局部搜索自动挑最优 pipeline；面向"本地文件检索"场景
- 可借鉴：**"用本地金标自动比较不同检索配置"的方法论**——这正是 ndecon #13 解冻时该做的（L1 vs L2 在金标上 A/B），与其"自动寻优"思路一致；但其实现技术栈（TS + 重依赖）不可用
- 依赖重量：**重**
- 借鉴判定：**禁止引入、NOASSERTION 不引用代码**；借"金标驱动自动选型"方法论

### 1.5 组件库（最贴近"可借鉴"层，但需逐一审视依赖）

#### ⑮ bm25s — xhluca/bm25s
- 链接：https://github.com/xhluca/bm25s ｜ ★1,801 ｜ MIT ｜ Python ｜ 活跃（2026-10-03）
- 定位：**纯 Python 快速 BM25 检索库**（描述原文："Fast BM25 search in Python, powered by Numpy and Numba"）
- 架构要点：支持 Okapi / BM25L / BM25+ 等多种变体；索引可序列化到磁盘；主打比 `rank_bm25` 快
- 可借鉴 / 依赖：**关键发现——bm25s 依赖 `numpy` + `numba`（JIT）**。`numba` 是 LLVM JIT 重依赖，不在本项目约束内；而我们 L1 已是**纯 Python 中文 bigram BM25（零第三方依赖）**。因此：**不要引入 bm25s**（会新增 numba 重依赖），但它的"多 BM25 变体 + 索引序列化"是很好的**算法对照基准**——用于验证我们自研实现的打分正确性
- 借鉴判定：**仅作算法对照基准，不引入**；印证"纯 Python BM25 是本项目正确选择"

#### ⑯ sqlite-vec — asg017/sqlite-vec
- 链接：https://github.com/asg017/sqlite-vec ｜ ★8,161 ｜ Apache-2.0 ｜ C（SQLite 扩展）｜ 活跃（2026-05-18 主仓，扩展预编译包持续发）
- 定位：**可在任意环境运行的 SQLite 向量搜索扩展**
- 架构要点：作为 `.load_extension()` 加载的 SQLite 扩展，提供 `vec0` 虚拟表做 ANN / KNN；支持 float/int/binary 向量；**进程内、无常驻服务**；纯 C 编译为平台原生 `.so/.dll`
- 可借鉴：**这正是 ADR-0004 为 L2 钦定的"sqlite-vec/numpy"栈之一**。它满足"禁止常驻向量服务"——它是嵌入进程的 SQLite 扩展，不是网络服务。**这是本文中唯一被约束直接允许的外部检索组件（需以 extras 可选引入）**
- 依赖重量：**轻**（单原生扩展文件，无 Python 重依赖；需本机可加载扩展，CI 需预置二进制）【未核实：CI 跨平台二进制可用性】
- 借鉴判定：**L2 sanctioned 组件**——#13 解冻时的向量底座首选；搭配本地量化 embedding（如 bge-small-zh int8 ONNX，见写作向调研 goink 组合）做离线向量召回，再 RRF 融合 L1

#### ⑰ rerankers — AnswerDotAI/rerankers
- 链接：https://github.com/AnswerDotAI/rerankers ｜ ★1,638 ｜ Apache-2.0 ｜ Python ｜ 活跃（2025-12-20，近期推送较少）
- 定位：**轻量、低依赖的再排序统一 API**（描述原文："lightweight, low-dependency, unified API to use all common reranking and cross-encoder models"）
- 架构要点：统一接口封装多种后端（cross-encoder / ColBERT / FlashRank / Cohere/Jina API 等）；**部分后端轻（FlashRank 用 onnxruntime）、部分极重（transformers cross-encoder）**
- 可借鉴：**"重排作为可插拔后处理、且优先轻后端"的设计** 值得借鉴；但注意：**任何 transformer cross-encoder 后端都拉入 torch/sentence-transformers 重栈**，与约束冲突；仅 **FlashRank（onnxruntime）/ Jina HTTP API（走 httpx，已在依赖内）** 两类后端勉强可接入【未核实：rerankers 是否仍维护、FlashRank 后端是否仍可用】
- 依赖重量：**取决于后端**——轻后端 OK，重后端禁止
- 借鉴判定：**仅借"重排可插拔 + 优先轻后端"思想**；自研轻量重排见第 5 节（或直接用 Jina HTTP rerank 走 httpx，零新增重依赖）

#### ⑱ sentence-transformers — huggingface/sentence-transformers
- 链接：https://github.com/huggingface/sentence-transformers ｜ ★19,150 ｜ Apache-2.0 ｜ Python ｜ 活跃（2026-10-01）
- 定位：**SOTA 嵌入 / 检索 / 重排模型库**（原 sbert）
- 架构要点：统一 `SentenceTransformer` 接口加载数千个嵌入/rerank 模型；是几乎所有 RAG 向量栈的嵌入底座
- 可借鉴：**模型命名与加载协议** 是事实标准；但它本身是**重栈入口**（拉 torch/tokenizers/transformers/huggingface_hub 等），本地 8B 嵌入模型更与"离线轻量"红线冲突
- 依赖重量：**极重**
- 借鉴判定：**禁止作为依赖引入**；仅作"L2 若解冻需选定具体小模型（如 bge-small-zh int8 ONNX）"的选型参考，且模型权重须经 ADR 引入门 + 金标 A/B

#### ⑲ ColBERT — stanford-futuredata/ColBERT
- 链接：https://github.com/stanford-futuredata/ColBERT ｜ ★3,944 ｜ MIT ｜ Python ｜ **活跃度偏低**（最近推送 2025-10-14）
- 定位：**晚期交互（late-interaction）神经检索**（token-level 向量 + MaxSim）
- 架构要点：`ColBERT` 把 query/文档都编码成 token 向量，检索时做 token 级 MaxSim 求和；`RAGatouille` 是其易用封装【未核实：当前主版本】
- 可借鉴：**late-interaction 的思路（比单向量召回更细粒度）** 学术价值高，但**需 GPU/重模型，完全超出约束**；仅作 L2+ 远期算法储备
- 依赖重量：**极重**
- 借鉴判定：**禁止引入**；仅作远期算法知识

---

## 2. 横向对比表

| # | 项目 | ★ (2026-10-05) | 许可证 | 语言 | 定位 | 借鉴判定 | 不引入主因 |
|---|---|---|---|---|---|---|---|
| ① | LangChain | 147,451 | MIT | Py | 框架 | 仅借思想 | 重栈溢出 3 依赖 |
| ② | LlamaIndex | 52,411 | MIT | Py | 框架 | 仅借思想 | 重栈、多集成 |
| ③ | Haystack | 26,648 | Apache-2.0 | Py | 框架 | 仅借思想 | 重栈；哲学已印证现有路线 |
| ④ | txtai | 12,991 | Apache-2.0 | Py | 框架/一体化 | 仅借思想 | 中-重依赖 |
| ⑤ | Dify | 157,865 | NOASSERTION | TS/Py | 平台 | 禁止+不引代码 | 全栈平台、无许可 |
| ⑥ | RAGFlow | 91,688 | Apache-2.0 | Go | 引擎 | 仅借思想 | 极重；借"质量切分" |
| ⑦ | GraphRAG | 36,224 | MIT | Py | 图 RAG | 仅借思想(L3) | 重栈、需 LLM 建图 |
| ⑧ | LightRAG | 39,983 | MIT | Py | 图 RAG | 仅借思想(L3) | 重栈 |
| ⑨ | Milvus | 46,317 | Apache-2.0 | Go | 向量库 | **禁止** | 常驻服务/分布式 |
| ⑩ | Qdrant | 34,931 | Apache-2.0 | Rust | 向量库 | **禁止常驻** | 常驻服务 |
| ⑪ | Chroma | 29,438 | Apache-2.0 | Rust/Py | 向量库 | **禁止常驻** | 独立重栈 |
| ⑫ | Ragas | 15,929 | Apache-2.0 | Py | 评测 | 仅借指标定义 | 重栈、需 LLM 评测 |
| ⑬ | FlashRAG | 3,585 | MIT | Py | 评测/研究 | 仅借结构范式 | 中-重依赖 |
| ⑭ | AutoRAG | 5,111 | NOASSERTION | TS | 自动选型 | 禁止+不引代码 | 重栈、无许可 |
| ⑮ | bm25s | 1,801 | MIT | Py | 组件库 | 仅作基准不引入 | 引 numba 重依赖 |
| ⑯ | **sqlite-vec** | 8,161 | Apache-2.0 | C | 组件库 | **L2 sanctioned** | 轻、进程内、ADR 钦定 |
| ⑰ | rerankers | 1,638 | Apache-2.0 | Py | 组件库 | 仅借思想 | 重后端禁、轻后端可 |
| ⑱ | sentence-transformers | 19,150 | Apache-2.0 | Py | 组件库/底座 | 禁止 | 极重栈入口 |
| ⑲ | ColBERT | 3,944 | MIT | Py | 神经检索 | 仅借算法(L2+) | 极重、需 GPU |

**刻度参考**：★>10万=现象级；1万–10万=头部；3千–1万=成长型；<3千=利基/组件级。

---

## 3. 对本项目最有价值的"思想"提炼（不依赖任何外部代码）

1. **关键词召回是向量的标准一等公民**（Haystack InMemoryBM25、LlamaIndex BM25Retriever、txtai 混合）——本项目的 L1 纯词法先行路线被主流框架一致印证，应继续坚持。
2. **同索引双路 + 融合排序是工业共识**（txtai、Qdrant 混合检索）——L2 解冻时用 `sqlite-vec 向量 + L1 BM25` 同索引、RRF 融合，是既定且被佐证的方向。
3. **重排作为可插拔后处理、优先轻后端**（rerankers）——可只接 Jina HTTP rerank（走 httpx，零新增依赖）或自研规则重排，绝不全量 cross-encoder。
4. **质量优先切分**（RAGFlow DeepDoc）——#13 切块时吸收"语义边界 + 结构保护（对话/表格）+ chunk 重叠 + hash 去重"经验，已在写作向调研记录。
5. **图检索的"local 实体邻域 + global 社区摘要"双路**（GraphRAG/LightRAG）——是 L3 实体图谱 CHAPTER_COMMIT 的算法雏形，但属远未来、且需 LLM 建图，与当前约束不符。
6. **金标驱动自动选型**（AutoRAG/FlashRAG 范式）——#13 解冻的正确做法：在 CC0 金标上自动 A/B 比较 L1 vs L2，而非"感觉更高级"就上向量。

---

## 4. 决策映射小结

| 现状/计划 | 外部证据 | 结论 |
|---|---|---|
| L1 纯词法先行、无嵌入可跑 | Haystack/LlamaIndex/txtai 均设无嵌入词法兜底 | 继续坚持，已被印证 |
| L2 限 sqlite-vec/numpy、禁常驻服务 | sqlite-vec 恰好是进程内轻扩展；Milvus/Qdrant/Chroma 常驻被禁 | sqlite-vec 是 sanctioned 底座 |
| 零重依赖默认安装 | 上述 19 个中 18 个都重栈；仅 sqlite-vec 轻且被 ADR 钦定 | 新依赖一律走 extras；默认零依赖 |
| 确定性金标优先于 LLM 评测 | Ragas 等需 LLM 评测、重栈；本项目已有 CC0 Recall@10 冻结 CI | 坚持本地确定性金标，LLM 评测仅可选 |
| 禁止拷贝 GPL/AGPL/NOASSERTION 代码 | Dify/AutoRAG 为 NOASSERTION；写作向多个 GPL/AGPL | 仅 clean-room 学思想 |

---

## 5. 零重依赖可自研实现的组件清单（核心交付）

> 前提：仅用 `pydantic` / `typer` / `httpx` + 标准库 + （L2 期）`sqlite-vec`/`numpy`。下列组件均可**不新增任何重依赖**自研。每个注明复杂度与坑。

### 5.1 中文 BM25 变体（已完成 L1，可作基准对照）
- **自研内容**：中文 bigram 分词 → 词项频率 → Okapi/BM25+ 打分；我们已有纯 Python 实现（零依赖）。
- **复杂度**：低（已实现）。扩展 BM25L/BM25+ 仅是 IDF/饱和函数微调。
- **坑**：
  - bigram 切分对单字/专名召回弱（已知短板，用金标量化而非粉饰）；
  - **IDF 平滑与 k1/b 超参**需对中文语料调；
  - 与 bm25s 这类"带 numba"实现做**打分一致性对照**，避免自己实现出数值 bug（用对方做 oracle 基准，但不引入其依赖）。

### 5.2 查询改写规则（Query Rewriting / HyDE-lite）
- **自研内容**：基于规则的改写——同义词扩写（内置网文同义词表）、实体别名归一、将"伏笔/金手指/人设"等查询词映射到结构化字段名；**不依赖 LLM 的确定性改写**优先，LLM 改写作为可选 extras。
- **复杂度**：中（规则库维护量随时间增长；可先用 pydantic 模型固化规则 schema）。
- **坑**：
  - 规则过宽会**召回噪声**，需金标上的 precision 监控；
  - 别名归一要区分"同名异义"（如两个"林"姓角色），应带实体消歧上下文；
  - 避免"改写后偏离作者原意"——改写只扩召回，不改生成。

### 5.3 融合排序 RRF（Reciprocal Rank Fusion）
- **自研内容**：`score = Σ 1/(k + rank_i)`，对 BM25 路 与（L2 期）向量路独立排序后融合；k 默认 60。
- **复杂度**：低（十几行）。
- **坑**：
  - **各路必须返回稳定 rank**（不能混用原始分数直接加权，分数量纲不同）；
  - k 取值影响长尾，建议金标上扫参；
  - 单路（仅 L1）时 RRF 退化为原排序，需保证"向量失败时确定性回退 L1"不破坏融合逻辑（ADR-0004 已要求）。

### 5.4 小型金标评测脚本（Golden Eval Harness）
- **自研内容**：用 pydantic 固化 `Query`/`ExpectedChapter` fixture；跑检索 → 计算 Recall@k、MRR、HitRate；结果 JSON 落盘并冻结基线（本项目已有 CC0 金标 Macro Recall@10=0.875 入 CI）。
- **复杂度**：中（fixture 构造成本最高，但一次投入长期受益）。
- **坑**：
  - **金标必须可复述、可审计**（CC0 合成数据，避免版权）；
  - 指标分母要正确处理"无应召回章节"的查询，否则 Macro 均值失真；
  - 不要效法 Ragas 用 LLM 做 faithfulness 评测塞进 CI——CI 零额度；LLM 评测只做本地可选人工复核。

### 5.5 轻量重排（规则 / Jina HTTP）
- **自研内容**：(a) 纯规则重排——按"章节邻近度（前章加权）+ 字段类型（人设/伏笔 > 摘要）+ 命中密度"打分；(b) 可选 Jina rerank API（**走 httpx，已在依赖内**，零新增依赖），作为 extras 可选。
- **复杂度**：低-中。
- **坑**：
  - 规则重排权重需金标调，否则可能压错顺序；
  - 若接 Jina HTTP，需处理**网络失败确定性回退**（断网时退回规则重排），且**不把外部 rerank 当作可信唯一路径**；
  - 切勿引入 rerankers 的 transformer cross-encoder 后端（拉 torch 重栈，违反约束）。

### 5.6 sqlite-vec 向量召回外壳（L2 解冻时，非现在）
- **自研内容**：以 extras 引入 `sqlite-vec`，建 `vec0` 虚拟表存章节嵌入；嵌入来源限定**本地量化小模型**（如 bge-small-zh int8 ONNX，离线推理），禁止外部 embed 服务；与 L1 同索引 RRF 融合。
- **复杂度**：中（需处理扩展加载、跨平台二进制、嵌入增量索引）。
- **坑**：
  - **必须先过 ADR-0004 引入门**：L1 在扩充金标上 Recall@10 被证不足才解冻，不留"感觉更高级"空间；
  - 扩展二进制在 CI 跨平台可用性是真实风险【未核实】，需预置/缓存；
  - 向量失败必须**确定性回退 L1**，离线回环永不破坏；
  - 嵌入模型权重仍是"重资产"，须经金标 A/B 与版权/许可审查（Apache-2.0 模型优先）。

### 5.7 增量索引与去重（复用自研）
- **自研内容**：章节提交后增量更新 L1 索引（已部分实现）；#13 时向量侧做 chunk hash 去重防重建重复（写作向调研已记 RAGFlow/中文教程经验）。
- **复杂度**：中。
- **坑**：增量更新要**保证可重算**——索引是派生物，真源是 JSONL；重建必须与真源一致。

---

## 6. 许可证合规速查

| 许可证 | 本文涉及项目 | 对本项目（Apache-2.0）含义 |
|---|---|---|
| MIT | LangChain/LlamaIndex/GraphRAG/LightRAG/FlashRAG/bm25s/ColBERT/Ragas(旧)/sqlite-vec(否) | 可参考/复用代码，保留声明 |
| Apache-2.0 | Haystack/txtai/RAGFlow/Milvus/Qdrant/Chroma/Ragas/FlashRAG(否)/sqlite-vec/rerankers/sentence-transformers | 可参考/复用，保留声明与 NOTICE |
| NOASSERTION | Dify / AutoRAG | 视同无许可，**不引用代码**，仅学思想 |
|（GPL/AGPL，见写作向调研）| webnovel-writer/AI_NovelGenerator/goink 等 | 只能 clean-room 学思想，禁止拷代码 |

---

## 7. 来源清单（均于 2026-10-05 经 GitHub API 抓取核验元数据）

**框架 / 引擎 / 平台**
1. https://github.com/langchain-ai/langchain
2. https://github.com/run-llama/llama_index
3. https://github.com/deepset-ai/haystack
4. https://github.com/neuml/txtai
5. https://github.com/langgenius/dify
6. https://github.com/infiniflow/ragflow
7. https://github.com/microsoft/graphrag
8. https://github.com/HKUDS/LightRAG

**向量库（禁止项对照）**
9. https://github.com/milvus-io/milvus
10. https://github.com/qdrant/qdrant
11. https://github.com/chroma-core/chroma

**评测 / 研究**
12. https://github.com/vibrantlabsai/ragas （原 explodinggradients/ragas）
13. https://github.com/RUC-NLPIR/FlashRAG
14. https://github.com/Marker-Inc-Korea/AutoRAG

**组件库**
15. https://github.com/xhluca/bm25s
16. https://github.com/asg017/sqlite-vec
17. https://github.com/AnswerDotAI/rerankers
18. https://github.com/huggingface/sentence-transformers
19. https://github.com/stanford-futuredata/ColBERT

**交叉引用**
- 写作向调研（含论文与小说创作型项目）：[`../research/2026-09-25-rag-writing-landscape.md`](../research/2026-09-25-rag-writing-landscape.md)
- 约束来源：[`adr/0004-rag-tiers-and-adoption-gates.md`](adr/0004-rag-tiers-and-adoption-gates.md)、[`../pyproject.toml`](../pyproject.toml)、[`../roadmap.md`](../roadmap.md)

---

## 8. 一句话收尾

**19 个主流 RAG 开源项目中，18 个因重栈/常驻服务/许可证触碰本项目硬约束而无法引入；真正被约束直接允许且已获 ADR 背书的外部组件只有 `sqlite-vec`（L2 向量底座）。本项目正确的 RAG 进阶路线是：坚持 L1 纯词法（已被主流框架一致印证）→ 用第 5 节的零依赖自研组件（BM25 变体 / 查询改写 / RRF / 金标评测 / 轻量重排）持续增强 → 仅当金标证不足时以 sqlite-vec 解冻 L2 并确定性回退。**

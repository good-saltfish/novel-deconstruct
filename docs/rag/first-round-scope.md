# novel-deconstruct · RAG 核心化第一轮落地范围

> 文档性质：复用既有调研（t1 前沿论文 / t2 开源生态 / t3 能力审计）+ 今日 arXiv 快照（2026-10-06）后的**范围与顺序决策**，供 captain 拍板与下游任务拆解。
> 配套输入（已读，不重做）：
> - `docs/rag/frontier-papers.md`（t1，21 篇，含「现在做/L2触发/L3触发/短期不做」分档）
> - `docs/rag/open-source-landscape.md`（t2，19 个开源，零重依赖自研组件清单在第 5 节）
> - `docs/rag/current-capability-audit.md`（t3，六环缺口、金标 0.875、P0/P1/P2/P3）
> - `冲刺/每日学习/rag/`（01-07 主线、精读 00-09、RAG 精通教程、资料 50+ PDF）
> - `冲刺/每日学习/rag/工具/快照/RAG前沿_20261006.md`（25 篇新增，均标【未核实】）
> - 约束根因：`docs/adr/0004-rag-tiers-and-adoption-gates.md`、`docs/roadmap.md`、`pyproject.toml`

---

## 0. 一句话范围结论

**第一轮只做「把现有 L1 链路接通 + 补可观测 + 补评测维度 + 补学习侧金标」这一层零依赖、零金标成绩变动的修复与增强（P0+P1）；L2 向量检索（#13）严格留在冻结档，解冻前置是「真实 dogfood 成规模零字面改写落空」证据，而该证据只能由 P1 埋点先于任何 L2 产出。** 所有 P0/P1 均不新增依赖、不改检索打分逻辑、不碰冻结金标 `baseline_report.json`、不破坏版权边界。

---

## 1. 三档总览

| 档位 | 含义 | 包含项 |
|---|---|---|
| **现在做** | 零依赖、零金标成绩变动、不碰红线，本轮直接落地（P0+P1+研究印证类） | P0①回路接通 ②tools.py:229 过滤 ③KB 渲染统一 ④未确认进索引策略 ⑤reindex 死写改按需；P1 埋点 / 评测维度扩展 / KB 侧金标；研究印证类（LongRAG/RAG-vs-LC/Modular/中文三件套补规范/Lost-in-the-Middle 排布/RRF 接口位） |
| **有触发条件再做** | 动金标或冻结项，须满足显式触发条件方可启动 | **#13 L2 向量混合检索（冻结档，强制）**；P2 规则重排 / 查询改写 / 上下文预算；P3 RRF 多路启用；roadmap #4/#6/#10/#11/#12；研究侧 ColBERT/M3/HyDE/Query2Doc/Cross-Encoder/CRAG/Self-RAG/GraphRAG/HippoRAG/RAGAS 等 |
| **不做（短期）** | 投入产出低或被更简单方案覆盖，或硬红线永不 | 引用参考书原文/quote；常驻向量服务/重依赖；正文接自主 agent 循环；Rewrite-Retrieve-Read 训练 / RankRAG 微调 / ARES 裁判训练；roadmap B token 成本追踪（待 A） |

---

## 2. 现在做（本轮落地，含可验收标准）

> 公共红线：全部项「不引入常驻向量服务 / 不新增第三方重依赖（pyproject.toml 禁改）/ KB 不存版权原文 / AI 未配置必须报错不得静默降级 Fake / 不擅改 docs/roadmap.md」。每项均给出可跑出的命令或可观测指标作为验收。

### 2.1 P0 — 接通断链与修正（t3 §7.3 / §8）

**P0① 接通「已写章节→摘要→索引」回路**
- 位置：`panel/server.py:808` 与 `agent/tools.py:228` 调用 `build_context_pack` 时不传 `summaries`；创作链写正文只落 `manuscripts/chNNN.md` + `ManuscriptRecord`，从不产出 `ChapterSummary`（t3 defect #3）。
- 做法：写完一章后派生 `ChapterSummary`（gist + 情节点），经 `indexer.documents_from_summaries()` 进入索引；`build_context_pack` 在生产侧真正收到它。不动 `bm25/tokenize`，只补接线。
- 理由：这是 L1「前章一致性」核心回路，目前在生产上断开，`_previous_summary` 永远走 `outline` 退化分支；纯补断链，不改打分逻辑，金标（只跑骨架语料）不受影响。
- 验收：`python -m pytest tests/unit/test_retrieval_eval_gold.py -q` 仍 **4 passed**；新增 `tests/unit/test_context_pack_summaries.py` 断言 `summary-chapter-*` 进入 `pack.retrieved`；真实项目 `ContextPack.index_doc_count` 含 summary 文档。

**P0② 修 `agent/tools.py:229` 的 `doc_type` 过滤**
- 现状：`hits = [d for d in pack.retrieved if d.doc_type != "summary_chapter"]`，但真实值是 `chapter_summary`（`indexer.py:141`），条件恒为真，过滤形同虚设（t3 defect #1）。
- 做法：改为 `doc_type != "chapter_summary"`（或按意图决定是否返回已写章节摘要给 Agent）。
- 理由：一行修正；当前等于无过滤，`SearchOwnBook` 会把已写章节摘要也返回给 Agent，与注释意图相反。
- 验收：新增断言 `SearchOwnBook.run` 返回的 hits 不含 `doc_type=="chapter_summary"`；`grep -rn "summary_chapter" src/` 无残留错误字面；`pytest` 绿。

**P0③ 统一三份 KB 渲染实现**
- 现状：`kb/corpus.py:190` / `creation/prompts.py:86` / `creation/plans.py:74` 三份 `knowledge_brief` 重复且措辞/截断（400/400/300）不一致（t3 defect #4、#5）。
- 做法：抽成单一渲染函数 + 统一截断与版权尾句；同步 `prompt_version`。
- 理由：纯重构，不改检索语义；避免「改一处漏两处」与「同一资料在不同路径喂给模型量不同」。
- 验收：`grep -rn "def knowledge_brief" src/` 仅剩 1 处；`pytest` 绿；Agent 侧 300 字与其余 400 字统一。

**P0④ 处理「骨架未确认→不进索引」的体感问题**
- 现状：`documents_from_creation()` 只收 `confirmed_parts=True` 的部件；真实 3 个项目 `confirmed_parts` 大面积为空（仅 1 个确认了金手指），导致 L1 索引常只剩 1–2 文档（t3 §7.4 第 3 点）。
- 做法：明确策略——（A）未确认部件也进索引（带候选不代笔纪律约束），或（B）保持仅确认但前端提示确认率。策略需维护者拍板；代码侧给出明确、可测的行为。
- 理由：属接线/策略问题，不动打分逻辑；是 P0 接线之外最影响体感的一点。
- 验收：策略以文档+代码开关固化；真实项目 `index_doc_count` 不再恒为 1–2；`pytest` 绿。

**P0⑤ `ndecon reindex` 死写改按需 rebuild**
- 现状：`ndecon reindex` 落 `projects/<id>/index.json` 无任何读取方（t3 defect #2）；每次 `build_context_pack` 现场重建索引。
- 做法：改为索引作为 `build_project_index()` 的派生物，按需 rebuild 并由 `build_context_pack` 复用；移除/保留 `ndecon reindex` 但明确其只重建可被读取的索引。
- 理由：链路未闭环；创作项目规模小性能不成问题，但应消除「写死无读方」。
- 验收：`grep -rn "read_project_artifact\|index.json" src/` 证明 index 有读取方，或 `ndecon reindex` 改为触发 `build_context_pack` 复用的 rebuild；`pytest` 绿。

### 2.2 P1 — 可观测与评测维度（t3 §7.3，可独立先做，不依赖 roadmap 触发）

**P1① 检索埋点（先于任何 L2）**
- 做法：每次检索记录 query / 命中数 / top1 与 top-k 分数 / 零命中次数 / 注入字符数，落到项目目录 JSONL。零依赖、纯新增。
- 理由：这是「检索侧可观测」，与 roadmap B「token 成本追踪」是两件事，不依赖 roadmap A；且**它是 #13 解冻、B 立项、所有「未验证」项的共同前置**——没有埋点就没有「真实 dogfood 成规模零字面改写落空」证据。
- 验收：新增 `tests/unit/test_retrieval_observability.py`；跑一次生成后 `.ndecon-workspace/projects/<id>/retrieval-log.jsonl` 含 query/hits/top1_score/zero_hit；`pytest` 绿。

**P1② 评测维度扩展（另存 extended，不碰冻结基线）**
- 做法：给 `eval_gold.py` 加 MRR / NDCG / HitRate 与多档 k（@1/@3/@5/@10），新增指标**另存 `baseline_report_extended.json`**。
- 理由：纯新增字段；保留 `Recall@10=0.875` 原样冻结，不动 `baseline_report.json`，避免 `test_report_matches_committed_baseline` 变红。
- 验收：`baseline_report.json` 字节不变（该用例仍过）；`baseline_report_extended.json` 含 MRR/NDCG/HitRate@1/3/5/10；`pytest` 绿。

**P1③ 知识库侧金标集（L0.5）**
- 做法：自建 15–20 条「方法论查询→应命中资料块」判定（沿用 CC0/自造 fixture 纪律），补 eval 与冻结基线。
- 理由：ADR-0004 引入门①要求「先有金标才允许引入新检索层」，目前只有一致性侧有金标，学习侧裸奔（29,277 块零判定）。
- 验收：`tests/unit/test_kb_eval_gold.py` 绿；KB macro_recall 冻结值入 CI。

### 2.3 研究印证类（frontier-papers.md 标「现在做」项在本范围的落点）

| 研究项（frontier-papers §10） | 本范围落点 | 理由 / 验收 |
|---|---|---|
| 1.3 LongRAG（长检索单元） | 现在做（架构印证，写进 ADR/学习文档） | 章节级 doc 已与 LongRAG 同构，零新增依赖；验收=文档补丁。 |
| 2.4 RAG-Fusion 的 RRF 部分 | 现在做（RRF 融合接口位，k=60，单路退化为恒等） | 纯算法零依赖；验收=单测断言单路不改变排序与金标。`*多路启用*`见 §3 冻结档。 |
| 3.3 重排收益边界（规则重排） | **显式排除出现在「现在做」**，改归 §3 有触发条件 | 见 §3 冲突说明：规则重排会改变金标 macro_recall，基线冻结，需先立 extended baseline 并维护者重冻。 |
| 4.1 RAG vs Long-Context | 现在做（架构印证，写进 ADR/学习文档） | 支撑既有 L0–L3 分层付费；验收=文档补丁。 |
| 4.2 Lost in the Middle | 现在做（ContextPack 片段首尾排布） | 指导 `build_context_pack` 把最相关命中/开放伏笔放包首尾；验收=单测断言最高分命中位于 index 0、开放伏笔块位于尾。 |
| 5.3 金标 Recall@k 口径 | 现在做（已落地门槛，本范围坚守） | `eval_gold.py` + 0.875 冻结已就位；本范围只守不破。 |
| 8.1 Modular RAG | 现在做（架构语言，写进 ADR/学习文档） | 统一 L0–L3 模块化词汇；验收=文档补丁。 |
| 9 中文三件套（bigram+受控词+伏笔回收） | 现在做（已实装，补规范） | 三件套已落地（`tokenize.py`/`CONTROLLED_TERM_BOOST=2.0`/`foreshadow.py`）；本轮补规范文档。扩展到同义词/别名改写见 §3。 |

---

## 3. 有触发条件再做（含冻结档）

### 3.1 #13 L2 向量混合检索 —— 强制归入冻结档

**触发条件原文（引自 `docs/roadmap.md` 冻结项表，第 28 行）：**

> `#13 | L2 向量混合检索（限 sqlite-vec/numpy） | L1 金标 Recall@10=0.875 被证明不足（真实 dogfood 出现成规模零字面改写查询落空）`

**当前解冻状态判定（t3 §7.1）：**
- ADR-0004 通用引入门**①②已满足**：① 先有金标集与 Recall@10 基线（16 条 CC0 金标 + 0.875 冻结基线在位 ✓）；② Fake-first 离线回退（Fake 双轨齐全 ✓）。
- **③④是约束不是门槛**。
- **缺的半句**：「真实 dogfood 出现成规模零字面改写查询落空」尚无证据——目前只有 fixture 里 2 条**人造**难例（g15/g16，零字面重合），没有任何真实 dogfood 证据。
- **结论：#13 仍是冻结项，本轮不碰。** 且 g15/g16 是 #13 解冻的两条零字面改写难例依据，金标 `FROZEN_MACRO_RECALL=0.875` 冻结不可动。

**顺序约束：P1 埋点先于任何 L2。** 没有埋点就没有「真实 dogfood 成规模零字面改写落空」的量化证据，#13 无从解冻。即便 L1 金标门槛已过，dogfood 门槛未过，0.875 本身还带「12 章语料 k=10 饱和、金标集偏向第 5/8 章」两个失真，用它证明「不足」证据不够硬。

**解冻后再做的内容（已定方向，非本轮）**：sqlite-vec/numpy 向量底座（t2 §5.6）+ L1 BM25 同索引 RRF 融合（t2 §5.3）+ 可选轻后端重排（t2 §5.5）；向量失败确定性回退 L1，离线回环永不破坏。

### 3.2 其余 roadmap 冻结项（roadmap.md 冻结项表，均不主动碰）

| Issue | 内容 | 解冻条件（原文） |
|---|---|---|
| #4 | Stage 4 拆书侧设定/角色档案 | 继续大规模拆书且 Planner Agent 的创作侧设定能力不够用时 |
| #6 | Stage 5 汇总报告 / Stage 6 文风分析 | #3/#4 有真实产物验证后 |
| #10 | 短篇拆书管道 | 长篇流程连续自用 4 周后另开，绝不与长篇混库 |
| #11 | 持续学习深化（跨书卡片库/差距对照） | L0.5 知识库被证明不足 |
| #12 | GUI/TUI 升级 | 面板被证明不足（当前 #15 面板够用） |

### 3.3 P2/P3 —— 会动金标成绩，需先立 extended baseline 才做（t3 §7.3）

| 项 | 触发条件 | 与研究的对应 |
|---|---|---|
| P2 轻量重排（规则式：文档类型权重 chapter_summary>outline>positioning、章节邻近度、命中密度） | 先立 P1② `baseline_report_extended.json` + 维护者确认是否重冻基线 | frontier-papers §3.3 标「现在做（规则重排）」——**此处显式冲突**：规则重排会改变 `macro_recall`，而 `baseline_report.json` 冻结，故下调为「有触发条件再做」。理由：金标成绩是冻结资产，任何改动须先有 extended baseline 并走重冻流程，不能现在直接动。 |
| P2 查询改写规则（非 LLM：同义词/别名扩写、实体归一、`controlled_terms` 扩展到角色/地名/金手指名） | 同上（extended baseline） | frontier-papers §9「现在做（三件套）/触发（稠密与改写）」——三件套已实装，扩展到同义词改写归触发档，一致。 |
| P2 上下文长度预算（字符预算 + 类型配额，超出按分数截断） | 需先有 P1① 埋点知道真实分布 | roadmap 点名的盲区项。 |
| P3 RRF 多路启用（k=60，单路退化为恒等） | 随 L2 解冻一并启用 | frontier-papers §2.4「现在做(RRF)」= 本范围 §2.3 已落「接口位」，多路启用随 #13。 |

### 3.4 研究侧「触发条件/不做」项（frontier-papers.md 分档，本范围守其边界）

- **触发条件再做**：ColBERT（1.1）/ M3-Embedding（1.2）/ HyDE（2.1）/ Query2Doc（2.2）/ RAG-Fusion 多路生成（2.4）/ Cross-Encoder 重排（3.1）/ Self-RAG 规则化（6.1）/ CRAG 规则化（6.2）/ Agentic RAG（6.3）/ GraphRAG（7.1）/ HippoRAG-PPR（7.2）/ RAGAS 离线评测（5.1）。均须满足 ADR-0004 引入门 + 带 L1 回退，且多依赖 key/模型，不在本轮。
- **不做（短期）**：Rewrite-Retrieve-Read 可训改写（2.3）/ RankRAG 微调（3.2）/ ARES 裁判训练（5.2）——投入大且被更简单方案覆盖，列入 L3 或路线图候选。
- 今日 arXiv 快照（2026-10-06，25 篇新增均【未核实】）的增量启示（投毒暴露控制 / Agentic 评测预算 / 规范嵌入对齐 / RAGStress 鲁棒性基准）仅作**未来 L2/L3 参考提示**，未进入任何落地判定，需后续精读核验。

---

## 4. 不做（短期 / 硬红线）

| 项 | 性质 | 依据 |
|---|---|---|
| 检索参考书原文 / quote 辅助写作 | **永不**（硬红线） | ADR-0004 放弃方案；KB 不存原文、学习检索不出原文 quote |
| 引入常驻向量服务 / 重依赖 | **永不**（硬红线） | 离线红线 + pyproject 三依赖约束（pydantic/typer/httpx） |
| 正文生成接自主 agent 循环 | **永不**（硬红线） | ADR-0006「ContextPack 的确定性与版权边界不可破坏」 |
| 多 provider 自动回退 / 静默从 AI 降级 Fake | **永不**（硬红线） | 未配置必须明确 400 引导，不得静默降级 |
| 面板绑非回环地址 / 加外部 CDN | 不做 | roadmap 第四节 |
| token 成本追踪（roadmap B） | 待 A | 前置依赖 A：没有真实调用数据前做是凭空设计 |

---

## 5. 禁止事项（本轮不可逾越，逐条锁死）

1. **不擅改 `docs/roadmap.md`** —— 用户冻结的未来计划文件；其事实性笔误见 §6，由用户拍板。
2. **不引入常驻向量服务** —— 禁 Milvus/Qdrant/Chroma server/外部 embed 服务（t2 §1.3）。
3. **不新增第三方重依赖** —— `pyproject.toml` 禁改；新依赖只能以 `extras` 可选引入，默认零重依赖。
4. **KB 不存版权原文** —— 学习型检索只出聚合数字与方法论片段，不出原文 quote（`exclusion.py` 多重硬排除 + 类型级/字段级闸门守住）。
5. **AI 未配置必须报错，不得静默降级 Fake** —— `dogfood-checklist.md` §0.4 的红线：若 AI 路径静默降级成 Fake 出结果，立刻记 issue，不是 bug 当正常。

---

## 6. 待用户确认的文档修订（本轮不自动改）

> 下列均为**文档事实错误**，不改代码、不阻塞 P0/P1 落地；但 `docs/roadmap.md` 是用户冻结文件，`dogfood-checklist.md` 与之口径一致，**本轮一律不自动改**，单列此处由用户拍板。

| # | 文件:行 | 现状（错误/过时） | 真实情况 | 建议 |
|---|---|---|---|---|
| 1 | `docs/roadmap.md` §二（第 37 行） | 「AI 路径从未用真实模型端到端跑过」 | 2026-09-25 真实跑通过：`随便-231725/manuscripts/ch001.md` 是 deepseek-chat 产出 2867 字正文；骨架 AI 路径亦跑通过（t3 §7.4） | 改为「AI 路径已用真实模型跑通过，但缺检索埋点度量」 |
| 2 | `docs/roadmap.md` §一（L0.5 知识库行）/ §三 A（第 16、50 行） | KB「290 文件 / 34639 块 / 127 排除」 | 真实产物（2026-10-04 建库）为 **306 / 29277 / 466**（补 markdown 章节体规则后排除数从 127 涨到 466）（t3 §2.4、defect #6） | 同步为 306/29277/466 |
| 3 | `docs/roadmap.md` §一（工程基线行，第 18 行） | 「159 测试全绿」 | 审计实测 **219 passed in 20.22s**（t3 §9 defect #7；`dogfood-checklist.md` 第 4 行「159…共162」同样过时） | 同步为 219 passed |
| 4 | `docs/dogfood-checklist.md` 第 16 行 | 建库命令写「`ndecon reindex`」 | 正确命令为「`ndecon kb index`」（reindex 是创作项目索引，非知识库建库） | 改为 `ndecon kb index` |

---

## 7. 顺序与验收节奏（给 captain 的执行建议）

1. **先 P0 五条接线/修正**（断链+过滤 bug+渲染统一+未确认策略+reindex 死写）——不动打分、不动金标，风险最低。
2. **再 P1 三条**（埋点 → 评测维度扩展 → KB 侧金标）——都不依赖真实 LLM，且 P1① 埋点是 #13 解冻与 roadmap B 的共同前置。
3. **研究印证类**随 P0/P1 顺带落（RRF 接口位、Lost-in-the-Middle 排布、三件套补规范、LongRAG/RAG-vs-LC/Modular 文档）。
4. **#13 及一切「动金标」的 P2/P3 严格留在冻结/触发档**，等 P1 埋点跑出「真实 dogfood 成规模零字面改写落空」证据，且立好 `baseline_report_extended.json` 后再议。
5. 全程守 §5 五条禁止事项；§6 文档修订交用户拍板，不自动改。

---

## 8. 与三份调研的一致性核对（防冲突）

- **frontier-papers.md「现在做」9 项**：LongRAG/RAG-vs-LC/Modular/中文三件套 = 本范围 §2.3 现在做（文档印证/补规范）；RAG-Fusion RRF = 现在做接口位（多路启用随 #13）；Lost-in-the-Middle = 现在做排布；金标口径 = 现在做且坚守。**唯一冲突项「规则重排（§3.3）」已显式排除出现在现在做、改归 §3.3 有触发条件，理由：改金标成绩+基线冻结。**
- **open-source-landscape.md**：19 个开源 18 个禁引，唯一 sanctioned 组件 `sqlite-vec` 仅作 #13 解冻后的 L2 底座（§3.1），本轮不引入；第 5 节零重依赖自研组件清单（BM25 变体/查询改写/RRF/金标评测/轻量重排）与 P0/P1/P2/P3 一一对应，无新增依赖。
- **current-capability-audit.md**：P0/P1/P2/P3 优先级与冻结项表（§7.2/§7.3）被本范围原样吸收；金标 0.875、g15/g16 难例、#13 解冻条件均照引。

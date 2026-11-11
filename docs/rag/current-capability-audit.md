# novel-deconstruct 现有检索/RAG 能力审计

- 审计日期：2026-10-05
- 审计人：rag-core-engineer（AgentTeams 自动任务 t3）
- 性质：**只读审计**。本文不修改任何代码；文中"应该修"的条目均为建议，落地需另开任务。
- 代码口径：`novel-deconstruct` 工作区，`src/ndecon/` 全部模块逐个读源码确认；调用点用 grep 全仓核验；金标成绩为**本次现场重跑**实测，不是引用旧数字。
- 配套文档：
  - [`adr/0004-rag-tiers-and-adoption-gates.md`](../adr/0004-rag-tiers-and-adoption-gates.md) —— 分层与引入门（本文第四节的判定依据）
  - [`adr/0005-writing-knowledge-base.md`](../adr/0005-writing-knowledge-base.md) —— L0.5 知识库
  - [`../roadmap.md`](../roadmap.md) —— 冻结项与解冻条件
  - [`open-source-landscape.md`](open-source-landscape.md) —— 外部组件可借鉴性评估（teammate t2 产出，本文第五节的"自研补法"来源）

---

## 0. 一句话结论

**项目已有一条完整、可重算、版权干净的 L1 检索链路（bigram BM25）与 L0.5 知识库链路，但它们是两条互不相交的管道：一致性检索（本书）只服务章节正文生成这一个调用点，学习型检索（知识库）只服务骨架/设定/细纲三个调用点；六环 RAG 能力里"召回"和"上下文组装"有实装、"评估"有一条金标基线，其余三环（重排/查询优化/可观测）为零。** 更关键的是：**已写章节摘要从未进入索引**（生产链路从不传 `summaries`、`reindex` 写的索引无人读取），所以"写了第 5 章再写第 6 章时检索第 5 章"这条 L1 最核心的回路，目前在生产上是**断开**的。

---

## 1. `src/ndecon/retrieval/` —— 六个模块职责与实际调用链

### 1.1 模块职责表

| 模块 | 行数 | 职责 | 关键常量/入口 |
|---|---|---|---|
| `tokenize.py` | 48 | 零模型/零词典/零网络的确定性中文分词：CJK 取 bigram、英数取整串 | `normalize_text()`（NFKC）、`tokenize(text)` |
| `bm25.py` | 167 | 纯 Python Okapi BM25 索引与检索，含受控词加权与可序列化格式 | `BM25_K1=1.5`、`BM25_B=0.75`、`CONTROLLED_TERM_BOOST=2.0`、`INDEX_VERSION=1` |
| `indexer.py` | 177 | 把创作项目与章节摘要转成索引文档；**版权闸门所在** | `OUTLINE_DOC_PREFIX="outline-chapter-"`、`SUMMARY_DOC_PREFIX="summary-chapter-"`、`build_project_index()` |
| `context.py` | 159 | 章节生成前的确定性上下文包组装 | `SNIPPET_LIMIT=120`、`DEFAULT_TOP_K=10`、`build_context_pack()` |
| `foreshadow.py` | 117 | 伏笔台账：从细纲显式章尾钩提取、按字面重叠判定回收 | `RESOLVE_THRESHOLD=0.6`、`EVIDENCE_LIMIT=120`、`extract_foreshadows()` |
| `eval_gold.py` | 112 | CC0 金标集上的 Recall@k 评测与报告生成 | `TOKENIZER_ID="cjk-bigram-v1"`、`REPORT_VERSION=1`、`run_gold_eval()` |

### 1.2 `tokenize.py`：确定性分词

- `_RUN_RE = re.compile(r"[一-鿿]+|[A-Za-z0-9]+")`：先把文本切成"纯 CJK 串"与"纯英数串"，标点与空白直接丢弃。
- `_cjk_bigrams(run)`：长度 1 的 CJK 串退化为单 token；否则 `[run[i:i+2] for i in range(len(run)-1)]`（滑窗 bigram）。
- `tokenize()` 先 lowercase 再切。例：「第3章 路灯，ＬＩＧＨＴ」→ `['第', '3', '章', '路灯', 'light']`（全角 ＬＩＧＨＴ 经 NFKC 归一为 light）。
- **性质**：完全确定性、离线、零依赖；代价是**零语义**——同义改写只要字面不重合就完全召回不到（见第三节 g15/g16）。

### 1.3 `bm25.py`：索引与打分

- 索引文本 = `tokenize(f"{doc.title}\n{doc.text}")`（标题进索引，权重靠重复出现体现）。
- IDF 用 BM25+ 恒正式：`math.log(1.0 + (self._n_docs - df + 0.5) / (df + 0.5))`，保证 IDF 恒为正，永不出负分。
- `controlled_terms()` 把 `domain.enums.Tone` 与 `ThemeTag` 的**全部枚举值**赋权 `CONTROLLED_TERM_BOOST = 2.0`；`_score_one` 里 `weights.get(term, 1.0)` 乘进去。这是"受控词表查询侧加权"：拆书产出的情绪/主题标签本身就出现在摘要索引文本里，查询侧给它们 2 倍权重。
- 排序完全确定：`ranked.sort(key=lambda item: (-item[0], item[1]))` —— 同分时按入索引次序，杜绝 dict 顺序扰动。
- `search(query, *, k=10, exclude_ids=None)` **零分文档不返回**（这是"平均召回 6.25 篇而非 10 篇"的原因）。
- 序列化只存语料与参数（version/k1/b/term_boost/doc_count/documents），统计量在 `load_json` 时重算，避免浮点漂移；版本不符抛 `ValueError`。

### 1.4 `indexer.py`：两道版权闸门

1. **类型级闸门**：`build_project_index()` 第一行
   `if not isinstance(project, CreationProject): raise TypeError("L1 索引只接受创作项目（CreationProject），参考书数据永不入索引")`
2. **字段级闸门**：`_plot_point_text(point)` 只读 `point.summary / characters / theme_tags / tone`，**刻意不读 `point.source.quote`** —— 原文永不入索引。

索引文档清单（`documents_from_creation`）：只收 `project.confirmed_parts` 为 True 的部件 ——

| doc_id | doc_type | 来源 |
|---|---|---|
| `positioning` | `positioning` | 已确认定位 |
| `volume` | `volume` | 已确认卷纲 |
| `outline-chapter-<order>` | `chapter_outline` | 已确认细纲章节 |
| `protagonist` | `protagonist` | 已确认主角 |
| `golden-finger` | `golden_finger` | 已确认金手指 |

`documents_from_summaries(summaries)` 追加 `summary-chapter-<order>`（`doc_type="chapter_summary"`），text = `title + gist + 各情节点文本 + characters`；meta 里带 chapter_id/order/characters/themes/tones/model_id。

### 1.5 `context.py`：上下文包（L1 的门面）

`build_context_pack(project, chapter_order, *, summaries=None, index=None, k=DEFAULT_TOP_K)`：

1. 非 `CreationProject` → `TypeError`；细纲里没有该章 → `ValueError(f"细纲中不存在第 {chapter_order} 章，无法组装上下文包")`。
2. **查询不是用户自然语言，而是由本章细纲反向构造**（这是本项目最反直觉、也最需要记住的一点）：
   ```python
   query_parts = [target.title, target.core_event, target.opening_hook, target.ending_hook]
   if project.draft.protagonist is not None:
       query_parts.append(project.draft.protagonist.name)
   query = "\n".join(part for part in query_parts if part and part.strip())
   ```
   即"用本章要写什么"去检索"本书之前写过什么"。
3. 排除本章自己的 `outline-chapter-N` 与 `summary-chapter-N`（直接字段里已有，不必回填）。
4. 命中转 `RetrievedDoc`，`snippet = doc.text[:120]`（`SNIPPET_LIMIT`）。
5. `_previous_summary()`：优先已写章节 gist（`source="written"`）→ 退化上一章 `core_event + ending_hook`（`"outline"`）→ 首章（`""`, `"none"`）。
6. 调 `extract_foreshadows` + `open_foreshadows_at` 得叙事债。

`ContextPack` 字段：`project_id / chapter_order / positioning / protagonist / golden_finger / chapter_outline / previous_summary / previous_summary_source / retrieved / open_foreshadows / index_doc_count`。**结构上不含参考书 ID、源路径与任何原文 quote** —— 这是"生成 prompt 不可能携带版权内容"的结构性保证。

### 1.6 `foreshadow.py`：叙事债

- 只认细纲里**显式写出**的 `ending_hook`，不猜（沿用 ADR-0002 证据纪律）。
- `_overlap_ratio()` = `|hook ∩ 候选| / |hook|`；`_candidate_text()` 取后续章 `opening_hook + core_event` 与摘要 gist/情节点，**不含该章自己的章尾钩**（防 A 章钩与 B 章钩字面相似导致连锁误判回收）。
- 按章号升序取最早达到 `RESOLVE_THRESHOLD = 0.6` 的章作为回收章。

### 1.7 实际调用链（grep 全仓核验）

```
                                    ┌─ panel/server.py:808  _generate_chapter
                                    │      pack = build_context_pack(project, target.order)
                                    │      → writer.write_chapter(pack)
build_context_pack() ── 只有 2 个 ──┤         (FakeChapterWriter / OpenAICompatChapterWriter)
                                    │         creation/chapters.py:35 context_brief(pack) 渲染进 prompt
                                    └─ agent/tools.py:228  SearchOwnBook.run
                                           hits = pack.retrieved 过滤后 [:BOOK_TOP_K]

ndecon reindex (cli.py:166) ──> build_project_index(project) ──> store.write_project_artifact("index.json")
                                                                    └── ⚠ 无任何读取方（死写）

evaluate_recall_at_k ──> 仅 tests/unit/test_retrieval_eval_gold.py 调用（无 CLI 入口、无 CI job）
```

**两个致命观察**（详见第六节缺陷清单）：

- `build_context_pack` 的 `summaries` 参数**全仓无任何调用点传入** → `documents_from_summaries()` 在生产中永不生效 → 索引里只有骨架部件，没有已写章节。
- `ndecon reindex` 落盘的 `projects/<id>/index.json` **没有任何代码读取**（grep 无 `read_project_artifact`，`BM25Index.load_json` 只被 `kb/corpus.py:161` 用来读 `kb/index.json`）→ 每次生成正文都现场重建索引。

---

## 2. `src/ndecon/kb/` —— L0.5 学习型知识库

### 2.1 `corpus.py`（199 行）

| 常量 | 值 | 含义 |
|---|---|---|
| `KB_DIRNAME` | `"kb"` | 工作区下的产物目录 |
| `INDEX_FILENAME` / `MANIFEST_FILENAME` | `index.json` / `manifest.json` | 索引与审计清单 |
| `KNOWLEDGE_PROMPT_VERSION` | `"kb-v1"` | 渲染版本标记 |
| `DEFAULT_KB_TOP_K` | `5` | 默认返回条数 |
| `KB_SNIPPET_CHARS` | `400` | **每条片段截断到 400 字** |

- `build_knowledge_index(roots)`：**复用 `retrieval.bm25.BM25Index`**（同一套分词与打分），逐文件走 `exclusion.decide()` → `docreader.read_document()` → `docreader.chunk_text()`；`doc_id = f"kb-{sha1(relative)[:12]}-{chunk.index:04d}"`，`doc_type="kb_chunk"`，`meta = {source, heading, chunk}`；解析失败/无文本/被排除都写进 manifest（可审计）。
- `write_knowledge_artifacts()`：`tmp + os.replace` 原子写两个 JSON。
- `search_knowledge(workspace, query, *, k=DEFAULT_KB_TOP_K)`：无索引返回 `[]`（静默降级，不报错）；否则 `index.search(query, k=k)` 后 `text = doc.text[:KB_SNIPPET_CHARS]`。
- `knowledge_brief(snippets: list[dict]) -> str`：渲染成
  ```
  【写作方法论参考】（来自作者本地知识库，借鉴手法与结构，严禁照抄具体作品情节）：
  1. 来源 <source> 《heading》
     <text>
  ```

### 2.2 `docreader.py`（129 行）：切块参数

| 常量 | 值 |
|---|---|
| `TARGET_CHUNK_CHARS` | 800 |
| `MAX_CHUNK_CHARS` | 1200 |
| `OVERLAP_CHARS` | 80 |

- 读取：md/txt 走 `utf-8-sig`，失败回退 `gbk`（中文 Windows 老资料）；docx 用 stdlib `zipfile` 读 `word/document.xml`，按 `</w:p>` 断行后剥标签 + `html.unescape`。**零第三方依赖**。
- `chunk_text()`：优先在 markdown 标题/空行处断块，累积到 800 字提前 flush，>1200 字硬切且保留 80 字重叠；**标题独占的段落不单独成块**（标题已作为 heading 参与打分，留空块会让它凭标题词挤掉真正有内容的邻近块 —— 源码注释记录实测 17.9% 的块只有标题）。

### 2.3 `exclusion.py`（153 行）：收录闸门（版权红线执行者）

判定完全基于**路径、大小、文本形态**，顺序固定、输出确定。硬排除规则：

| 规则 | 触发 | 实测命中数 |
|---|---|---|
| v1 不支持类型（非 .md/.txt/.docx） | 后缀不在 `SUPPORTED_SUFFIXES` | 338 |
| 全本命名 `书名 - 作者.txt` | `_BOOK_AUTHOR_RE` | 112 |
| 路径含"原文"目录 | 目录名匹配 | 5 |
| 文件名含"原文" | `_ORIGIN_TEXT_RE` | 5 |
| 采集缓存 `.progress.json`（含整章 HTML 全文） | 精确文件名 | 1 |
| "前 40 章" 命名 | `_FRONT40_RE` | 1 |
| txt > 1MB（`MAX_FILE_BYTES`） | 仅对 .txt 生效 | 1 |
| 章节体小说文本 | 章节行 ≥ `CHAPTER_LINE_THRESHOLD=8` 且评论术语 < `ANALYSIS_TERM_MIN=8` | 1 |
| 单章命名"第N章" | `_SINGLE_CHAPTER_RE` | 1 |
| 解析后无有效文本 | `chunk_text` 返回空 | 1 |

- 章节启发式 `_CHAPTER_LINE_RE` 支持 markdown 形态（`## 第1章`），是 2026-09 dogfood 发现版权全本以 md 形态归档后才补的（旧规则零漏过）。
- **两重豁免**防误伤自研分析：路径/文件名命中 `_ANALYSIS_MARKER_RE`（拆解|分析|报告|指南|教程|心得|笔记|研究|调研|扫榜|仿写|方法论|大纲|逐章|总结|决策|详案），或 `_ANALYSIS_TERMS_RE` 术语密度 ≥ 8（真实语料实测：小说正文 2 vs 自研分析 11，阈值取 8）。

### 2.4 真实知识库产物（本次只读读取 `.ndecon-workspace/kb/`）

| 项 | 实测值 |
|---|---|
| `index.json` 大小 | 21,164,551 字节 |
| manifest `built_at` | 2026-10-04T21:20:36 |
| 扫描根 | `<素材库根目录>` |
| 候选文件 / 收录 / 排除 | 772 / **306** / **466** |
| 总块数 | **29,277** |

> roadmap 快照写的"290 文件 / 34639 块 / 127 排除"是 2026-10-04 之前的旧数字，与当前产物不一致（后来补了 markdown 章节体规则，排除数从 127 涨到 466）。**规划文档与产物口径已漂移**，建议后续同步。

---

## 3. 片段如何进 prompt：三条互不相同的注入路径

### 3.1 路径 A —— 骨架生成（`creation/prompts.py`）

`full_draft_user_message(title, genre, premise, reference_stats, knowledge_snippets=None)`，段落顺序：

1. 标题 / 题材 / 一句话设定
2. `learning_brief(reference_stats)` —— **只出聚合数字**（每本参考书的 top5 主题 ×次数、爽点平均章距），不出任何原文
3. `knowledge_brief(knowledge_snippets)` —— 本文 2.1 的渲染
4. JSON spec（chapters 恰好 10 条）

`creation/prompts.py:86` 的 `knowledge_brief()` 与 `kb/corpus.py:190` 的 `knowledge_brief()` **是两份重复实现**，措辞差一个分号（`来自作者本地知识库；借鉴…` vs `来自作者本地知识库，借鉴…`）。

### 3.2 路径 B —— 设定 / 细纲（`creation/plans.py`，注入纪律最严）

`_kb_block(snippets, heading)`（`plans.py:74`）渲染「序号. 来源 <source> 《heading》\n   <text>」+ **固定尾句**：
> （以上只能借鉴做法与数值手感；禁止照抄其中的具体情节、设定名与成句。）

被两处使用：

- `setting_user_message()`（`plans.py:139`）→ heading = 【设定写法的方法论参考】，检索词 `SETTING_KB_QUERY = "设定 世界观 规则 限制 代价 势力 力量体系 金手指 的写法"`
- `outline_user_message()`（`plans.py:166`）→ heading = 【节奏与张力的方法论参考】，检索词 `OUTLINE_KB_QUERY = "细纲 节拍 冲突三角 章尾钩子 情绪曲线 爽点 章距 节奏 密度"`，并额外加「【本项目已确认设定（细纲不得越界引入新能力/势力/规则）】」硬约束块

注意这两条检索词是**手工写死的"怎么写"型查询**，不是用户自然语言 —— 这是本项目目前唯一的"查询优化"，且是人工常量，不是系统能力。

### 3.3 路径 C —— Planner Agent 工具（`agent/tools.py`）

`SearchMethodology`（`id="search_methodology"`）：

```python
snippets = search_knowledge(ctx.workspace, params.query, k=METHODOLOGY_TOP_K)   # METHODOLOGY_TOP_K = 3
if not snippets:
    return "知识库无命中（可能尚未建库，或没有相关资料）。"
return "\n".join(f"- 来源 {s.source} 《{s.heading}》：{_flatten_snippet(s.text, s.heading)}" for s in snippets)
```

- `params.query` 是**模型自己给的自然语言查询**（工具 schema 只要求 `query: str`），这是全项目唯一"模型驱动的检索查询"。
- `_flatten_snippet()`（`tools.py:109`）把多行片段折成单行并截 `_METHODOLOGY_SNIPPET_CHARS = 300` 字；正文为空返回「（该片段无正文）」，正文与标题同义返回「（该片段仅有章节标题，无正文）」—— 这是对"17.9% 的块只有标题"这一切块缺陷的**下游补救**，而不是修切块本身。
- 还有 `SearchOwnBook`（`tools.py:216`）：`build_context_pack(ctx.project, order)` 后取 `pack.retrieved` 前 `BOOK_TOP_K = 5` 条，给 Agent 检索本书已确认细纲/设定。

### 3.4 三条路径的横向对比（含不一致）

| | 路径 A 骨架 | 路径 B 设定/细纲 | 路径 C Agent |
|---|---|---|---|
| 检索函数 | `search_knowledge` | `search_knowledge` | `search_knowledge` |
| top_k | 5（`DEFAULT_KB_TOP_K`） | 5 | 3（`METHODOLOGY_TOP_K`） |
| 片段截断 | 400（来自 `search_knowledge`） | 400 | **300**（`_flatten_snippet`） |
| 查询来源 | 书名+题材+设定拼串 | **手工常量**（方法论聚焦） | **模型自由输入** |
| 渲染实现 | `creation/prompts.py:86` | `creation/plans.py:74` | `agent/tools.py:152` |
| 版权尾句 | 有（措辞 A） | 有（措辞 B，更强） | 无 |

**三份渲染实现、三种截断长度、两种查询策略** —— 这是"检索注入"这一环目前最大的工程债。

---

## 4. 问题①：金标 Macro Recall@10 = 0.875 在哪、怎么跑、覆盖率多少

### 4.1 在哪

| 角色 | 路径 |
|---|---|
| 评测实现 | `src/ndecon/retrieval/eval_gold.py`（`evaluate_recall_at_k` / `run_gold_eval`） |
| 语料 | `tests/fixtures/gold/corpus.json`（CC0-1.0，12 章） |
| 查询集 | `tests/fixtures/gold/queries.json`（CC0-1.0，16 条） |
| 冻结基线报告 | `tests/fixtures/gold/baseline_report.json`（5852 字节） |
| 测试 | `tests/unit/test_retrieval_eval_gold.py`（4 个用例） |

四个用例的断言：`test_gold_files_are_cc0`（license 必须为 CC0、语料 12 章）、`test_report_matches_committed_baseline`（**重算报告必须逐字段等于提交的基线**）、`test_frozen_macro_recall`（`macro_recall == 0.875`、`case_count == 16`）、`test_hard_paraphrase_cases_are_the_known_weakness`（未命中集合必须恰好是 `{g15-hard-responsibility, g16-hard-protect-supply}`）。

### 4.2 怎么跑

**没有 CLI 入口**（`cli.py` 的命令只有 version/doctor/split/analyze/panel/reindex/kb/lint/scaffold/deconstruct/plan），**也没有独立的 CI eval job**（`.github/workflows/ci.yml` 4 个 job：pytest×3 版本、ruff、wheel 完整性、repo hygiene）。金标靠 pytest 冻结。

```bash
# 方式一：跑测试（唯一进 CI 的方式）
python -m pytest tests/unit/test_retrieval_eval_gold.py -q

# 方式二：直接出报告（不进 CI，可换 k / 换语料做对照）
python -c "import json;from ndecon.retrieval.eval_gold import run_gold_eval;\
print(json.dumps(run_gold_eval('tests/fixtures/gold/corpus.json','tests/fixtures/gold/queries.json'),ensure_ascii=False,indent=2))"
```

报告字段：`version=1 / eval="recall_at_k" / k=10 / case_count=16 / macro_recall / params{tokenizer:"cjk-bigram-v1", k1:1.5, b:0.75, controlled_term_boost:2.0} / cases[]`（每条含 query、relevant_orders、retrieved_orders、hit_orders、recall）。

### 4.3 覆盖率（本次实测，非引用）

**语料覆盖率**：12 章（order 1–12，无一章缺失），每章恰好 3 个情节点。

**查询覆盖率**：16 条查询，相关章号并集 = `{1..12}` 全覆盖；14 条单章、2 条多章（`g4-fire-truth`[6,8]、`g9-beast-appearances`[3,5]）。但**章间分布严重不均**：

| 章 | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 | 11 | 12 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 被引用次数 | 1 | 1 | 2 | 1 | **4** | 1 | 1 | **3** | 1 | 1 | 1 | 1 |

第 5、8 章合计占 7/20 的相关判定权重 —— **金标集偏斜，0.875 这个数字对第 5/8 章的表现过度敏感**。

**成绩实测**：

| 指标 | 值 |
|---|---|
| Recall@1 | 0.7812 |
| Recall@3 | 0.875 |
| Recall@5 | 0.875 |
| **Recall@10（冻结值）** | **0.875** |
| MRR@10 | 0.8438 |
| 平均召回篇数 | 6.25（min 0，g16 完全零命中） |
| 同规模随机基线 | **0.5208** |

> **k≥3 就已饱和**：12 章语料里 k=10 几乎等于"全召回"，所以 0.875 这个冻结数字**对 k 不敏感、区分度低**。真正的信号是 Recall@1=0.7812 与 MRR@10=0.8438（排名质量），以及 g15/g16 两条零字面改写查询的 recall=0。
>
> 随机基线 0.5208 是按每条查询**实际召回篇数 m** 计算的随机期望（m/12）：L1 的 0.875 显著优于随机，但必须承认语料只有 12 章，随机基线天然高。

**两条难例**（也是 roadmap #13 的解冻依据）：

| case | query | 应召回 | 实测 |
|---|---|---|---|
| `g15-hard-responsibility` | 「那场悲剧究竟是谁的过错」 | 第 8 章 | recall=0.0（只召回 1 篇且不是第 8 章） |
| `g16-hard-protect-supply` | 「全城人晚上还能用上电，全靠他做了什么」 | 第 5 章 | recall=0.0（**零命中**） |

两条查询与原文**零字面重合**，是纯语义改写 —— 这正是 ADR-0004 已承认的 bigram 短板。

**待补的评测维度**（现在完全没有）：MRR / NDCG / HitRate@k、多档 k 的冻结、知识库侧（L0.5）**零金标**、上下文组装质量（注入后是否真被模型用上）**零评测**。

---

## 5. 问题②：从「用户查询」到「进 prompt 的片段」的完整数据流

本项目有两条互不相交的管道，且**两条都不是"用户自然语言查询 → 片段"的经典 RAG 形态**。

### 5.1 一致性检索（本书，L1）

```
[用户动作] 面板点「生成第 N 章正文」
      │
      ▼
panel/server.py:808  _generate_chapter(project_id, order, {provider})
      │
      ├─ 校验：仅 1-10 章 / 必须 CreationProject / 细纲里必须有该章
      ▼
build_context_pack(project, N)                       ← retrieval/context.py:101
      │
      ├─ summaries 参数：❌ 无调用点传入 → 恒为 []（已写章节永不入索引）
      ├─ index    参数：❌ 无调用点传入 → 现场 build_project_index()
      │                    └─ documents_from_creation(project)：只收 confirmed_parts=True 的部件
      │                       （positioning / volume / outline-chapter-N / protagonist / golden-finger）
      │                       ⚠ documents_from_summaries() 生产环境从不执行
      │
      ├─ 查询构造：query = 本章 title + core_event + opening_hook + ending_hook + 主角名（换行拼接）
      │            ⚠ 不是用户自然语言，是"本章细纲"
      ├─ BM25Index.search(query, k=10, exclude_ids={outline-chapter-N, summary-chapter-N})
      │            └─ tokenize() → bigram → IDF×TF 打分 → 零分丢弃 → (-score, 入索引序) 排序
      ├─ _snippet()：每条截前 120 字 → RetrievedDoc
      ├─ _previous_summary()：written → outline → none
      └─ extract_foreshadows() + open_foreshadows_at() → 叙事债
      ▼
ContextPack（结构上无参考书 ID / 源路径 / 原文 quote）
      │
      ▼
creation/chapters.py:35  context_brief(pack)  →  五个区块：
      【本书已确认设定】【本章细纲·第N章】【前一章摘要】
      【本书相关回顾】  ← 检索命中，每条 "- [doc_type] title：snippet(≤120字)"
      【尚未回收的伏笔】
      │
      ▼
chapter_user_message(pack) = context_brief + "请为第 N 章撰写正文草稿并输出 JSON：" + spec
      │
      ▼
OpenAICompatChapterWriter.write_chapter() → provider._chat_json([system, user])
```

**关键事实：这条链路上"用户查询"根本不存在** —— 查询是系统用本章细纲反向构造的。因此"查询优化"这一环目前由**人工写死的字段拼接顺序**承担，没有任何查询改写/扩展/去重逻辑。

### 5.2 学习型检索（知识库，L0.5）

```
[建库，离线一次]  ndecon kb index --root <dir>
      │  iter_candidate_files() → exclusion.decide() → docreader.read_document()
      │  → chunk_text(800/1200/80) → IndexedDocument(doc_type="kb_chunk")
      │  → BM25Index → 原子写 kb/index.json + kb/manifest.json
      ▼
[检索]  search_knowledge(workspace, query, k=5)     ← kb/corpus.py:164
      │  无索引 → 返回 []（静默降级）
      │  text = doc.text[:400]
      ▼
三条渲染路径（三份实现、三种截断）
      ├─ A 骨架：creation/prompts.py:86  knowledge_brief()  → full_draft_user_message()
      │     查询 = "\n".join([书名, 题材, 设定])          ← panel/server.py:386
      ├─ B 设定/细纲：creation/plans.py:74  _kb_block()   → setting/outline_user_message()
      │     查询 = SETTING_KB_QUERY / OUTLINE_KB_QUERY（手工常量）
      └─ C Agent：agent/tools.py:130  SearchMethodology
            查询 = 模型自由输入 → 折单行 + 截 300 字 → 作为 observation 回喂
```

---

## 6. 问题③：以 RAG 为核心，现在缺哪几环

按标准 RAG 六环逐环评估（✅ 有实装 / ⚠️ 有但残缺 / ❌ 零）：

### 6.1 召回（Retrieval）—— ⚠️ 有实装但回路断开

**现状**：单路 bigram BM25，纯 Python、零依赖、离线、确定性可重算。两条管道（本书 / 知识库）共用同一实现。

**缺口**：

1. **已写章节永不入索引**（最严重）。`build_context_pack` 的 `summaries` 全仓无调用点传入；创作链写正文只落 `manuscripts/chNNN.md` + `ManuscriptRecord`（`workspace/store.py:118-136`），**从不产出 `ChapterSummary`**（只有拆书管道 `pipeline/runner.py` 才产出）。结果：写完第 5 章再写第 6 章，检索不到第 5 章写了什么 —— **L1 最核心的"前章一致性"回路在生产上是断开的**，`_previous_summary` 永远走 `outline` 退化分支。
2. **单路召回，无融合位**。没有 RRF / 多路合并的接口，未来加向量层要现造。
3. **索引无复用**。`ndecon reindex` 写的 `index.json` 无人读取，每次生成正文现场重建（创作项目规模小，性能不成问题，但说明链路没闭环）。
4. **无语义召回**。bigram 对同义改写零容忍（g15/g16 实测 recall=0）。

### 6.2 重排（Rerank）—— ❌ 完全缺失

**现状**：`bm25.py` 里 `(-score, 入索引序)` 排完即结束，无任何二次排序。

**后果**：

- 排序质量只看 Recall@1=0.7812 / MRR@10=0.8438，无优化手段。
- 上下文包里的"本书相关回顾"是按 BM25 分硬排的前 10 条，**不区分文档类型**：一条 `positioning`（写的是题材卖点）和一条 `chapter_summary`（写的是上一章发生了什么）在列表里地位相同，但后者对写下一章显然更重要。
- 知识库侧同样：块之间无去重、无同源合并（同一文件的相邻块可能同时进 top5，实测有 80 字重叠）。

### 6.3 查询优化（Query Optimization）—— ❌ 完全缺失

**现状**：

- 一致性侧：查询 = 细纲四个字段 + 主角名的**固定拼接**，无改写、无扩展、无去噪、无字段加权。
- 知识库侧：只有两条**手工写死的常量查询**（`SETTING_KB_QUERY` / `OUTLINE_KB_QUERY`），骨架侧甚至用书名+题材+设定拼串（方法论聚焦最弱）。

**缺口**：同义词/别名扩写（如"影兽"与"影子怪物"）、实体归一、查询词权重、停用词过滤、`controlled_terms` 目前只覆盖 Tone/ThemeTag 两个枚举，未覆盖角色名/地名/金手指名等本书实体。

### 6.4 上下文组装（Context Assembly）—— ⚠️ 有骨架但无预算控制

**现状**：`context.py` + `chapters.py:35 context_brief()` 的组装是**确定性、结构清晰、版权干净**的，这是项目做得最好的一环。

**缺口**：

1. **无 token/字符预算**。检索条数 `k=10` 与截断 `120` 字都是硬编码常量，拼完多长全看命中数；roadmap"最大盲区"里点名的「知识库片段 + ContextPack 拼进 prompt 后的实际长度」**至今没有测过**。
2. **无去重/压缩**。命中条目不去重、不按类型配额（比如"最多 3 条 chapter_summary + 1 条 positioning"）。
3. **三份渲染实现不一致**（见 3.4 表）：截断 400/400/300、版权尾句两种措辞、其中一个路径无尾句。
4. **知识库片段与本书上下文从不同时出现**（骨架/细纲路径走 KB，正文路径走 ContextPack），没有统一的组装器。

### 6.5 评估（Evaluation）—— ⚠️ 一条金标基线，维度单一

**现状**：`eval_gold.py` + 16 条 CC0 金标 + 0.875 冻结入 CI —— **这是全项目 RAG 最扎实的资产**，ADR-0004 引入门①（先有金标与基线才允许引入新检索层）已经满足。

**缺口**：

1. 只有 Recall@k，**无 MRR / NDCG / HitRate**（本次实测 MRR@10=0.8438 是临时脚本算的，代码里没有）。
2. **k≥3 饱和**，冻结值区分度低；只有 k=10 一个冻结点。
3. **金标集偏斜**：第 5 章占 4/20、第 8 章占 3/20 权重（见 4.3）。
4. **知识库侧零金标**：L0.5 检索了 29,277 个块，没有一条"这条查询应该命中这个块"的判定，无法度量方法论检索质量。
5. **无端到端质量评估**：检索到的片段是否真被模型用上、是否改善了正文 —— 完全没有（属 roadmap 盲区 A）。
6. **只有 pytest 入口**，无 CLI、无独立 CI job、无报告归档流程（基线靠手改 JSON）。

### 6.6 可观测（Observability）—— ❌ 几乎为零

**现状**：检索链路**没有任何日志/指标埋点**。全仓 grep 无 `logging` / `logger` / `metrics`（只有 `deconstruct` 侧的 `ChapterMetrics` 与 Agent 侧的 `trace`，均与检索无关）。

**缺口**：

- 不知道每次生成用了哪条 query、命中几条、top1 分数多少、零命中发生了多少次。
- 不知道注入 prompt 的总字符数/估算 token 数。
- 知识库侧只有建库时的 `manifest.json`（收录/排除可审计，这一块做得好），但**检索侧零记录**：哪条查询查了什么、命中哪些块、是否被截断，全无。
- token 成本追踪是 roadmap B，前置依赖 A（真实 LLM dogfood）。

**唯一的可观测亮点**：`kb/manifest.json` 逐文件记录收录/排除原因（实测 466 条排除全部带原因），这是版权合规审计的硬凭证。

---

## 7. 问题④：哪些是冻结项（不能擅动），哪些现在就能补

### 7.1 判定依据

ADR-0004 的**通用引入门**四条：① 先有金标集与 Recall@10 基线才允许引入新检索层；② Fake-first 离线回退；③ 证据纪律（只出聚合数字/方法论，不出原文 quote）；④ 新层以 extras 引入，默认零重依赖。

- 门槛①**已满足**（16 条 CC0 金标 + 0.875 冻结基线在位）。
- 门槛②**已满足**（Fake 双轨齐全）。
- 门槛③④是约束，不是门槛。

所以 **#13（L2 向量混合检索）现在仍未解冻，原因不是"没有金标"，而是 roadmap 写的触发条件「L1 金标 Recall@10=0.875 被证明不足（真实 dogfood 出现成规模零字面改写查询落空）」的第二半句没满足**：目前只有 fixture 里 2 条**人造**难例，**没有任何真实 dogfood 证据**。在真实 LLM dogfood（roadmap A）跑出成规模落空前，#13 不能动。

### 7.2 冻结项清单 —— 不能擅动

| 项 | 内容 | 解冻条件（原文） | 当前状态 |
|---|---|---|---|
| **#13** | L2 向量混合检索（限 sqlite-vec/numpy，禁常驻服务） | 「L1 金标 Recall@10=0.875 被证明不足（真实 dogfood 出现成规模零字面改写查询落空）」 | 金标门槛已过；**dogfood 门槛未过**（无人造之外的证据）→ **冻结** |
| **#4** | Stage 4 拆书侧设定/角色档案 | 「继续大规模拆书且 Planner Agent 的创作侧设定能力不够用时」 | 与 L3 实体图谱强绑定（L3 排 #4 之后）→ **冻结** |
| **#6** | Stage 5 汇总报告 / Stage 6 文风分析 | 「#3/#4 有真实产物验证后」 | → **冻结** |
| **#10** | 短篇拆书管道 | 「长篇流程连续自用 4 周后」 | → **冻结** |
| **#11** | 持续学习深化（跨书卡片库/差距对照） | 「L0.5 知识库被证明不足」 | 无证据 → **冻结** |
| **#12** | GUI/TUI 升级 | 「面板被证明不足（当前 #15 面板够用）」 | → **冻结** |
| — | 检索参考书原文 / quote 辅助写作 | 版权红线，**永不解冻**（ADR-0004 放弃方案） | 硬红线 |
| — | 引入常驻向量服务 / 重依赖 | 违反离线红线与 pyproject 三依赖约束 | 硬红线 |
| — | 正文生成接自主 agent 循环 | ADR-0006「ContextPack 的确定性与版权边界不可破坏」 | 硬红线 |
| — | token 成本追踪（roadmap B） | 「前置依赖 A：没有真实调用数据前做是凭空设计」 | 待 A |

### 7.3 现在就能补的（不触碰任何冻结项）

判据：**不动检索层分层（不加向量/图）、不动任何会改变金标成绩的打分逻辑、不改版权边界、不加运行时依赖**。

| 优先级 | 补什么 | 落在哪一环 | 为什么现在能做 | 风险/前置 |
|---|---|---|---|---|
| **P0** | **接通"已写章节 → 摘要 → 索引"回路**：让章节写完产出 `ChapterSummary`（gist + 情节点），并让 `build_context_pack` 拿到它 | 召回 | 纯补断链，不改打分逻辑；`indexer.documents_from_summaries` 与 `context._previous_summary("written")` 分支**已实现且已测试**，只差生产侧接线 | 会改变 L1 索引内容，但**不动 bm25/tokenize**，金标（只跑 summaries 语料）不受影响 |
| **P0** | **修 `agent/tools.py:229` 的 doc_type 过滤**（`"summary_chapter"` → 实际值 `"chapter_summary"`） | 召回 | 一行修正，过滤条件当前恒为真、等于不存在 | 需补一个断言用例 |
| **P0** | **统一三份 KB 渲染实现**（`kb/corpus.py:190` / `creation/prompts.py:86` / `creation/plans.py:74`）为一个函数 + 统一截断与版权尾句 | 上下文组装 | 纯重构，不改检索语义；顺带统一 Agent 侧 300 字与其余 400 字的不一致 | 改渲染文本会动 prompt 字符串，需同步 prompt_version |
| **P1** | **检索埋点**：每次检索记录 query / 命中数 / top1 与 topk 分数 / 零命中次数 / 注入字符数，落到项目目录 JSONL | 可观测 | 零依赖、纯新增；**不依赖 roadmap A**（这是"检索侧可观测"，与"token 成本追踪"是两件事） | 需定产物文件名与保留策略 |
| **P1** | **评测维度扩展**：给 `eval_gold.py` 加 MRR / NDCG / HitRate 与多档 k（@1/@3/@5/@10） | 评估 | 纯新增字段；**保留 Recall@10=0.875 原样冻结**，新增指标另存，不动 `baseline_report.json` | 建议新增 `baseline_report_extended.json`，避免 `test_report_matches_committed_baseline` 变红 |
| **P1** | **知识库侧金标集**（L0.5）：自建 15–20 条"方法论查询 → 应命中资料块"判定（沿用 CC0/自造 fixture 纪律） | 评估 | 门槛①要求"先有金标才允许引入新检索层"，目前**只有一致性侧有金标，学习侧裸奔** | 需自造 CC0 方法论语料 |
| **P2** | **轻量重排（规则式）**：按文档类型权重（chapter_summary > outline > positioning）、章节邻近度、命中密度做二次排序 | 重排 | `open-source-landscape.md` §5.5 已给出零依赖配方；纯 Python 规则，非"新检索层" | ⚠ **会改变金标成绩**，必须与维护者确认是否重冻基线 |
| **P2** | **查询改写规则（非 LLM）**：同义词/别名扩写、实体归一、`controlled_terms` 扩展到本书角色/地名/金手指名 | 查询优化 | `open-source-landscape.md` §5.2 零依赖配方；不改检索层 | ⚠ 同样会改变金标成绩 |
| **P2** | **上下文预算控制**：给 `ContextPack` 加字符预算与类型配额（如最多 3 条 summary + 1 条 positioning），超出按分数截断 | 上下文组装 | roadmap 点名的盲区项，零依赖可实现 | 需先有 P1 埋点才知道真实分布 |
| **P3** | **RRF 融合接口**（`k=60`，单路时退化为恒等） | 召回 | §5.3 配方；**先把接口位留出来**，等 L2 解冻才真正启用 | 单路无收益，纯前瞻 |
| **P0** | **区分"骨架部件未确认 → 不进索引"的体感问题**：确认率极低时 L1 召回量趋近于 0（见 7.4 第 3 点） | 召回 | 属接线/策略问题，不动打分逻辑 | 需维护者决定：未确认部件是否可入索引（有版权与"候选不代笔"纪律的取舍） |
| — | **roadmap A：真实 LLM dogfood（带检索埋点地跑）** | — | **最高杠杆**：它是 #13 解冻、B 立项、本文所有"未验证"项的共同前置。注意工作区证据显示 AI 路径已跑通过（7.4），但**跑过不等于度量过** —— 缺的是埋点而非勇气 | 需供应商 key |

### 7.4 真实工作区证据（只读核验，`.ndecon-workspace/projects/`）

工作区里躺着 3 个真实创作项目，可作为"链路到底跑没跑通"的一手证据：

| 项目 | 创建时间 | `confirmed_parts` | 已写正文 | 骨架 provenance |
|---|---|---|---|---|
| `creation-守灯人实测书-222125` | 2026-09-25T22:21:25 | `{}`（全未确认） | 无 | **全部 `openai-compat:deepseek-chat`** |
| `creation-守灯人实测书-222213` | 2026-09-25T22:22:13 | `{golden_finger: True}` | ch1（606 B，`rule:fake-chapter`） | `rule:fake-creator` ×4 + 金手指 `user-confirmed` |
| `creation-随便-231725` | 2026-09-25T23:17:25 | `{}` | ch1（8877 B，**`openai-compat:deepseek-chat`**，2867 字）、ch2/ch9（`rule:fake-chapter`） | 全部 `rule:fake-creator` |

三点结论：

1. **真实 LLM 正文生成确实跑通过一次**：`随便-231725/manuscripts/ch001.md` 是 deepseek-chat 产出的 2867 字真实正文，自评 `hook_strength=4 / info_density=4 / outline_followed=true`，`model_id="openai-compat:deepseek-chat"`、`prompt_version="cw-v0"`。**roadmap 第二节「AI 路径从未用真实模型端到端跑过」这句话已经过时**（至少骨架 AI 路径与正文 AI 路径各跑通过一次，时间 2026-09-25），应同步修订。
2. **但没有任何检索落空的证据**：工作区不记录检索 query、命中数、零命中次数（见 6.6 零埋点）。所以即便跑过真实模型，#13 的解冻条件「真实 dogfood 出现**成规模零字面改写查询落空**」**仍然没有证据支撑** —— 跑通过 ≠ 度量过。
3. **`confirmed_parts` 大面积为空**：三个项目里只有 1 个确认了金手指。而 `documents_from_creation()` 只收 `confirmed_parts=True` 的部件 —— **未确认的骨架部件根本不进索引**。这意味着真实使用中 L1 索引常常只有 1–2 个文档，`build_context_pack` 的"本书相关回顾"实际可能是空的。这个"确认率决定召回量"的耦合，是 P0 接线之外最影响体感的一点。

### 7.5 一句话给 captain

**先补 P0 的三条接线/修正（断链 + 过滤 bug + 渲染统一），再补 P1 的可观测与评测维度（都不依赖真实 LLM），把 #13 严格留给真实 dogfood 之后。** 检索层的"加向量"现在解冻是违规的——金标门槛过了，但 dogfood 门槛没过，而 0.875 这个数字本身还带着"12 章语料 k=10 饱和、金标集偏向第 5/8 章"两个失真，用它证明"不足"证据不够硬。

---

## 8. 审计中发现的代码缺陷（只读记录，未修改）

| # | 位置 | 问题 | 影响 |
|---|---|---|---|
| 1 | `src/ndecon/agent/tools.py:229` | `hits = [d for d in pack.retrieved if d.doc_type != "summary_chapter"]` —— 实际 doc_type 是 `chapter_summary`（`indexer.py:141`），条件恒为真 | 过滤形同虚设；`SearchOwnBook` 会把已写章节摘要也返回给 Agent（与注释意图相反） |
| 2 | `src/ndecon/cli.py:166` | `ndecon reindex` 写的 `projects/<id>/index.json` 无任何读取方 | 死写；每次 `build_context_pack` 现场重建索引 |
| 3 | `src/ndecon/retrieval/context.py:105` + `panel/server.py:808` + `agent/tools.py:228` | `summaries` 参数全仓无调用点传入；创作链从不产出 `ChapterSummary` | **已写章节永不入索引**，L1 前章一致性回路断开 |
| 4 | `kb/corpus.py:190` / `creation/prompts.py:86` / `creation/plans.py:74` | 三份 KB 片段渲染实现重复且措辞/截断不一致 | 行为漂移风险；改一处漏两处 |
| 5 | `agent/tools.py:105` vs `kb/corpus.py:31` | 片段截断 300 vs 400 字不一致 | 同一批资料在不同路径喂给模型的量不同 |
| 6 | `docs/roadmap.md:16` | 知识库数字"290 文件 / 34639 块 / 127 排除"与真实产物（306 / 29277 / 466）不一致 | 规划文档口径漂移 |
| 7 | `docs/roadmap.md:18` | "159 测试全绿"过时（本次全量实测 **219 passed in 20.22s**） | 同上 |
| 8 | 全仓 | 检索链路零日志/零指标埋点 | 无任何运行时可观测性（见 6.6） |

---

## 9. 附：本次审计执行过的命令（可复现）

```bash
cd novel-deconstruct
python -m pytest                                            # 219 passed in 20.22s
python -m pytest tests/unit/test_retrieval_eval_gold.py -q   # 4 passed
# 金标重跑 + 多档 k + MRR + 随机基线（临时脚本，未落盘，未改仓库）
python -c "... evaluate_recall_at_k / build_index / documents_from_summaries ..."
# 真实 KB 产物统计（只读读 .ndecon-workspace/kb/manifest.json）
```

**本文未修改任何源码、未新增任何文件到 `src/` 或 `tests/`。**

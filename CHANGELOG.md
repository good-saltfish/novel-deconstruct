# Changelog

本项目遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/) 规范，
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [Unreleased]

### Added

- **对白终于有基线了（此前只量节奏，台词完全没被评价过）**：`quality/style.py` 量的是叙述怎么排（分段、断句、破折号、身体外化），**台词怎么说一句都没管**。而「AI 味」在台词上完全是另一套症状：条条完整、句句在解释、满嘴「你……」冲着读者说、爱用破折号把解释焊在台词后面。新增 `measure_dialogue(text)`，按引号切分（`「」`/`“”`）后产 19 项台词指标（条数每千字、句均字、完整句占比、未收尾占比、问号/省略号/破折号占比、语气词/解释腔/客气语/称呼/第二人称密度、及「引号句未带说语占比」等）。
  基线来自 **81 本 31,889 章**（去重后按 `sha1(book_key)[:8] % 5` 划**19 本 4,654 章留出**做误杀验收）。三条定稿判据（训练侧分位定线、留出侧验收）：`对白破折号占比` > p95（人类章里 **75% 的台词一句破折号都没有**，p75=0，所以 prompt 档退到 p90 而不是照抄 p75——否则等于禁止用破折号）；`对白条数每千字` > p95（AI 台词太密）；`对白第二人称密度` > p95（AI 爱说「你……」）。留出集单条误杀 1.5%–2.0%，AI 命中 18%–36%。
  三条纪律写进了代码：①**没写对白的章不参与对白比对，但也不算「对白判据全过」**——`StyleReport.dialogue_checked` 显式区分「比过且过了」与「压根没比」，否则「一句对话都不写」会变成满分策略；②**对白基线单独报家门**（`baseline_meta()["dialogue"]`，语料口径不同：81 本 31,889 章 vs 节奏的 67 本 5,829 章），混进主表会让「本基线来自 67 本 5829 章」变成假话；③**AI 侧样本只有 11 章**，命中率只作方向参考，`note` 里已写明，不足以当判别器验收。
  组合门槛随之重标：判据从 5 条变 8 条后，`STYLE_ADVISORY_LIMIT` 由 1 提到 **2**（合计越界 >2 才重写）。人类 5270 章留出集实测：`>1` 误杀 14.9%、`>2` 误杀 **1.0%** 而 AI 命中仍 67%——文风重写真花一轮生成预算，误杀一次就是白烧钱，宁放过几条。
  同时把对白约束送进生成侧：`chapters.py:style_card` 新增 4 条台词约束（台词语数按 `target_chars` 折成整章预算、台词里几乎不用破折号、别让角色对读者说「你……」、**允许台词有废话和误解**）与两组台词「差/好」反例；面板 `/style` 响应新增 `dialogue_checked` / `dialogue_metrics` / `rule_count`；`ndecon style` 输出会说明「本章无对白，台词判据未参与比对」，不再把 5 条当成全部。

- **能导出了：EPUB / DOCX（此前写出来的书只能看 `manuscripts/chNNN.md`）**：全仓 `epub` 零命中，`docx` 只出现在 `kb/docreader.py`（读取资料用），一本书写出来就停在几个 markdown 文件里，发不出去。新增 `ndecon/render/export.py`（**零第三方依赖**：stdlib `zipfile` + 手写 OOXML/XHTML，为一个「另存为」引 python-docx + ebooklib 两个包不值，且每个字节都可测）、`ndecon export <project_id> --format epub|docx [--out] [--author]` 命令，以及面板 `GET /api/projects/<id>/export?format=epub|docx`（用 GET 是因为它不改变状态、浏览器点了就下载；POST 得让前端把响应体转 Blob 再触发下载）。
  三条实现决定：①**只导出盘上真有正文的章**——以 `store.read_manuscript()` 非空为准，不看 `project.manuscripts` 的键（历史工作区里「键没了文件还在」和「键在文件被回滚删掉」都出现过）；②**占位正文照常导出但点名**——`ExportResult.placeholder_orders` 会列出 Fake 占位章（静默丢掉会让「为什么少了三章」变成谜，静默收下会让占位文本被当真正文发出去）；③**EPUB 书号是确定性的**（`uuid5(NAMESPACE_URL, "ndecon:<书名>")`），随机 uuid 会让同一份稿子导出两次变成两本不同的书。
  两处容易踩空的格式细节：EPUB 的 `mimetype` 必须是 zip 里**第一个**条目且 `ZIP_STORED`（压缩了严格阅读器直接判非法）；中文书名的下载响应必须走 RFC 5987 的 `filename*=UTF-8''`，只给 `filename=` 浏览器会按 latin-1 解码成乱码。DOCX 侧用 1.5 倍行距、每章另起一页、A4 页面（`11906×16838` twips），XML 1.0 非法控制字符先剔掉（否则 Word 报「文件已损坏」）。
  顺带给 `workspace/atomic.py` 加了 `atomic_write_bytes`：Windows 上目标文件被阅读器/索引器打开时普通 `write_bytes` 直接抛 PermissionError，且不能留半截文件。
  实测真实项目（`creation-随便-231725`，盘上 ch1/ch2/ch9）：docx 6458 字节、epub 8760 字节、各 3 章、`placeholder_orders == [2, 9]`、耗时 12–16ms，全部 XML `ET.fromstring` 通过。

- **拆书产物真正接进创作（原来只喂了两行数字）**：`creation/` 对 `deconstruct/` 的依赖此前只有 `plans.py` 借了一个 `MAX_QUOTE_CHARS` 常量，拆出来的世界观/金手指/人物/情节点/细纲在创作侧**零消费**——真实项目 `reference_ids` 全为空，等于拆书拆完就丢。三层接上：**L1 规则层直连**：导入参考书时顺手跑一次 `build_rule_deconstruction`（纯 regex、毫秒级、零 token，失败不阻断导入），产物存进 `ReferenceProject.deconstruction`；`_reference_stats` 把它压成一份**只有数字**的 `structure`（句子多长、短句占比、每千字冲突点、钩子吊几章），经 `creation.prompts.learning_brief` 进骨架 prompt。**L2 语义层进设定/细纲**：`setting_user_message` / `outline_user_message` 新增 `reference_stats`，`_plan_settings` / `_plan_outline` 把同一份数字传给 planner，模型据此对标节奏与钩子节拍。**L3 章尾钩回收统计**：拆书侧新增 `build_hook_ledger`——对每章 `end_hook` 用 bigram 重合率（阈值 0.6，与 `retrieval.foreshadow` 同口径）扫后续章找回收章，产出钩子台账（埋钩章/回收章/间隔）与 `hook_recovery_median` / `hook_recovery_longest` / `hook_open_count`；判回收时**刻意不看该章自己的钩子**，否则钩子会自我回收。
- **版权闸门第二道：结构化产物也只出数字**：`_reference_stats` 只挑 `structure` 的 8 个数值键，`chapters` / `panorama` / `hook_text` 等转述文本结构性不可达；`test_reference_block_never_carries_narrative_fields` 用三个带情节的字段做反向断言。KB 版 markdown 新增「钩子的回收节奏」段，只列章号与间隔，不列钩子写了什么。
- **句子层面能回答「靠长句压气还是靠短句抽打」**：`ChapterMetrics` 新增 `median_sentence_chars` / `min_sentence_chars` / `max_sentence_chars` / `short_sentence_ratio`（≤12 字）/ `long_sentence_ratio`（>45 字）与 `closing_hook_signal`（章尾 200 字内的悬念信号，与开篇窗口对称）。平均句长会把"全短句"和"长短交替"算成同一本书，中位数与两端占比才能分开。全书口径按**句数计权**（`_weighted_median` / `_weighted_ratio`）而不是各章取平均——否则一个 200 字的过场章和 4000 字的主线章等权，算出来的中位数不是这本书的中位数。实测三本真实长篇（《九鼎记》74 章 /《从水猴子开始成神》77 章 /《夜无疆》1269 章）：句长中位 26–33 字、短句占比 13–23%、章尾钩 9–120 章，规则层耗时 190ms–4.9s。

### Fixed

- **笔法卡不再把「长段删光」教成标准答案（`cw-v1` 上线后最严重的退化）**：分段那条要求原文是「至少 {single:.0%} 的段落只有一句话」，而 `single` 取的是人类**中位数**（0.9059）。中位数不是下限，它是分布的正中间——一句话一段的人类作者里，另外一半的段长还在中位数以上。模型于是找到了最省事的解：把所有多句段删光。实测同一本书 15 章：`cw-v0`（无卡）12 章段长 90 分位平均 **94.8 字**、多句长段占 26%，`cw-v1`（有卡）7 章塌到 **35.3 字**、多句长段 1.7%；其中 `qiniu/conatus-free` 同一个模型跨两个版本，`cw-v0` 段p90=83／最长段 131 字，`cw-v1` 段p90=29／最长段 39 字，51 字以上的段**一个都没有**。而闸门全程零告警，因为 `单句段占比` 只有 lo 向判据（越低越差），「全是一句一段」永远不触发。
  四处收口：①分段那条重写成区间 + 明写下限（「平均每三四段就要有一段把 2–4 句连起来写，六十字到一百二十字」），并直接点破退化解「把所有段落都压成一句话，是这道要求最省事也最糟的解法」；②删掉第 5 条括注「单段平均句数别超过 {para:.1f} 句」——那取的是人类 p75（1.2131），等于再压一道上限；③台词那条补长度下限，只压条数会让模型用「嗯。」「走吧。」凑够数目（实测第 13 章台词句均 6.25 字、第 3 章 5.96 字，人类 p1 是 6.5）；④基线新增两条 lo 向判据：`段p90字` < p10（35 字，留出 3695 章误杀 0.54%、AI 25 章命中 28%）、`对白句均字` < p1（6.5263 字，留出 3304 章误杀 0.33%、AI 命中 17.6%）。判据从 5 条变 6 条、有对白时 8 条变 10 条。
  顺带发现并修掉一个**基线自身的错**：`percentiles["段p90字"]` 那块写的是 p50=25，而同一张表里 `段均字` 的 p50 是 27.5——段长的 90 分位不可能小于段均值，说明这是旧口径残留。用引擎自己的 `measure()` 在 62 本 28889 章上重算，真实值是 p10=35 / p50=48 / p90=74，旧表低了整整一倍，拿它算分位会给出「人类中位 1.0 分位」这种自相矛盾的文案。
  这次改判据同时戳穿了测试里的一个假前提：`_human_chapter()` / `_humanish()` 两个夹具原本是**全篇一行一句**，段p90 只有 19–25 字，正是本次认定的退化解形态，却被当成「人类腔范本」用来断言「文风越界清零」。已改成真实的分布形态（约 12.5% 的段落承载 2–4 句），并在函数 docstring 里写明为什么不能全篇一句一段。

- **模型自评终于参与判断（原来只写进记录给人看）**：`ChapterSelfReview` 从 `#16` 起就每章都生成——`hook_strength` / `info_density`（1-5）、`outline_followed`、`deviations`——但全仓只有面板负责显示，**没有任何逻辑分支读它**。于是「模型自己说这章没按细纲写」这件事，对引擎完全不存在。现在接进 `txn.run_chapter` 的重试循环：`outline_followed is False` 时带上 `deviations` 里的具体偏差点重写一次（上限 1 次，与文风重写同量级），重写完仍报偏离就照常入库并把「自评：模型判断本章偏离细纲 —— …」写进 `advisories`。
  为什么**只有** `outline_followed` 能触发、1-5 分不能：那两个分是模型给自己打的主观分，没有任何人类标定分布做对照。真实工作区实测 4 条自评——真模型那一章恒 4/4、`FakeChapterWriter` 恒 3/3，一个从不变化的数字不构成判据；给它定「≤2 就重写」的阈值，等于把刚拆掉的假指标换个位置重建。`outline_followed` 不同：它是对一件**人能拿细纲一眼复核**的事的自认，且必须附 `deviations` 说明偏在哪。分数仍然落盘（新增 `ChapterRun.review_hook_strength` / `review_info_density` / `review_outline_followed` / `review_deviations` 四个字段），等真实模型批量产出的分布出来再定阈值——那时候阈值来自数据，不是拍脑袋。
  顺带修掉两处相关的判断错误：①**「过硬门但更差」的版本会被收下**——原实现只在「最后一版没过硬门」时才回退 `accepted`，末轮重写让文风越界变多也照样入库，明明手里有一份更好的；现在最终选择统一走 `_is_better`（先比文风实测越界条数，再比自评有没有报偏离，实测值压在模型自述前面）。②**`ManuscriptRecord.review` 记的是最后一版**而不是收下的那一版，重写回退时会分叉，等于给作者一份指错方向的评语。

- **报告里章号列表去重并截断**：真实语料重复章号很常见（实测《夜无疆》1269 个章节只有 850 个不同章号，切分器告警但保留），`quiet_chapters` / `hook_openings` / `closing_hook_openings` 会把同一章号连写两遍。改用 `dict.fromkeys` 保序去重；渲染侧 `_render_orders` 只报「共 N 章（前 12 章：…）」，不再把 438 个章号排成一行撑爆知识库页面。

- **连写断点续跑（原来只能从头重来）**：`RunLog` 早就有 `committed_orders()` / `unsettled_orders()` / `reclaim_stale()`，但 `src/` 里零调用——`jobs.py:_run` 的 `for order in range(job.first, job.last + 1)` 从不看 run 记录，中途崩了再点连写就从第 1 章整章重写。三处收口：①`_run` 循环前置 `RunLog(...).reclaim_stale()` + `committed_orders()`，已 committed 的章直接跳过（`BatchJob.skipped` 单独计数，`written` 只算本轮真写的，界面上「跳过 3 章、写了 2 章」不会再显示成「写了 5 章」）；②被杀留下的 `in_progress` 死记录先结算成 failed 再重跑那一章——否则它永远既不重跑也不完成，台账一直显示「这一章在跑」；③同一个 `run_id` 再发一次也不会重写已提交的正文，用户手改过的稿子不会被覆盖。
- **陈旧游标导致「接着写」变成「从头重写」**：老项目 `project.json` 里 `cursor` 停在 0，而 `manuscripts/` 下已经躺着 ch001-ch009；连写默认区间是 `cursor + 1` 起（前端也算这一份），于是用户点「接着往下写」实际从第 1 章开始，九章被整章覆盖。游标陈旧的三个来源都不写游标——逐章 `POST /chapters/<n>/generate`、版本还原 `restore_version`、早期没有 `cursor` 字段的文件——所以修在**读**这一处：`WorkspaceStore.get()` 调 `_heal_cursor()`，把游标补到 `manuscripts/chNNN.md` 的最大章号并落盘。写入走 `update_creation`（锁内重读再改）而不是 `save_creation`，避免重犯 B-2 的「过期快照冲掉用户编辑」。顺带修好 `summarize.missing_summary_orders`——它同样按 `cursor` 扫盘补摘要，游标陈旧会漏章。
- **指纹报告从"人类章节恒不通过"改为可按比例判读（fp-v2）**：旧版八条规则用 `all()` 合取，实测 1500 章人类真实正文只有 **0.1%** 能通过——元凶是 `scene_rotation` 把「场景标记 ≥5」当下限（人类中位 0，单条误杀 99.9%），`period_per_paragraph` 的限值 2 又恰好卡在人类中位数上。三处口径修正：①段落切分改用 `style.paragraphs_of`，与阈值表同一坐标系（旧 `split_paragraphs` 按空行切块，把整章当一段，实测差 17 倍）；②`scene_rotation` 降为 info 只展示不判罚；③`passed` 改为「warn 规则越界 ≤1 条」——单条越界只是风格，累计才是信号。阈值按本机语料重标定（dev 20 本 899 章定线 / hold 19 本 854 章回放）：`not_x_but_y` 2→3、`em_dash` 1→5、`period_per_paragraph` 改超长段占比 ≤15%、`simile_density` 去双重合取后取「整章 ≤10 且密集段占比 ≤5%」。人类章节 `passed=False` 从 99.9% 降到 dev 8.0% / hold 4.7%，与 `style.py` 五条联合误杀率 4.3% 同量级。`AI_FINGERPRINT_VERSION` 升 `fp-v2`（同名不同义），`drift.py` 的参考线同步换单位（`em_dash` 9.0、`period_per_paragraph` 0.143、`simile_density` 12.0），否则该规则永远不会触发。
- **删掉 `gates.py` 的 `fingerprint_passed` 软分键**：它是八条规则的再压缩，而每条规则的实测值已逐个记进 `soft_scores` 的 `fp_<rule>`，全仓（含面板与 IDE 插件）无任何消费者。合取语义一改，留一个同名不同义的列只会误导。

### Removed

- **旧拆书管道退役（`split` / `analyze` 两个命令、跨章聚合 `aggregation`、黄金三章报告）**：全仓曾有**两条拆书管道并存**——旧的 `pipeline/`（`run_split` + `build_entries` + `aggregate` + LLM 逐章摘要 + 黄金三章报告 + `render/markdown.py` 三目录落盘）与新的 `deconstruct/`（纯规则层 `build_rule_deconstruction` + 可选语义层）。`panel/server.py` 导入参考书时**两条都跑**，产物分别存进 `ReferenceProject.aggregation` 与 `.deconstruction`；`ReferenceProject` 的注释自称「与 aggregation 并存不是冗余，创作侧要的正是后者」——**实测是反的**：创作侧 `_reference_stats()` 只从 `deconstruction.structure` 取 8 个数字键，`aggregation` 一个字段都没被读过；插件侧 `dsh-novel-studio` 对 `aggregation` / `themes` / `pacing` / `total_plot_points` 的引用也都是 0 次。
  删掉的东西：`src/ndecon/pipeline/`（runner / stage0 / stage3）、`src/ndecon/providers/base.py`（`DeconstructProvider` 协议，只服务旧管道）、`src/ndecon/render/markdown.py`、`ReferenceProject.aggregation`、`cli.py` 的 `split` / `analyze` 两个命令、`FakeProvider.thin_summary` / `golden_report`、`OpenAICompatProvider.golden_report`、`prompts.golden_user_message`，以及 `domain/models.py` 里随之成为孤儿的 `ReportBundle` / `ChapterEntry` / `ChapterRhythm` / `CharacterAppearance` / `PacingStats` / `Aggregation` / `GoldenChapterReport` / `StructureBeat`（158 行降到 111 行）。
  保留的判断依据（不是凭感觉）：`providers/prompts.py` **必须留**——`openai_compat.py` 的 `thin_summary` / `summarize_chapter` 都靠它的 `SYSTEM_MESSAGE` / `chapter_user_message`；`FakeProvider.summarize_chapter` **必须留**——`creation/summarize.py` 的 `provider_for_writer` 对 `FakeChapterWriter` 返它，删了 Fake 写作路径就没有确定性摘要；`creation/providers.py` + `creation/prompts.py`（一次性五部件骨架生成）**必须留**——插件 `lib/client.js:921` 在「导入的稿子里没有细纲」时真在调 `POST /projects/<id>/generate`。
  `_import_reference` 随之重写：不再调 `run_analyze`，只跑规则层拆书；`total_plot_points` 固定写 0，并注明「情节点只有旧分析管道会产出，规则层拆书不造情节点。字段保留是为了旧工作区还能读，新导入一律 0——不编造没测过的东西」。拆书失败从「静默降级、只丢结构信息」改为**直接回 400**（旧管道没了，失败就没有任何产物可留，静默成功比报错更糟）。内置 UI `panel/web/app.js` 的参考书卡片同时重写：从主题分布/角色出场/爽点节奏三张卡换成结构剖面 + 钩子节拍两张卡（顺带修掉一个真实崩溃：`agg?.pacing.satisfying_chapters.join()` 在 `agg` 为 `undefined` 时必抛 TypeError，可选链只兜住了 `pacing` 之前那一段）。

- **模型定义归位：`creation/models.py` + `deconstruct/models.py` → `domain/`**：`domain` 本是全仓唯一零内部依赖的叶子，`creation/models.py`（180 行）与 `deconstruct/models.py`（266 行）也都是只 import pydantic 的纯模型文件——**模型放错包直接造出三条包级循环**：`retrieval → creation.models`（`retrieval/context.py:15` 要 `ChapterOutline`，而 `creation` 又要 `retrieval.context`）、`workspace → creation.models`（`workspace/models.py:17` 要 `CreationDraft`，而 `creation` 要 `workspace.store`）、`workspace → deconstruct.models`（要 `BookDeconstruction`）。三条循环单看都只是「拿个类型定义」，但类型定义一旦跨包就是反向依赖。归位后变成 `domain/creation.py` 与 `domain/deconstruction.py`（33 个文件、40 处 import 全改），`workspace` 的包依赖从 3 个降到 1 个（只剩 `domain`），`retrieval` 不再指向 `creation`。
  同包合并时撞出一个必须处理的坑：`deconstruct/models.py` 自带 `SCHEMA_VERSION = "bd-v1"`，而包根 `ndecon.SCHEMA_VERSION = "v0"` 且 `domain/models.py` 正是 `from ndecon import SCHEMA_VERSION`——合进同一个包后两个同名常量不同值，读代码的人分不清、改错一个就是数据版本对不上。已改名为 `DECONSTRUCTION_SCHEMA_VERSION`，并把改名理由写进注释与 `BookDeconstruction.schema_version` 的默认值。

- **`CreationProject.summaries` 死字段**：摘要在 `cb925b6` 就已外置到 `summaries/chNNN.json`，但模型上仍留着这个字段，生成链路从不写它——`agent/tools.py` 据此读它，`SearchOwnBook` 拿到的永远是空列表，等于「已写章节入索引」那次修复从未生效。现在字段删除，`ToolContext.summaries` + `run_planner(summaries=...)` 由调用方显式传入；旧工作区的内联摘要由 `WorkspaceStore.get()` 在反序列化**之前**从裸 dict 取走并搬盘（顺序不能换：pydantic `extra="ignore"` 会先静默吞掉该键）。

### Changed

- **同一个函数名在三处各写了一遍（有的能合，有的必须分开，逐个判过）**：全仓重名 `def` 扫出五组，逐组给结论——只说「合并」或「保留」不讲为什么，下次还会长回来。
  ① `_strip_title_line`（`deconstruct/pipeline.py:65` 与 `providers/fake.py:39`）**逐字相同**：都是找第一个 `\n`、返回 `(正文, newline+1)`、无换行返回 `(原文, 0)`。已提到 `ingest/splitter.py` 的公开 `strip_title_line()`，两边都改成 import。它算的是「章切片内绝对偏移」，两边各写一份迟早出现偏移口径不一致，而偏移错了 quote 就定位到错误位置——这种错在测试里很难看出来。
  ② `content_hash` **名字一样、语义必须不同，不能合并**：`creation/summarize.py:30` 是原样正文取 SHA-256 前 16 位（只用于同项目内区分章节版本、串 run record，越短越好）；`domain/ids.py:34` 先 `normalize_for_hash` 再取全 64 位（拆书 quote 的偏移要能在源文件被轻微改写后仍判等，要的是「同内容同 hash」）。两个都加了注释说明对方存在、以及合并会坏在哪里。
  ③ `paragraphs_of` **名字一样、口径必须不同，不能合并**：`quality/style.py:237` 按句末标点并入（判据要作者的真实分段意图，`_MAX_PARA_CHARS=400` 兜底），`render/export.py:79` 按空行切（排版要作者自己敲出来的分段）。两边都加了注释点名另一个的存在——用错一边，要么段均句数/单句段占比整套判据失准，要么导出的 docx 里每句话变成一段。
  ④ `_evidence` **同名不同返回类型，各管各的**：`quality/gates.py:146` 返 `list[str]`（硬门要讲「哪几处违规」），`quality/style.py:438` 返 `str`（文风判据只讲「命中哪一段」）。同包不同模块，不构成冲突，只在 docstring 里点名区分。
  ⑤ `_char_count` 只剩 `deconstruct/pipeline.py:60` 一处（原 `pipeline/stage0.py` 那份随退役删掉），docstring 里的「与 stage0 同一口径」已同步改成「与 `quality.style.cjk_count` 同为不算空白与换行口径」。

- **`workspace` 与 `retrieval` 的循环依赖真正断掉了**：`workspace/store.py` 顶层 `from ndecon.retrieval.bm25 import BM25Index` 只为给 `load_project_index()` 写返回类型注解，而 `retrieval` 包反过来要 `workspace.models`（`CreationProject` / `is_usable`）——两个包互相拉进一个环。改法是把顶层 import 换成 `if TYPE_CHECKING:`（类型注解照常有、运行时不导入），方法内那一处 import 保留（它本来就该延迟到真正要还原索引时）。实测 `import ndecon.workspace.store` 后 `ndecon.retrieval.bm25` 不在 `sys.modules` 里，说明环真的断了，而不只是「碰巧没炸」。


## [0.2.0] - 2026-10-01

### Fixed

- **知识库版权闸门封堵 markdown 章节形态全本（#27）**：真实资料库 dogfood 发现版权全本以 `## 第N章` markdown 归档时双重漏网——章节行正则不接受 `#` 前缀（0 命中），前40章文件名/1MB 大小规则仅对 txt 生效（0.27MB 的《斩神》前40章 md 被切成 3195 块错误收录）。修复：①章节行正则允许 markdown 标题前缀；②分析标记豁免从文件名扩展到父目录名（保护 `拆解/Untitled.md` 式原创大纲）；③新增内容侧豁免，文本前部评论/方法论术语（拆解/爽点/钩子/伏笔/节奏等 21 词）≥8 次判为自研分析。阈值由 48 个真实章节体候选统计确定（全本 2 次 vs 最稀疏真拆解 11 次）。真实库重建全库仅 1 文件翻转、自研作品零误伤；收录 291→290 文件、37834→34639 块；新增 4 个测试（共 143 全绿）。

### Changed

- **本地 pre-commit 启用（#9）**：`.pre-commit-config.yaml` 改为 `repo: local` + `language: system` 直接调用本机 ruff 0.16.8，不再 clone GitHub（适配断网/SSH-only 环境）；pre-commit 4.6.2 已安装并 `pre-commit install`，`pre-commit run --all-files` 通过；CONTRIBUTING 安装说明同步。

### Added

- **面板内模型供应商选择与 key 配置（#23）**：顶栏「模型设置」弹窗内置 8 家 OpenAI 兼容供应商（DeepSeek/智谱 GLM/硅基流动/月之暗面 Kimi/通义 DashScope/本地 Ollama/OpenAI/自定义），选择后自动填充 base_url 与可选模型；支持「测试连接」（轻量 chat 请求，返回延迟与可读错误，如 401 鉴权失败）；key **只存面板进程内存、不落盘、GET 不回传、重启清空**，同供应商留空保存保留旧 key。骨架生成新增「AI 生成」按钮，章节 AI 生成统一走该会话配置；未配置时明确 400 引导，绝不静默回退。新增 10 个测试（共 139 全绿）。
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

[Unreleased]: https://example.com/compare/v0.2.0...HEAD
[0.2.0]: https://example.com/releases/tag/v0.2.0
[0.1.0]: https://example.com/releases/tag/v0.1.0


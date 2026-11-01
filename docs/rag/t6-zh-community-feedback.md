# t6 中文社区口碑收集：多智能体/Agent 团队一线反馈

> 采集人：mas-scout-a（rag-core2）· attempt 74656e68-da5c-47a4-8547-a309d0d90be0 · 日期 2026-10-23
> 方法：agent-reach（opencli 后端）逐平台检索；知乎用 `opencli zhihu search` + `opencli zhihu download` 取全文，贴吧用 `opencli tieba search/posts/read` 取楼层，B 站用 `opencli bilibili search/video/summary/comments`，V2EX 用公开 API + sov2ex 检索 API（v2ex.com 直连 403）。
> 说明：只收有信息量的；平台无结果或只有广告/噪声的已明确标注。

---

## 一、真实失败案例（静默失败 / 假完成 / 重复劳动 / 责任稀释）

1. **V2EX · caesor《最近在做一套多 Agent 协作系统，遇到一个让我头疼很久的问题》**，2026-03-30，node864，2 回复
   原话："系统里有 6 个 Agent 同时运行（内容创作、发布、数据分析、增长策略等），每个 Agent 完成任务后会汇报状态。但汇报本身不可靠——有时候 Agent 会在会话内输出「任务完成」，但上游 orchestrator 根本收不到通知。"
   原话："「说了不等于收到」：Agent 在自己的会话里输出文字 ≠ orchestrator 看到了"
   原话："任务超时无感知：如果没有 watchdog，一个 Agent 卡住了，整个流水线就挂着没人知道"
   解法（可借鉴）：强制双通道回调 = sessions_send + 写 completions.log；watchdog 每 5 分钟扫 dispatch.log。
   链接：https://www.v2ex.com/t/1202232

2. **贴吧 · 贴吧用户_aJG8a8W《DeepSeek×codex双智能体协作》**，2026-10-20，deepseek吧
   楼主设想"一个执行者，一个审核者……另一个负责独立审核，提出建议和验证结果"；2 楼贴吧用户_Ja6b1CK 原话：
   "其实不用这么复杂 工作区内自己写个分工规则文本 内容划清工作边界 只用主agent作为工作面板即可 文本类的如果你有总则 只需要对比总则 有skill就总则+skill 完全无需频繁自检 此外 你人工把规则定死 把工作流定死 把模块切分好 比折腾多模写作更好"
   链接：https://tieba.baidu.com/p/11120664818

3. **B 站 · 阿喵讲AI《多Agent总翻车？项目越做越乱，问题常出在这2类，从任务拆分失误到并行协作失控全拆解》**，2026-07-09，播放 226、赞 2、回复 1（回复是作者自己的引流评论，无实质讨论）
   元数据：https://www.bilibili.com/video/BV1EeME63ESz （视频标题即结论：任务拆分失误 + 并行协作失控两类；播放极低说明此类"踩坑"内容在 B 站没有受众）

4. **知乎 · tomSheep《为什么多 Agent 系统总是失败？》（UC Berkeley MAST 论文解读）**，2025-05-03
   原话（论文结论）："很多 MAS 的失败源于系统设计和智能体协调问题，而非仅仅是底层 LLM 能力的限制"
   数据：7 个 MAS 框架、200+ 任务、6 位专家标注、Cohen's Kappa 0.88；14 种失败模式三类，FM2.2 不主动寻求澄清 / FM2.4 信息隐藏 / FM2.6 推理-行动不匹配 出现频率 14.0%。
   典型假完成案例："ChatDev 国际象棋案例=验证 agent 只查编译不查规则→'通过测试但根本玩不了'"；AppWorld 取 Spotify 前 10 首歌，协调 agent 与 Spotify agent 聊了 10 轮每轮一首（重复劳动/成本飙升）。
   链接：https://zhuanlan.zhihu.com/p/1900953262250780260

5. **知乎 · Meta《多Agent系统为啥总翻车？》**，2025-09-14
   原话："MAS 性能'并未显著超越单 Agent 系统，甚至低于简单的 best-of-N sampling'"
   原话："多智能体系统的失败并非单一 LLM 缺陷（如幻觉）的产物，而是系统设计、交互协议与验证机制的综合结果"
   数据：ChatDev 某些任务正确率仅 25%；AG2 数学改进 prompt 后 GPT-4o 84.25%→89%，HumanEval 89.6%→91.5%，ProgramDev 仍仅 40.6%；"拓扑重构效果有限"。
   链接：https://zhuanlan.zhihu.com/p/1889732067119584810

6. **知乎 · KevinChen《多智能体三年反思：为什么 Agent 越多，系统反而越乱？》**，2026-05-02（责任稀释/上下文污染的一线表述）
   原话："产品经理说用户要 A。架构师基于自己的理解画了 B。开发 agent 没等两边对齐，直接写了 C。最后你得到的不是一个功能，而是一堆彼此不兼容的中间产物。"
   原话："多一个 agent，就多一份上下文。多一份上下文，就多一份污染风险。"
   原话："如果没有控制，5 个 agent 的复杂度不是 5 倍，而更像 25 倍。"
   原话："并行最适合扩大观察面，不适合抢同一个方向盘"；误用警示："critic 不能替代测试"。
   链接：https://zhuanlan.zhihu.com/p/2034003763179074255

7. **知乎 · CyPaul Space《为什么不建议构建多智能体？《Don't Build Multi-Agents》博客解读》**，2025-06-13
   原话（Cognition Walden Yan）：两个子代理独立做背景和小鸟，"像素分辨率、色彩空间有很大概率发生协调冲突"，成品出现"Super Mario 风格的背景配卡通风格角色"。
   原则：全局上下文共享 + 决策一致性约束；建议回归单线程线性 agent（中短任务，10 分钟内）或压缩中继（长任务）。
   链接：https://zhuanlan.zhihu.com/p/1916865679484747890

---

## 二、规模经验（几个 agent 合适 / agent 数与效果）

8. **V2EX · zisen**（回复 node1203 主题），2026-06-04
   原话："现在很多 subagent 模式都是派很多子代理去探索代码库，派另一个子代理去研究方案，派一个子代理写代码实现需求，但是如果说整个工作内容都能在 1M 窗口内被做完，子代理就没有任何优势了，特别是现在缓存价格非常便宜，每次都新开子代理的话，是吃不到缓存价格的"
   链接：https://www.v2ex.com/t/1217883

9. **V2EX · terryso**，2025-08-21（ccusage 工具分享帖内的结论）
   原话："不要定义太多子代理, 每一个子代理都占用上下文"
   链接：https://www.v2ex.com/t/1154037

10. **V2EX · ursash《我的 AI 工作台：多 Agent 协作和统一上下文》**，2026-10-19，node1170（正面但克制的规模样本：6 角色）
    原话："目前我把 Agent 分成了六个角色，也是模仿现在比较流行的用 Agent 组建软件团队的做法。每个角色都是独立的会话，即使模型相同（比如 Lead 和 Architect 都是 Opus），也会单独开一个子 Agent。"
    角色表：Lead(Opus 5.5 Medium)/Architect(Opus 5.5 High)/Developer(Composer 2.5 Fast)/Reviewer(GPT-6 Astra High)/QA(GPT-6.1 Sol XHigh)/DevOps(Composer 2.5 Fast)。
    原话："改动风险高一些的，再交给 Reviewer 审代码、QA 实际跑一遍；有问题退回 Developer 修，最后由 Lead 核对真实的输出结果再交给我。"
    成本侧：ChatGPT/Codex Pro $200/月、Claude Code Pro $20×2、Cursor 企业订阅 $300/月额外额度、X Premium $30/月、ZenMux $20/月。
    链接：https://www.v2ex.com/t/1250009

11. **V2EX · OumaeKumiko《想尝试 Claude + Codex + DeepSeek/Qwen/... 协同多子代理，大家目前用的什么方案？》**，2026-07-24，node1148（"多个便宜模型听一个强模型调度"的动因）
    原话："我非常讨厌 GPT 的说话风格（充满伪人感🤮），5.6 系列还有强烈的过度完成、简单任务被复杂化的倾向。"
    原话："所以想着能不能让强模型当大脑，指挥 DeepSeek / Qwen / GLM 等便宜模型去干活，省下一大截钱。"
    方案清单（4 类，均未定论）：Claude Code + CCR 路由 + sub-agent；Claude Code + headless OpenCode/Codex CLI 当 worker；Codex 为主 + 第三方路由；OpenCode 原生多模型 Agent Teams。
    链接：https://www.v2ex.com/t/1229576

12. **V2EX · wmxpop《别急着上智能体，有些问题一个对话框就够了》**，2026-10-23，node829（同一天新帖，"过度工程"复盘）
    原话："人家要的就是个'对话框'"
    原话："顺序大概是这样：能不能不做，能不能用代码，能不能用对话框，最后才轮到智能体。"
    原话："智能体留给真正需要它的活……得同时满足三条：任务本身多步骤，中间结果要喂给下一步；要跨系统动手，读文件、调接口、改东西；用户愿意为它付安装成本。"
    原话："运行那边，一次对话就是一次 API 调用，智能体要跑多轮、要调工具、要维护状态，token 和延迟都翻几倍。论出错，答得不好再问一次就行，智能体中途跑偏可能真的改错文件。"
    链接：https://www.v2ex.com/t/1251249

13. **知乎 · deephub《为什么你的多智能体比单智能体更贵更慢，质量还更差》**，2026-09-08（最硬的一条规模/成本实测）
    原话："我们团队按照教程搭了一套标准的多智能体：Researcher、Writer、Editor、Manager……到了生产环境，报告质量也算过得去，不过成本是原来单智能体的 12 倍，耗时是 4 倍；真正做质量评测时这套团队系统还比一个 Prompt 写得好、工具配置合理并带压缩循环的单智能体低了 2 分。"
    原话："在一个根本不需要团队的任务上交了团队税"
    原话（Anthropic 数据）："普通智能体大约消耗聊天交互 4 倍的 Token，多智能体系统大约是 15 倍"，把工作 agent 路由到小模型可把 15 倍压到接近 6 倍。
    判断线："任务的工作集（working set）超过一个上下文窗口时多智能体才开始有意义"。
    链接：https://zhuanlan.zhihu.com/p/2080771508289402826

---

## 三、成本感受（token / 时间 / 钱）

14. **V2EX · sungnix**，2026-03-20，node1319
    原话："下午试了下腾讯刚公开发布的 QClaw，两个人物耗费了约 590 万 TOKEN，要求制作的 PPT 最后也没能生成。两次会话的 TOKEN 消耗合计和总计消耗 TOKEN 也对不上😂"
    链接：https://www.v2ex.com/t/1199899

15. **V2EX · junwind**，2026-03-08，node300，5 回复
    原话："我买了火山云的 coding plan lite，cluade code 接入 auto 模型，我随便问几个问题，token 消耗蹭蹭往上涨，大家都是这样的吗，有没有降低消耗的方案。"
    链接：https://www.v2ex.com/t/1196631

16. **V2EX · dododada**，2026-09-21，node300
    原话："最近发现做方案、做计划、计划审核、测试用例、包括代码实现、逻辑分析、边界处理之类的，token 消耗都还好；随着积累的文档越来越多，如果需要做文档的复核，token 消耗就会很多，各种关联分析"
    链接：https://www.v2ex.com/t/1243568

17. **V2EX · kuicc1987《问一下，你们一个月大概花多少钱在 AI 上？》**，2026-10-15，node557（个人成本样本）
    原话："ChatGPT plus 套餐，每个月续 20 刀；朋友那边的中转站，每个月 230 左右；DeepSeek 官方 API 充值，每个月 100 左右。"
    链接：https://www.v2ex.com/t/1248969

18. **V2EX · idoubi《从 OpenClaw 到 FastClaw：如何设计优秀的多 Agent 架构》**，2026-06-22，node17（托管 500+ 用户后的架构账）
    原话："默认上下文偏重，Token 容易失控……OpenClaw 每轮都会把一组 bootstrap 文件——SOUL.md、MEMORY.md、AGENTS.md、工具说明——注入提示词。文件一多、对话一长，system prompt 越堆越大……bootstrap 写得啰嗦，账单就很可观。钱烧得心疼。"
    原话："Gateway 是单点故障……一个 Plugin 内存泄漏，整个 Gateway 崩溃；一个 Channel 的 API 超时，所有 Channel 都卡住。没有隔离，就没有可靠性。"
    原话："隔离是围绕'Agent'设计的……但没有'用户（account）'这层抽象。要真正隔离，得'一人一 Agent'。"
    链接：https://www.v2ex.com/t/1222063

---

## 四、框架 / 工具选型教训

19. **V2EX · node300《锐评一下用过的 Agents/Harness》**，2026-09-18（一篇横评 9 家 harness 的一手感言）
    - claude code："终端 agent 的鼻祖……很多概念都是 anthropic 提出并在 cc 首发的，例如 subagent，workflow，很喜欢 focus 模式……但是最后发现接其他模型缓存命中率就是比 chat 协议的 agent 第一档，然后走网关那边也修不好，cc 也不开放源码，就放弃了。"
    - codex cli："大白嫖时代的神……后面更新了，强制 responses 协议，便宜号也没了，开发重心转向桌面端了，而且界面还是很烂，几个月了一点改进都没有，遂放弃。"
    - deepseek harness（DSH 本身）："万众期待的 harness，但是出来的是一个预览版……pi 出来就是毛坯房，用户自己装修，而 dsh 告诉你他先给你一个毛坯房，但是你自己装修的时候，官方会把墙拆了，布局改了，你装修的都白装修了，在迭代了几个版本之后我决定等 dsh 稳定了之后再来使用。"
    - pi/omp："tui 太丑了……尝试让 ai 自己开发插件，但当时 ai 能力不强，开发的 tui 插件总是有 bug，我自己也不懂解决不了，反复 push 几轮就不想尝试了。"
    - opencode："session 用数据库管理而不是 json 文件，功能对比 cc 缺少了一些，总之用起来不顺手。"
    - gemini cli："额度实在少的可怜，随便用下就 gemini 额度没有了。"
    - copilot："远古时期唯一能用的 agent……每月可以向神提问三百次，所有的代码都得截取片段拖到对话框里。"
    - grok（当前主力）："界面流畅，功能齐全，还开源，对标 cc，cc 的 mcp 都可以被无缝衔接到 grok 里面，默认 chat 协议对缓存友好。"
    链接：https://www.v2ex.com/t/1242976

20. **V2EX · zovy123《多 Agent 框架选型对比》**，2026-03-05，node16，1 回复（CrewAI/LangGraph/AutoGen/OpenAgents/ChatDev/CAMEL 逐条点评）
    原话（CrewAI）："CrewAI 是近期流行的轻量级多 Agent 团队框架……号称开箱即用且性能高效，完全自主实现（不依赖 LangChain 等），因此运行开销小、执行速度快。CrewAI 的上手成本很低……缺点是相比 LangChain/AutoGen 这种底层框架，它抽象层更高，开发者对每步细节的定制控制略少……对高度非线性的动态场景可能需要自定义 Python 代码扩展。"
    原话（LangChain+LangGraph）："优点是生态成熟、工具整合丰富……缺点是 multi Agent 功能相对新，需要熟悉 LangGraph 的 Graph 定义；另外 LangChain 本身较重，构建简单流水线时可能显得学习成本偏高。"
    原话（OpenAgents）："优势是在大规模 Agent 网络、跨系统协作方面功能强大……劣势是对一般应用来说过于厚重，初始配置和部署成本较高。如果仅在单个应用内组织少量角色 Agent，直接使用 OpenAgents 可能大材小用。"
    原话（ChatDev 2.0）："优势是学术前沿技术（如可学习的调度、Agent 网络拓扑）已经融入框架……劣势是框架相对庞大、概念较多，上手需要一定学习成本。"
    链接：https://www.v2ex.com/t/1196125

21. **V2EX · OXOYO《My Agent Team》**，2026-04-29，node300（自建 10 agent 体系的工程约束，正面样本）
    原话："一个开箱即用的多 Agent 协作体系 Kit……基于 14+ 轮真实迭代打磨：10 个专职 Agent + 9 条强制规则 + 3 个自建 Skills + 7 份文档模板 + 9 个脚本。"
    原话（关键治理机制）："HOTL 自治流转：L0 全自动 / L1 半自动 / L2 手动确认，人监督而非逐步确认"；"三级验收门禁：test-runner → code-reviewer → product-designer，禁止跳步"；"熔断保护：同环节返工 ≥3 次自动回退手动模式"。
    链接：https://www.v2ex.com/t/1209315

22. **B 站 · Zhiyong的生成式《为什么 multi-agent 会失败》**，2025-03-26，播放 46403、赞 1866、弹幕 20、回复 168（B 站该话题播放量最高）
    AI 总结原话："作者认为多智能体系统不应该模仿人的协作分工，而应该探索能够发挥群体智能的场景，如引入对抗或启发机制。"
    AI 总结原话："多agent不应模仿人的分工与并行, 更适用于群体智能；适合引入agent对抗与启发机制, 如谷歌COSCIENTIST系统。"
    链接：https://www.bilibili.com/video/BV14hovYZEV5

---

## 五、平台级结论（哪些平台有/没有结果）

- **知乎**：信息量最高，5 篇全文已下载细读（见上 4/5/6/7/13），另有 20 条高相关候选未逐篇展开（Zilliz《80%的 Multi-Agent都是伪需求！》p/2014296290327025275、问题《为什么在生产环境部署多智能体系统容易出现成本失控》zhihu.com/question/1979960176271438606、问题《多Agent一定比单Agent更强吗？AI应用到底该在什么节点拆分？》zhihu.com/question/2023445311293506589、《Thinking in Context: 何时需要多智能体》p/2010163501814010621、《我为什么放弃多 Agent"群聊"》p/2044071758656013914、《为什么你写的 LangGraph 多智能体一上线就崩溃？聊聊 Supervisor 与 Worker 的状态隔离》p/2048105195020800029、《做agent使用LangGraph你一定也踩过这些坑》p/2081326087133466799、《CrewAI 深度剖析——与自建 Agent 系统的差距在哪》p/2031083853499060681、《muti-agent体验：使用CrewAI搭建一个简单任务》p/1936111868834977117、《MetaGPT框架学习个人心得体会》p/684460108、《一百万个 Agent，面临的是分布式系统问题》p/2088362449506508909、《实在受不了 Codex 的多 Agent 插件，自己撸了一个…编排工具》p/2032401788427573161、《AI 系统不是 Agent 越多越好》p/2074925199921778698、《多个 Agent 一起工作，为什么不一定更可靠？》p/2087982612321255692、《都说刷短剧会"降智"，我用 Agent 干活，怎么也越来越累了》p/2094057618612433720、《Agent 框架横向对比：LangChain、LangGraph、AutoGen、CrewAI、smolagents、OpenAI Swarm 和 OpenManus》p/1984725399767376336 等）。知乎问题页答案正文需登录态，opencli zhihu 未取到，属本平台未覆盖部分。
- **贴吧**：整体质量低。搜「多智能体」只命中 deepseek吧自建小说写作系统（alekes 2025-12-30 https://tieba.baidu.com/p/10350687233，"多llm分工的循环审阅修改，从而减少幻觉保证设定的严谨性，同时通过资料管理员agent和上下文工程来尽可能的节约上下文"）、sillytavern吧 FlowMAS 游戏框架内测（提到"抢话、不推剧情、乱推剧情" https://tieba.baidu.com/p/10223530679）、985吧南大参与多智能体国标（2026-06-27 https://tieba.baidu.com/p/10826084905）。**搜「多智能体 踩坑」「AI agent 翻车」贴吧无相关结果**；搜「CrewAI/LangGraph」几乎只有课程广告贴（软件测试开发吧《10+热门 AI Agent 框架深度解析》2025-07-15 https://tieba.baidu.com/p/9873265548，短评"CrewAI 多角色分工清晰……分工设计合理才有用；LangGraph 控制逻辑强、支持DAG流程图……适合技术架构师主导"）。**另有一条 DSH 自身事故**：deepseek吧理壮 2026-10-17《求助dsh智能体团队插件》https://tieba.baidu.com/p/11113091204，原话"我开了 智能体团队 插件后，软件突然卡了一下，然后工作区全消失了，刚才的对话也全部没了"，5 楼回"可能是乱动权限给工作区干崩了"——可作"工具/权限边界"例证，与多智能体效果相关性弱，供参考。
- **B 站**：话题播放量高但内容以教程/论文解读为主，一线实操反馈少。已核 3 个视频元数据与 AI 总结（见 3、22；另《别再乱用 Multi Agent 了！单 Agent 优先，按需上多 Agent》朱老师AI课堂 2026-10-14 https://www.bilibili.com/video/BV1napC6SExk，播放 515、赞 7，标题即立场，无正文可引）。`opencli bilibili comments` 对测试视频返回 EMPTY_RESULT，评论区未取到，属未覆盖部分。其他高播放候选：《撕开遮羞布，看看Agent到底有多烂》差评前沿部 198408 播放 https://www.bilibili.com/video/BV1xAigBZEti；《AI为何开始欺骗人类｜OpenAI智能体失控事件》644832 播放 https://www.bilibili.com/video/BV1WqpS66E7w；《Agent Teams/Swarm究竟是什么？7分钟看懂100个Agent团战的技术原理》小白debug 253630 播放 https://www.bilibili.com/video/BV1o3ADzZEmg；《LLM多智能体工作流翻车的隐形原因：要求从 必须 变 可能》熊二等兵 https://www.bilibili.com/video/BV13t4Z6WEdm；《[多Agent规划] ICML26-EPC-AW：多智能体动作都对，为何仍会失败？》Agent智能体深度研究院 2111 播放 https://www.bilibili.com/video/BV1nf8265EZ8。
- **V2EX**：实操密度最高（8 条全文），且与 t6 关注的失败模式/成本/选型高度吻合。v2ex.com 官方 API 与 opencli v2ex 均不可用（403 / Connect Timeout），改走 sov2ex 检索 API 成功。
- **掘金/微信公众号**：本次未检索（时间预算优先给前四平台）；如需补充可用 `opencli juejin`/web 兜底。

---

## 六、给 captain 的两条浓缩结论（供写 Day19 风格文档直接引用）

- **失败根因集中在"协调与验证"，不在模型能力**：V2EX caesor 的 6-agent 系统证明"Agent 说完成了 ≠ orchestrator 收到"（假完成），需双通道回调 + watchdog；知乎 MAST 解读给出系统化对照（14 种失败模式、验证类失败、干预仅改系统设计即 +9.4%~15.6%）。
- **规模的默认答案偏小、且必须先算成本账**：一线共识是"1M 窗口内做完就别开 subagent"（V2EX zisen）、"不要定义太多子代理"（V2EX terryso）、"5 个 agent 复杂度像 25 倍"（知乎 KevinChen）；deephub 的实测是 12 倍成本/4 倍耗时/质量反而低 2 分，Anthropic 口径是聊天 4 倍、多智能体 15 倍 token；V2EX 有 590 万 token 没做出一份 PPT 的极端样本。选型上，社区对"重框架 + 群聊"普遍警惕，对"主会话 + 定死规则 + 少量专职角色 + 验收门禁"更买账（V2EX OXOYO 的三级门禁与返工熔断是可复用机制）。

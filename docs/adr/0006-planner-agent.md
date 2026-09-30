# 0006 · 细纲/设定 Planner Agent：受限工具循环

- 状态：Accepted
- 日期：2026-09-26
- 关联：[ADR-0004](0004-rag-tiers-and-adoption-gates.md)（RAG 分层）、[ADR-0005](0005-writing-knowledge-base.md)（知识库）、#15/#16/#17/#21、#25

## 上下文

用户需要一个"帮助丰富细纲与设定的 agent"。调研 5 个开源项目代码后（goink/ai-novelist/NovelClaw/
AI_NovelGenerator/webnovel-writer）+ 相关论文（StoryWriter/CreAgentive），得到两个事实：

1. 网文写作开源圈里真正的"工具循环 agent"只有 goink（模型每轮自主选工具，循环执行观察结果）；
   其余项目（AI_NovelGenerator 的 Step 流水线、NovelClaw 七角色串联、StoryWriter/CreAgentive
   的多 agent 固定网、webnovel-writer 的确定性投影）本质都是代码定步骤的 **workflow**。
2. goink 的自主循环用在正文与记忆维护环节；**细纲/设定规划阶段没有项目用真 agent**——这是空白带。

用户已明确否决 workflow 包装，要模型自主决定查什么、补什么。

## 决策

新增**单主体 Planner Agent**：在"丰富指定章节节拍"或"围绕主题补设定"的窄任务内，
模型通过 JSON 动作协议自主调用工具、观察结果、循环推进，直到提交 `finish`。

### 工具集（8 个，白名单物理隔离）

- 只读 5：`search_methodology`（L0.5 知识库）、`get_outline`、`get_confirmed_setting`、
  `search_own_book`（L1 BM25）、`list_open_foreshadows`
- 写候选 2：`draft_beats`（节拍）、`draft_setting`（设定条目）——只能产 draft，不能确认/删除/改正文
- 终止 1：`finish`（汇报查询路径与发现的矛盾）

### 循环与安全（clean-room 自 goink 实测机制）

1. 动作协议为 JSON `{thought, tool, args}`，不依赖厂商 function calling（DeepSeek/智谱/Ollama 通用）
2. MaxTurns = 12（任务窄；goink 开放对话给 100）
3. **死循环检测**：近 4 轮调用模式去重后 ≤2 种且全为只读工具 → 中断，保留已产出候选
4. 工具异常不中断循环，错误文本（截断 200 字防注入）作为 observation 回喂模型自愈
5. token 超预算时截断最早 observation（第一版不做 LLM 摘要压缩）
6. 每轮 thought/tool/args/observation 落 run trace，面板可见完整思考链

### 双轨与产物

- FakePlanner：规则脚本按固定动作序列（伏笔→方法论→细纲→提交→finish）模拟循环，离线可测
- OpenAICompatPlanner：复用 #23 面板会话 provider 的 JSON 通道
- 产物：**Beat**（场景/出场角色/冲突三角/情绪走向/埋收伏笔）与
  **SettingEntry**（世界观规则/势力/配角/地点/道具/力量体系），一律 draft，
  经用户逐条确认才并入权威细纲/设定库

## 后果

- 好处：规划阶段具备真自主性（模型按中间结果决定查什么/补什么），同时边界、预算、可审计性可控；
  填补调研发现的空白带；工具注册表让 L2 向量检索、子 agent（审稿）日后可即插即用。
- 代价：真实 LLM 每次任务多轮调用，token 成本高于一次性生成（用窄任务+12 步上限+死循环检测约束）；
  循环结果天然不完全可复现，确定性保证下沉到工具层（检索/校验/落盘仍纯函数），Fake 路径供 CI。
- 放弃的方案：
  - 多角色固定编排（NovelClaw/StoryWriter 式）——已证为 workflow，不是用户要的自主性；
  - 在正文生成（#16）引入自主循环——破坏 ContextPack 的确定性与版权边界，永不开放；
  - 原生 function calling——绑定厂商格式，与 BYOK/本地模型目标冲突；
  - LLM 自动摘要压缩、run_subagent——第一版不做，接口预留。

## 实施备注（2026-09-27，第一版落地）

- 决策第 5 条「token 超预算时截断最早 observation」第一版**未实现**：12 步硬上限 +
  单条 observation 自身较短（方法论片段截 300 字、错误截 200 字）已使上下文规模有界；
  待真实 LLM dogfood 证明有压力时再补，不提前优化。
- 面板 API：`POST /api/projects/{id}/agent/runs`（同步返回 trace 与候选）、
  `PUT /api/projects/{id}/agent/accept/{beats|settings}`（候选确认才入库；beats 覆盖本章、
  settings 同名同类型去重追加）。
- 测试：11 个单元测试（工具白名单/参数校验/死循环/步数上限/Fake 确定性）+
  5 个面板契约测试，共 155 测试全绿；浏览器端到端实测两条任务闭环通过。

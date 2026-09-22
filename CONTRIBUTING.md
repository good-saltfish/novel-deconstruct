# 贡献指南

感谢参与 novel-deconstruct（ndecon）。本文是参与本项目的唯一流程入口：开发环境、分支纪律、提交规范、DoD 都在这里。

## 一、项目形态与红线

- 本地优先的中文网文拆书 CLI；**章节切分是纯确定性代码，LLM 永不参与边界判定**。
- 模型产物一律带 provider/prompt/schema 版本；FakeProvider 输出必须标记 `rule:fake`。
- **学习层（"可借鉴要素/为什么有效"）只能由使用者填写，任何 provider 不得自动填充。**
- 版权正文与大段 quote 不进仓库；测试夹具只允许 CC0 自造文本。

## 二、开发环境

```powershell
# 当前开发环境：Python 3.10.4（D:\Python\Python310\python.exe）
D:\Python\Python310\python.exe -m pip install -e ".[dev]"
D:\Python\Python310\python.exe -m pytest
```

可选（网络可用时）：

```powershell
pip install pre-commit ruff
pre-commit install
```

## 三、Issue 驱动（持续流，Kanban）

本项目不设固定 sprint，按看板拉动：**Backlog → Doing → Review → Done**（见根目录 [KANBAN.md](../KANBAN.md)）。

- 新工作先开 Issue：功能用 `feature` 模板（用户故事 As a… / I want… / so that… + 验收标准），缺陷用 `bug` 模板。
- 标签约定：
  - 类型：`type/feature` `type/bug` `type/docs` `type/refactor` `type/test` `type/chore`
  - 优先级：`priority/high` `priority/medium` `priority/low`
  - 状态辅助：`good first issue` `blocked`
- 一次只做一个 Issue；开始做即移入 Doing 并建分支。
- 远程仓库启用前，Issue 清单以 KANBAN.md 中的编号为准，建仓时按编号创建。

## 四、分支与 PR 纪律（混合制）

| 变更类型 | 路径 |
|---|---|
| 新功能、bugfix、架构调整 | feature 分支 + PR（必须过 CI 与 DoD 自查） |
| 文档、typo、注释、配置微调 | 可直接推 `main`，仍需 conventional commit |

分支命名：`feat/<issue-id>-<短描述>`、`fix/<issue-id>-<短描述>`、`docs/<短描述>`。

主干模型：GitHub Flow（`main` 始终可发布；远程启用后对 main 开分支保护，PR 需 CI 全绿）。

## 五、提交信息规范（Conventional Commits）

```
<type>(<可选 scope>): <祈使句简述>

<可选正文：为什么这样做，而不是做了什么>
```

type 取值：`feat` `fix` `docs` `style` `refactor` `test` `chore` `ci` `perf`。

示例：`feat(providers): add openai-compatible provider`、`fix(splitter): accept comma in separated short title`。

## 六、Definition of Done（功能类 PR 必须全部满足）

- [ ] 关联 Issue（`Closes #N`），验收标准逐条可回答
- [ ] 新增逻辑有测试，`pytest` 本地全绿（CI 同跑 3.10/3.11/3.12）
- [ ] 新增/修改函数有函数级注释（中文）
- [ ] 确定性与证据纪律未被破坏（切分无 LLM；quote 偏移可定位；takeaways 无模型填充）
- [ ] CHANGELOG.md 已追加条目
- [ ] README/文档随行为变化同步
- [ ] 无版权正文、无大文件入库；quote 单条 ≤30 字
- [ ] 架构上不可逆的决策已写 ADR（见 docs/adr/）

## 七、架构决策记录（ADR）

任何"以后改起来很贵"的决定（换存储、改产物 schema、加模型依赖、改证据格式）必须先写 ADR：
上下文 → 决策 → 后果（含放弃的方案）。模板与索引见 [docs/adr/README.md](docs/adr/README.md)。

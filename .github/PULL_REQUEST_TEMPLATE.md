<!--
PR 纪律提醒：
- 功能/bugfix 必须走 PR；文档/typo/配置微调可直推 main，无需开 PR。
- 标题请使用 Conventional Commits：feat/fix/docs/refactor/test/chore/ci(scope): 简述
-->

Closes #

## 变更类型

- [ ] feat（新功能）
- [ ] fix（缺陷修复）
- [ ] refactor（重构，无行为变化）
- [ ] test（仅测试）
- [ ] docs（仅文档）
- [ ] chore/ci（工程配置）

## 做了什么 / 为什么

<!-- 重点写"为什么"；关联的 ADR 如有请附上 -->

## 测试方式

<!-- 新增/修改了哪些测试；手动验证命令与结果 -->

## DoD 自查

- [ ] 验收标准逐条满足（关联 Issue）
- [ ] `pytest` 本地全绿
- [ ] 新增/修改函数有函数级中文注释
- [ ] 未触碰红线：切分无 LLM；证据 quote 可定位；takeaways 无模型填充
- [ ] CHANGELOG.md 已更新
- [ ] README/文档已同步（如行为变化）
- [ ] 无版权正文/大文件；quote 单条 ≤30 字
- [ ] 不可逆决策已写 ADR（如适用）

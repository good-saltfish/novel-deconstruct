"""面板 LLM 供应商配置（#23）：供应商目录 + 进程内会话。

安全约定：
- API key 只保存在面板进程内存中，**永不落盘、永不进 project.json、GET 不回传**；
- 面板仅绑定 127.0.0.1，重启面板后配置清空，需要重新输入；
- 所有供应商都走 OpenAI 兼容 /chat/completions 协议。
"""

from __future__ import annotations


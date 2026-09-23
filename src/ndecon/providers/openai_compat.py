"""OpenAI 兼容 Chat Completions Provider（使用者自带 key，项目不内置任何凭据）。

关键设计：
1. 传输可注入（httpx.BaseTransport），测试用 MockTransport 零网络；
2. 模型只产出 quote 文本，字符偏移由本地 locate_quote 锚定——模型永远不提供偏移；
3. quote 无法唯一定位的情节点被丢弃并计入诊断，绝不伪造证据；
4. 受控词表由 pydantic 枚举强制，非法响应整体判失败（ProviderSchemaError），不写脏数据；
5. 学习层 takeaways 在本层根本不读取模型返回，物理上无法被代笔。
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field

import httpx
from pydantic import ValidationError

from ndecon.domain.ids import chapter_id
from ndecon.domain.models import (
    ChapterSummary,
    GoldenChapterReport,
    PlotPoint,
    SourceRef,
    StructureBeat,
)
from ndecon.providers import prompts
from ndecon.providers.errors import (
    ProviderConfigError,
    ProviderHTTPError,
    ProviderResponseError,
    ProviderSchemaError,
)
from ndecon.providers.locate import locate_quote
from ndecon.providers.retry import call_with_retry

# 单章送入模型的字符上限（约对应 8k token 预算的中文输入）；超出截断并记入诊断
_MAX_CHAPTER_CHARS = 16000
_RETRYABLE_STATUS = {429, 500, 502, 503, 504}
_JSON_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


@dataclass
class ProviderDiagnostics:
    """一次 analyze 运行中需要向使用者披露的非致命问题计数。"""

    http_retries: int = 0
    unresolved_quotes: int = 0
    dropped_plot_points: int = 0
    truncated_chapters: list[str] = field(default_factory=list)
    empty_optional_quotes: int = 0


class OpenAICompatProvider:
    """通过 OpenAI /chat/completions 兼容接口产出拆书语义结果。"""

    name = "openai-compat"
    prompt_version = prompts.PROMPT_VERSION

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        timeout: float = 60.0,
        max_attempts: int = 4,
        transport: httpx.BaseTransport | None = None,
        sleeper=time.sleep,
    ) -> None:
        """从显式参数或环境变量读取配置；缺少 key 时立即报配置错误。"""
        self.api_key = (
            api_key
            or os.getenv("NOVEL_DECON_API_KEY")
            or os.getenv("OPENAI_API_KEY")
            or ""
        )
        self.base_url = (
            base_url
            or os.getenv("NOVEL_DECON_BASE_URL")
            or os.getenv("OPENAI_BASE_URL")
            or "https://api.openai.com/v1"
        ).rstrip("/")
        self.model = model or os.getenv("NOVEL_DECON_MODEL", "gpt-4o-mini")
        if not self.api_key:
            raise ProviderConfigError(
                "缺少 API key：请设置环境变量 NOVEL_DECON_API_KEY 或 OPENAI_API_KEY"
            )
        self.max_attempts = max_attempts
        self._sleeper = sleeper
        self._client = httpx.Client(
            timeout=timeout,
            transport=transport,
            headers={"Authorization": f"Bearer {self.api_key}"},
        )
        self.diagnostics = ProviderDiagnostics()

    # ---------- 传输层 ----------

    def _post_chat(self, messages: list[dict]) -> str:
        """发起一次 chat/completions 请求并返回文本内容；按状态码分类错误。"""

        def _request() -> str:
            response = self._client.post(
                f"{self.base_url}/chat/completions",
                json={
                    "model": self.model,
                    "messages": messages,
                    "temperature": 0.2,
                    "response_format": {"type": "json_object"},
                },
            )
            if response.status_code >= 400:
                retryable = response.status_code in _RETRYABLE_STATUS
                if retryable:
                    self.diagnostics.http_retries += 1
                raise ProviderHTTPError(
                    f"上游返回 HTTP {response.status_code}: {response.text[:200]}",
                    status_code=response.status_code,
                    retryable=retryable,
                )
            return self._extract_content(response)

        return call_with_retry(
            _request, max_attempts=self.max_attempts, sleeper=self._sleeper
        )

    @staticmethod
    def _extract_content(response: httpx.Response) -> str:
        """从标准响应体取出首条消息文本；结构异常按响应错误处理。"""
        try:
            payload = response.json()
            content = payload["choices"][0]["message"]["content"]
        except (KeyError, IndexError, json.JSONDecodeError) as exc:
            raise ProviderResponseError(f"响应结构不符合 chat/completions 约定：{exc}") from exc
        if not isinstance(content, str) or not content.strip():
            raise ProviderResponseError("上游返回了空内容")
        return content

    @staticmethod
    def _parse_json(content: str) -> dict:
        """把模型文本解析为 JSON；容忍 markdown 代码围栏，仍失败则报响应错误。"""
        cleaned = _JSON_FENCE.sub("", content.strip()).strip()
        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError as exc:
            raise ProviderResponseError(f"模型未返回合法 JSON：{exc}") from exc
        if not isinstance(data, dict):
            raise ProviderResponseError("模型返回的 JSON 顶层不是对象")
        return data

    def _chat_json(self, messages: list[dict]) -> dict:
        """请求并解析为 JSON 对象。"""
        return self._parse_json(self._post_chat(messages))

    def close(self) -> None:
        """关闭底层 HTTP 连接池。"""
        self._client.close()

    # ---------- 证据锚定 ----------

    def _anchor(
        self, order: int, quote: str, chapter_text: str, chapter_hash: str
    ) -> SourceRef | None:
        """把模型 quote 定位回原文；失败返回 None（调用方计数并丢弃该点）。"""
        span = locate_quote(chapter_text, quote)
        if span is None:
            return None
        start, end = span
        return SourceRef(
            chapter_id=chapter_id(order),
            start=start,
            end=end,
            quote=chapter_text[start:end],
            content_hash=chapter_hash,
        )

    # ---------- Provider 协议 ----------

    def thin_summary(self, first_chapter_text: str, book_title: str) -> str:
        """请求 100-200 字 thin 概要并校验非空。"""
        data = self._chat_json(
            [
                {"role": "system", "content": prompts.SYSTEM_MESSAGE},
                {
                    "role": "user",
                    "content": prompts.thin_summary_user_message(book_title, first_chapter_text),
                },
            ]
        )
        summary = str(data.get("thin_summary", "")).strip()
        if not summary:
            raise ProviderSchemaError("thin_summary 缺失或为空")
        return summary

    def summarize_chapter(
        self, order: int, title: str, chapter_text: str, chapter_hash: str
    ) -> ChapterSummary:
        """请求单章摘要，逐情节点锚定证据；无任何有效情节点时判 schema 失败。"""
        body = chapter_text
        if len(body) > _MAX_CHAPTER_CHARS:
            self.diagnostics.truncated_chapters.append(chapter_id(order))
            body = body[:_MAX_CHAPTER_CHARS]
        data = self._chat_json(
            [
                {"role": "system", "content": prompts.SYSTEM_MESSAGE},
                {"role": "user", "content": prompts.chapter_user_message(order, title, body)},
            ]
        )

        raw_points = data.get("plot_points")
        if not isinstance(raw_points, list) or not raw_points:
            raise ProviderSchemaError(f"{chapter_id(order)}：plot_points 缺失或为空")
        if not str(data.get("gist", "")).strip():
            raise ProviderSchemaError(f"{chapter_id(order)}：gist 缺失")

        points: list[PlotPoint] = []
        for raw in raw_points[:40]:
            quote_text = str(raw.get("quote", "")).strip()
            source = (
                self._anchor(order, quote_text, chapter_text, chapter_hash)
                if quote_text
                else None
            )
            if source is None:
                self.diagnostics.unresolved_quotes += 1
                self.diagnostics.dropped_plot_points += 1
                continue
            try:
                points.append(
                    PlotPoint(
                        index=len(points) + 1,
                        summary=str(raw["summary"]),
                        tone=raw["tone"],
                        theme_tags=raw["theme_tags"],
                        characters=[str(c) for c in raw.get("characters", [])],
                        source=source,
                    )
                )
            except (ValidationError, KeyError, TypeError) as exc:
                raise ProviderSchemaError(f"{chapter_id(order)}：情节点字段不合法：{exc}") from exc
        if not points:
            raise ProviderSchemaError(
                f"{chapter_id(order)}：全部情节点证据无法定位回原文，拒绝写入无证据数据"
            )

        return ChapterSummary(
            model_id=f"{self.name}:{self.model}",
            prompt_version=self.prompt_version,
            chapter_id=chapter_id(order),
            order=order,
            title=title,
            gist=str(data["gist"]).strip(),
            plot_points=points,
            characters=[str(c) for c in data.get("characters", [])],
        )

    def golden_report(
        self, order: int, title: str, chapter_text: str, chapter_hash: str
    ) -> GoldenChapterReport:
        """请求黄金三章报告；可选 quote 定位失败时置空并计数，不丢弃整份报告。"""
        data = self._chat_json(
            [
                {"role": "system", "content": prompts.SYSTEM_MESSAGE},
                {
                    "role": "user",
                    "content": prompts.golden_user_message(order, title, chapter_text),
                },
            ]
        )
        opening_ref = self._anchor(
            order, str(data.get("opening_quote", "")), chapter_text, chapter_hash
        )
        cliff_ref = self._anchor(
            order, str(data.get("cliffhanger_quote", "")), chapter_text, chapter_hash
        )
        if opening_ref is None and str(data.get("opening_quote", "")).strip():
            self.diagnostics.empty_optional_quotes += 1
        if cliff_ref is None and str(data.get("cliffhanger_quote", "")).strip():
            self.diagnostics.empty_optional_quotes += 1

        try:
            beats = [
                StructureBeat(name=str(b["name"]), note=str(b["note"]))
                for b in data.get("structure_beats", [])
            ]
            report = GoldenChapterReport(
                model_id=f"{self.name}:{self.model}",
                prompt_version=self.prompt_version,
                chapter_id=chapter_id(order),
                order=order,
                title=title,
                opening_hook_quote=opening_ref.quote if opening_ref else "",
                opening_hook_note=str(data["opening_hook_note"]),
                worldview_revealed=[str(w) for w in data.get("worldview_revealed", [])],
                structure_beats=beats,
                cliffhanger_quote=cliff_ref.quote if cliff_ref else "",
                takeaways=[],  # 物理红线：不读取模型是否返回该字段，强制空
            )
        except (ValidationError, KeyError, TypeError) as exc:
            raise ProviderSchemaError(f"{chapter_id(order)}：黄金三章报告字段不合法：{exc}") from exc
        return report

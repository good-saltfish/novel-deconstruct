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
    ProviderTransportError,
)
from ndecon.providers.locate import locate_quote
from ndecon.providers.retry import call_with_retry

# 单章送入模型的字符上限（约对应 8k token 预算的中文输入）；超出截断并记入诊断
_MAX_CHAPTER_CHARS = 16000
_RETRYABLE_STATUS = {429, 500, 502, 503, 504}
def _json_object_prefix(text: str, start: int) -> str | None:
    """从 start 处的 `{` 开始，返回第一个括号配平的完整 JSON 对象。

    用来对付「对象写完了又续一句解释」的 `Extra data`。用括号计数 + 字符串
    状态机，不用正则（正则数不清嵌套）。找不到配平就返回 None——那说明
    对象本身是截断的，不是多了尾巴，不能假装成功。
    """
    if start < 0 or start >= len(text) or text[start] != "{":
        return None
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    return None


_JSON_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)

# JSON 字符串内部的裸引号：前一个字符不是反斜杠的 `"`，且它不在合法边界上。
_STRING_QUOTE = '"'


def _repair_unescaped_quotes(text: str) -> str:
    """把 JSON 字符串值里未转义的裸引号转义掉，返回修复后的文本。

    只在整体解析失败后才调用，所以这里可以假设「文本本来就不是合法 JSON」。
    做法是逐字符走一遍状态机：遇到 `"` 且当前处于字符串内时，判断它是
    真正的结束引号还是正文里的引号——正文里的引号后面紧跟着的字符通常不是
    `:`、`,`、`}`、`]` 这些结构符号，也不会有 `\\n` 之外的结构。

    判据要保守：宁可修不好（返回原文，让上层报错），也不要把合法 JSON 改坏。
    真正的结束引号后面一定跟着 `:`（对象键）、`,`、`}`、`]`、或空白后再跟这些。
    """
    out: list[str] = []
    in_string = False
    escaped = False
    index = 0
    length = len(text)
    changed = False
    while index < length:
        char = text[index]
        if not in_string:
            if char == _STRING_QUOTE:
                in_string = True
            out.append(char)
            index += 1
            continue
        if escaped:
            escaped = False
            out.append(char)
            index += 1
            continue
        if char == "\\":
            escaped = True
            out.append(char)
            index += 1
            continue
        if char != _STRING_QUOTE:
            out.append(char)
            index += 1
            continue

        # 处于字符串内，遇到一个引号：看后面第一个非空白字符，判断是不是结构符号。
        lookahead = index + 1
        while lookahead < length and text[lookahead] in " \t\r\n":
            lookahead += 1
        following = text[lookahead] if lookahead < length else ""
        if following in (":", ",", "}", "]", ""):
            in_string = False
            out.append(char)
        else:
            # 正文里的引号：转义它，字符串继续。
            changed = True
            out.append("\\")
            out.append(char)
        index += 1
    if not changed:
        return text
    return "".join(out)

# 超时是**兜底**，不是判断模型好坏的标准。
#
# 用户的原话：「可以让用户手动暂停 遇到模型出错报错 而不是给自己一个超时时间
# 没必要」。他的意思是：慢不该由引擎单方面判死刑——一章要写多久取决于模型、
# 目标字数、以及那台机器当时的状态，引擎猜不准；用户看着进度条，该停的时候
# 自己点暂停。
#
# 但 `httpx` 不设超时就会永久挂住（连接断了都不返回），所以必须留一个
# 远大于正常耗时的兜底：实测 deepseek-flash 写一章 2600 字 25 秒、最慢 81 秒，
# glm-5.3-flash 只回 1 个 token 也要 9 秒。900 秒是「最慢实测的 11 倍」，
# 正常情况永远碰不到；碰到它说明端点真的卡死了（不是模型慢），此时报错才是
# 对的。想改就设 NOVEL_DECON_TIMEOUT，或在面板的模型设置里填。
_DEFAULT_TIMEOUT_SECONDS = 900.0


def _describe_http_error(response: httpx.Response) -> str:
    """把上游错误体翻译成一句能照着做的话。

    上游（包括本插件的模型桥）返回的是 OpenAI 风格的
    `{"error": {"message": "...", "code": "..."}}`。直接把 response.text 塞进
    错误里，用户看到的是 `上游返回 HTTP 502: {"error":{"message":"模型没返回
    内容（...）","code":"model-failed"}}` —— 转义引号糊成一片，真正有用的那句
    话被埋在最里面。这里把 message 抠出来放在最前面，原始 JSON 只留一小段兜底。
    """
    fallback = f"上游返回 HTTP {response.status_code}: {response.text[:200]}"
    try:
        payload = response.json()
    except ValueError:
        return fallback
    if not isinstance(payload, dict):
        return fallback
    error = payload.get("error")
    message = ""
    code = ""
    if isinstance(error, dict):
        message = str(error.get("message") or "").strip()
        code = str(error.get("code") or "").strip()
    elif isinstance(error, str):
        message = error.strip()
    if not message:
        message = str(payload.get("detail") or payload.get("message") or "").strip()
    if not message:
        return fallback
    prefix = f"上游返回 HTTP {response.status_code}"
    if code:
        prefix = f"{prefix}（{code}）"
    return f"{prefix}：{message}"


def _resolve_timeout(timeout: float | None) -> float:
    """解析实际超时秒数：显式参数优先，其次 NOVEL_DECON_TIMEOUT，最后默认 900s。

    这是兜底值而不是性能判据：正常耗时远小于它，撞上它说明端点卡死。
    留一个环境变量出口，是为了让确实需要更长的场景（超大章节、慢机器）
    不必改代码；非法值静默回退默认值而不是启动失败——写书的人不该因为
    一个拼错的环境变量连面板都打不开。
    """
    if timeout is not None and timeout > 0:
        return float(timeout)
    raw = os.getenv("NOVEL_DECON_TIMEOUT", "").strip()
    if raw:
        try:
            value = float(raw)
        except ValueError:
            return _DEFAULT_TIMEOUT_SECONDS
        if value > 0:
            return value
    return _DEFAULT_TIMEOUT_SECONDS


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
        timeout: float | None = None,
        max_attempts: int = 4,
        max_tokens: int = 0,
        transport: httpx.BaseTransport | None = None,
        sleeper=time.sleep,
    ) -> None:
        """从显式参数或环境变量读取配置；缺少 key 时立即报配置错误。

        ``max_tokens`` 为 0 表示不指定（沿用上游默认上限）。指定它是为了
        **让模型知道预算边界**：不指定时宿主按模型默认上限放行（实测有的
        中转站给到 128k），模型看不到任何「你只有这么多空间」的提示，于是
        写完 1300 字就自己收尾——而闸门按 2600±15% 判，必被打回重写。
        写正文时由 chapter writer 传入一个略高于目标字数的值。
        """
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
        self.max_tokens = max(0, int(max_tokens))
        self._sleeper = sleeper
        # 超时值要留成属性：报错文案里必须写出实际秒数与怎么改，
        # 只写「timed out」用户无从判断是模型慢还是引擎挂了。
        self._timeout_seconds = _resolve_timeout(timeout)
        # trust_env=False 是必须的，不是洁癖：Windows 上 httpx 默认会读系统代理/
        # 证书设置，某些中转站（实测 api.site）在 TLS 握手中被系统证书链拦成
        # `ConnectError: EOF occurred in violation of protocol`，而 urllib 直连同一
        # 地址是通的——用户看到的是「只有 deepseek-account 能用，其它模型都不行」。
        # 我们要连的是用户自己填的端点，不该被本机代理环境改写。
        self._client = httpx.Client(
            timeout=self._timeout_seconds,
            transport=transport,
            headers={"Authorization": f"Bearer {self.api_key}"},
            trust_env=False,
        )
        self.diagnostics = ProviderDiagnostics()

    # ---------- 传输层 ----------

    def _post_chat(self, messages: list[dict], *, max_tokens: int | None = None) -> str:
        """发起一次 chat/completions 请求并返回文本内容；按状态码分类错误。

        传输层异常（读超时、连接重置、网络不可达）必须在这里收成
        `ProviderTransportError`。裸抛 httpx 异常会一路逃出 HTTP 请求处理器
        （面板只 catch `ProviderError`），连接被断开且写不出任何响应，
        插件侧只能看到 `fetch failed`，于是界面上报的是「无法连接引擎」——
        把「模型慢/超时」误报成「引擎挂了」，排查方向会被带偏很远。

        ``max_tokens`` 逐次覆盖实例默认值。写正文必须显式给足：不指定时
        上游按自己的默认上限截断（实测有中转站默认只给 2048），一章 2600 字
        写到一半被砍，闸门判「生成被截断」，重写两次还是砍在同一处。
        注意它**不会**让模型写得更长——模型看不到这个字段，它只决定在哪儿
        被砍；字数服从度是 prompt 的事（见 `chapter_user_message`）。
        """
        budget = self.max_tokens if max_tokens is None else max(0, int(max_tokens))

        def _request() -> str:
            body: dict = {
                "model": self.model,
                "messages": messages,
                "temperature": 0.2,
                "response_format": {"type": "json_object"},
            }
            if budget > 0:
                body["max_tokens"] = budget
            try:
                response = self._client.post(f"{self.base_url}/chat/completions", json=body)
            except httpx.TimeoutException as exc:
                # 走到这里说明连兜底超时都撞上了（默认 900 秒），不是「模型慢」，
                # 是端点真的卡死或网络断了。这种情况重试也没意义：
                # 实测用户在第 3 次重试期间就来问「为什么写了 0 章」——他等的
                # 不是结果，是「还在转圈」的假象。所以 retryable=False，立刻报。
                #
                # 文案给面板里做得到的事，不提 NOVEL_DECON_TIMEOUT（那是引擎
                # 子进程的环境变量，用户在右栏里改不到，写上去只会让他更困惑）。
                raise ProviderTransportError(
                    f"模型响应超时（{self.model}，超过 {self._timeout_seconds:g} 秒）。"
                    "这个时长已经远超正常耗时，多半是端点卡住或网络断了；"
                    "点一次「重启引擎」再试，或换一个模型",
                    retryable=False,
                ) from exc
            except httpx.HTTPError as exc:
                raise ProviderTransportError(
                    f"连不上模型端点（{self.base_url}）：{type(exc).__name__}: {exc}"
                ) from exc
            if response.status_code >= 400:
                retryable = response.status_code in _RETRYABLE_STATUS
                if retryable:
                    self.diagnostics.http_retries += 1
                raise ProviderHTTPError(
                    _describe_http_error(response),
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
        """把模型文本解析为 JSON；容忍围栏、裸引号、以及对象后面多出的尾巴。

        真实故障（2026-11-04，glm-5.3-flash 三轮实测三种坏法）：
        1. 正文里写了 `那条"绿灯路权"。` —— 字符串值内部裸引号 →
           `Expecting ',' delimiter`；
        2. 对象写完后模型又续了一句解释 → `Extra data: line 1 column 2235`。
        这两种都是「内容已经写对了，只是包裹坏了」，整章 35 秒白扔太亏。
        修复只针对已确定坏掉的文本：合法 JSON 一次都不会进修复分支。
        """
        cleaned = _JSON_FENCE.sub("", content.strip()).strip()
        candidates = [cleaned]
        # Extra data：第一个完整对象之后的尾巴，直接截掉再试一次。
        first_brace = cleaned.find("{")
        if first_brace >= 0:
            truncated = _json_object_prefix(cleaned, first_brace)
            if truncated is not None and truncated != cleaned:
                candidates.append(truncated)
        for text in list(candidates):
            repaired = _repair_unescaped_quotes(text)
            if repaired != text:
                candidates.append(repaired)
                truncated_repaired = _json_object_prefix(repaired, repaired.find("{"))
                if truncated_repaired is not None and truncated_repaired not in candidates:
                    candidates.append(truncated_repaired)
        first_error: json.JSONDecodeError | None = None
        for text in candidates:
            try:
                data = json.loads(text)
            except json.JSONDecodeError as exc:
                if first_error is None:
                    first_error = exc
                continue
            if not isinstance(data, dict):
                raise ProviderResponseError("模型返回的 JSON 顶层不是对象")
            return data
        raise ProviderResponseError(f"模型未返回合法 JSON：{first_error}") from first_error

    def _chat_json(self, messages: list[dict], *, max_tokens: int | None = None) -> dict:
        """请求并解析为 JSON 对象；``max_tokens`` 语义同 ``_post_chat``。"""
        return self._parse_json(self._post_chat(messages, max_tokens=max_tokens))

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

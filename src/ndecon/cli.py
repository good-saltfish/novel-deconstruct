"""ndecon 命令行入口（v0.1：split / analyze / version / doctor）。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import typer

from ndecon import __version__
from ndecon.pipeline.runner import run_analyze, run_split
from ndecon.pipeline.stage0 import build_entries
from ndecon.providers.errors import ProviderError
from ndecon.providers.fake import FakeProvider
from ndecon.providers.openai_compat import OpenAICompatProvider
from ndecon.render.markdown import write_bundle, write_outline
from ndecon.workspace.models import CreationProject
from ndecon.workspace.store import WorkspaceStore

app = typer.Typer(help="本地优先的中文网文拆书 CLI", no_args_is_help=True)


def _read_text(path: Path) -> str:
    """读取 UTF-8 文本；文件不存在时给出可读错误。"""
    if not path.is_file():
        raise typer.BadParameter(f"找不到输入文件：{path}")
    return path.read_text(encoding="utf-8")


@app.command()
def version() -> None:
    """打印当前版本号。"""
    typer.echo(__version__)


@app.command()
def doctor() -> None:
    """检查运行环境（Python 与关键依赖）。"""
    typer.echo(f"python: {sys.version.split()[0]}")
    try:
        import pydantic

        typer.echo(f"pydantic: {pydantic.VERSION}")
    except ImportError:
        typer.echo("pydantic: MISSING")


@app.command()
def split(
    input_file: Path = typer.Argument(..., help="小说文本文件（UTF-8 .txt/.md）"),
    out: Path = typer.Option(Path(".out/book"), "--out", help="产物输出目录"),
    book_title: str = typer.Option("", "--title", help="书名；默认取文件名"),
) -> None:
    """仅做章节切分与索引（纯本地，不需要模型）。"""
    text = _read_text(input_file)
    title = book_title or input_file.stem
    result = run_split(text)
    entries = build_entries(result)
    write_outline(out, title, entries, list(result.warnings))
    typer.secho(f"切分完成：{result.chapter_count} 章 -> {out}", fg=typer.colors.GREEN)
    for warning in result.warnings:
        typer.secho(f"告警：{warning}", fg=typer.colors.YELLOW)


@app.command()
def analyze(
    input_file: Path = typer.Argument(..., help="小说文本文件（UTF-8 .txt/.md）"),
    out: Path = typer.Option(Path(".out/book"), "--out", help="产物输出目录"),
    book_title: str = typer.Option("", "--title", help="书名；默认取文件名"),
    provider: str = typer.Option("fake", "--provider", help="fake（离线）或 openai-compat（自带 key）"),
    model: str = typer.Option("", "--model", help="openai-compat 使用的模型名；默认读 NOVEL_DECON_MODEL"),
) -> None:
    """运行完整拆书管道（fake 离线可跑；openai-compat 需自备 API key）。"""
    if provider == "fake":
        active_provider = FakeProvider()
    elif provider == "openai-compat":
        try:
            active_provider = OpenAICompatProvider(model=model or None)
        except ProviderError as exc:
            raise typer.BadParameter(str(exc)) from exc
    else:
        raise typer.BadParameter(f"未知 provider：{provider}（可选 fake / openai-compat）")

    text = _read_text(input_file)
    title = book_title or input_file.stem
    try:
        bundle = run_analyze(text, title, active_provider)
    except ProviderError as exc:
        typer.secho(f"拆解失败：{exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc
    write_bundle(out, bundle)
    typer.secho(
        f"拆解完成：{len(bundle.entries)} 章 / 摘要 {len(bundle.summaries)} 份 / "
        f"黄金三章报告 {len(bundle.golden_reports)} 份 -> {out}",
        fg=typer.colors.GREEN,
    )
    for warning in bundle.warnings:
        typer.secho(f"告警：{warning}", fg=typer.colors.YELLOW)
    if provider == "openai-compat":
        diag = active_provider.diagnostics  # type: ignore[attr-defined]
        if (
            diag.http_retries
            or diag.dropped_plot_points
            or diag.truncated_chapters
            or diag.empty_optional_quotes
        ):
            typer.secho(
                f"Provider 诊断：HTTP 重试 {diag.http_retries} 次；"
                f"丢弃无证据情节点 {diag.dropped_plot_points} 个；"
                f"截断章节 {diag.truncated_chapters or '无'}；"
                f"未锚定可选 quote {diag.empty_optional_quotes} 处",
                fg=typer.colors.YELLOW,
            )
        active_provider.close()  # type: ignore[attr-defined]


@app.command()
def panel(
    workspace: Path = typer.Option(
        Path(".ndecon-workspace"), "--workspace", help="项目工作区目录（项目 JSON 存放处）"
    ),
    host: str = typer.Option("127.0.0.1", "--host", help="监听地址；默认仅本机回环，勿改成 0.0.0.0"),
    port: int = typer.Option(8765, "--port", help="监听端口；0 表示由系统分配"),
    open_browser: bool = typer.Option(False, "--open", help="启动后自动打开浏览器"),
) -> None:
    """启动本地项目面板（拆书入库 + 长篇结构化创作工作台）。"""
    from ndecon.panel.server import run_server

    if host != "127.0.0.1":
        typer.secho("安全提示：面板设计为本机使用，强烈建议保持 --host 127.0.0.1", fg=typer.colors.YELLOW)
    server = run_server(workspace, host=host, port=port)
    actual_host, actual_port = server.server_address
    url = f"http://{actual_host}:{actual_port}"
    typer.secho(f"ndecon 面板已启动：{url}（Ctrl+C 停止）", fg=typer.colors.GREEN)
    if open_browser:
        import webbrowser

        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        typer.echo("\n面板已停止")
    finally:
        server.server_close()


@app.command()
def reindex(
    project_id: str = typer.Argument(..., help="创作项目 ID"),
    workspace: Path = typer.Option(
        Path(".ndecon-workspace"), "--workspace", help="项目工作区目录（项目 JSON 存放处）"
    ),
) -> None:
    """为创作项目重建 L1 一致性检索索引（中文 bigram BM25，纯本地离线，#17）。"""
    from ndecon.retrieval import build_project_index

    store = WorkspaceStore(workspace)
    try:
        project = store.get(project_id)
    except KeyError as exc:
        raise typer.BadParameter(str(exc)) from exc
    if not isinstance(project, CreationProject):
        raise typer.BadParameter("reindex 仅支持创作项目；参考书项目不建立 L1 索引")
    # 已落盘章节摘要一并注入索引，使 reindex 后的 index.json 真正覆盖本书已写内容
    # （t3 缺陷 #3/#2 修复）。CLI 保持"显式按需重建"语义，不引入常驻索引服务。
    summaries = list(project.summaries.values())
    index = build_project_index(project, summaries or None)
    store.write_project_artifact(project_id, "index.json", index.to_dict())
    typer.secho(
        f"索引完成：{len(index.documents)} 个文档 -> projects/{project_id}/index.json",
        fg=typer.colors.GREEN,
    )


@app.command()
def kb(
    action: str = typer.Argument(..., help="操作：index（建库）或 search（查询）"),
    query: str = typer.Argument("", help="search 操作的查询文本"),
    roots: list[Path] = typer.Option(
        [], "--root", help="资料库根目录，可重复；index 时缺省读 NOVEL_DECON_KB_ROOTS（分号分隔）"
    ),
    workspace: Path = typer.Option(
        Path(".ndecon-workspace"), "--workspace", help="项目工作区目录（kb/ 产物存放处）"
    ),
    top_k: int = typer.Option(5, "--top-k", help="search 返回条数"),
) -> None:
    """管理学习型写作知识库（L0.5，ADR-0005）：扫描资料库建库或检索方法论片段。"""
    import os

    from ndecon.kb.corpus import (
        build_knowledge_index,
        search_knowledge,
        write_knowledge_artifacts,
    )

    if action == "index":
        root_paths = list(roots)
        if not root_paths:
            env_roots = os.environ.get("NOVEL_DECON_KB_ROOTS", "")
            root_paths = [Path(part.strip()) for part in env_roots.split(";") if part.strip()]
        root_paths = [path for path in root_paths if path.is_dir()]
        if not root_paths:
            raise typer.BadParameter(
                "没有可用的资料库目录：用 --root 指定，或设置 NOVEL_DECON_KB_ROOTS（分号分隔）"
            )
        index, manifest = build_knowledge_index(root_paths)
        directory = write_knowledge_artifacts(workspace, root_paths, index, manifest)
        included = sum(1 for item in manifest if item.included)
        excluded = len(manifest) - included
        typer.secho(
            f"知识库已建立：{len(index.documents)} 个知识块 / 收录 {included} 个文件、"
            f"排除 {excluded} 个 -> {directory}",
            fg=typer.colors.GREEN,
        )
        typer.echo("审计清单：kb/manifest.json（含每个文件的收录/排除原因）")
        for item in manifest:
            if not item.included:
                typer.secho(f"  排除：{item.relative_path} —— {item.reason}", fg=typer.colors.YELLOW)
    elif action == "search":
        if not query.strip():
            raise typer.BadParameter("search 需要提供查询文本")
        snippets = search_knowledge(workspace, query, k=top_k)
        if not snippets:
            typer.secho("无命中（或尚未建库：先运行 ndecon kb index --root <目录>）", fg=typer.colors.YELLOW)
            return
        for i, snippet in enumerate(snippets, start=1):
            heading_part = f"《{snippet.heading}》" if snippet.heading else ""
            typer.echo(f"\n{i}. [{snippet.score:.3f}] {snippet.source} {heading_part}")
            typer.echo(f"   {snippet.text[:200]}")
    else:
        raise typer.BadParameter(f"未知操作：{action}（可选 index / search）")


@app.command()
def lint(
    input_file: Path = typer.Argument(..., help="待检查的正文文件（UTF-8 .md/.txt，一章）"),
    strict: bool = typer.Option(False, "--strict", help="有越界项时以非零退出码结束（供 CI/git hook 用）"),
) -> None:
    """只读扫描一章正文的"AI 指纹"密度，报告越界项（**不改写、不产出替代文本**）。

    规则与阈值见 docs/ai-writing-workflow.md 5.3；改什么由作者自己决定。
    """
    from ndecon.quality.fingerprint import CHAPTER_CALIBRATION_CHARS, scan_chapter

    text = _read_text(input_file)
    report = scan_chapter(text)

    typer.echo(f"AI 指纹扫描（{report.version}）：{input_file}")
    typer.echo(f"字数 {report.cjk_chars} / 段落 {report.paragraph_count} / 场景轮换 {report.scene_rotations}")
    if report.cjk_chars > CHAPTER_CALIBRATION_CHARS:
        # 阈值按"一章 2000-3500 字"校准；远超该体量时绝对值不可比，如实告知
        typer.secho(
            f"注意：阈值按单章约 {CHAPTER_CALIBRATION_CHARS} 字校准，本文 {report.cjk_chars} 字，"
            f"绝对次数不可直接对比；请按单章文件扫描，或只看比例不看次数。",
            fg=typer.colors.YELLOW,
        )

    violations = [item for item in report.findings if item.severity == "warn" and not item.passed]
    for item in report.findings:
        marker = "OK  " if item.passed else "越界"
        color = typer.colors.GREEN if item.passed else typer.colors.YELLOW
        if item.severity == "info":
            marker = "提示"
            color = typer.colors.CYAN
        suffix = f"（{item.detail}）" if item.detail else ""
        typer.secho(f"  [{marker}] {item.label} —— 实测 {item.value:g}{suffix}", fg=color)

    if report.passed:
        typer.secho("结论：全部规则未越界。", fg=typer.colors.GREEN)
    else:
        typer.secho(
            f"结论：{len(violations)} 项越界 —— 这只是密度信号，不是'是不是 AI'的判定；"
            f"改不改、怎么改由你决定。",
            fg=typer.colors.YELLOW,
        )
        if strict:
            raise typer.Exit(code=1)


@app.command()
def scaffold(
    out: Path = typer.Option(Path("."), "--out", help="台账输出目录"),
    project_id: str = typer.Option("", "--project", help="可选：用创作项目已有细纲/人设预填"),
    workspace: Path = typer.Option(
        Path(".ndecon-workspace"), "--workspace", help="项目工作区目录（--project 时读取）"
    ),
    force: bool = typer.Option(False, "--force", help="覆盖已存在的台账文件（默认跳过）"),
) -> None:
    """生成三份写作台账模板：人物卡 / 伏笔账本 / 章节蓝图（SOP 第 0 周）。

    **只搭骨架，不代笔**：不调模型、不联网、不产出任何创意内容；
    给了 --project 也只是搬运你已有的细纲与人设字段，没有的就留空。
    """
    from ndecon.creation.ledger import write_scaffold

    project: CreationProject | None = None
    if project_id:
        project = WorkspaceStore(workspace).get(project_id)
        if not isinstance(project, CreationProject):
            raise typer.BadParameter("scaffold 只能用创作项目预填；参考书项目没有细纲与人设")

    written, skipped = write_scaffold(out, project, force=force)
    typer.secho(f"台账已生成：{len(written)} 份 -> {out}", fg=typer.colors.GREEN)
    for path in written:
        typer.echo(f"  新建 {path}")
    for path in skipped:
        typer.secho(f"  跳过 {path}（已存在；加 --force 覆盖）", fg=typer.colors.YELLOW)
    typer.echo("模板只搭骨架，内容由你写；伏笔只登记细纲里显式写出的章尾钩。")


@app.command()
def deconstruct(
    input_file: Path = typer.Argument(..., help="待拆解的小说正文（UTF-8 .md/.txt，含'第N章'标题行）"),
    out: Path = typer.Option(Path(".out/deconstruct"), "--out", help="产物输出目录"),
    book_title: str = typer.Option("", "--title", help="书名；默认取文件名"),
    provider: str = typer.Option("rule", "--provider", help="rule（离线纯测量）或 openai-compat（需 key）"),
    model: str = typer.Option("", "--model", help="openai-compat 的模型名；默认读 NOVEL_DECON_MODEL"),
    max_chapters: int = typer.Option(0, "--max-chapters", help="只拆前 N 章；0 表示全拆"),
    semantic_chapters: int = typer.Option(
        40, "--semantic-chapters", help="语义层最多处理前 N 章（规则层仍量全部切出的章）"
    ),
    to_kb: bool = typer.Option(False, "--to-kb", help="把 KB 版抄进工作区 kb/deconstructed/ 供下次建库收录"),
    workspace: Path = typer.Option(
        Path(".ndecon-workspace"), "--workspace", help="工作区目录（--to-kb 时写入）"
    ),
) -> None:
    """结构化拆书：切成可迁移的零部件（客观测量 + 可选语义层）。

    `rule`（默认）完全离线：只量句长、对话占比、疑似冲突点坐标，**不冒充看懂**。
    `openai-compat` 会用你自己的 key 补语义层：核心事件、冲突三角、章末钩子、全书骨架。
    两种模式都产出两份 markdown：作者版（含复核坐标）与 KB 版（只有数值与手法）。
    """
    from ndecon.deconstruct.pipeline import (
        build_full_deconstruction,
        ingest_into_kb,
        write_deconstruction,
    )
    from ndecon.deconstruct.semantics import DeconstructionError, OpenAICompatDeconstructor

    text = _read_text(input_file)
    title = book_title or input_file.stem

    deconstructor = None
    if provider == "rule":
        pass
    elif provider == "openai-compat":
        try:
            deconstructor = OpenAICompatDeconstructor(OpenAICompatProvider(model=model or None))
        except ProviderError as exc:
            raise typer.BadParameter(str(exc)) from exc
    else:
        raise typer.BadParameter(f"未知 provider：{provider}（可选 rule / openai-compat）")

    try:
        book = build_full_deconstruction(
            text,
            title,
            deconstructor,
            source_path=str(input_file),
            max_chapters=max_chapters or 0,
            semantic_chapters=semantic_chapters,
        )
    except DeconstructionError as exc:
        typer.secho(f"拆解失败：{exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc

    author_path, kb_path = write_deconstruction(out, book)
    label = "规则测量" if deconstructor is None else "规则测量 + 语义层"
    typer.secho(
        f"拆解完成（{label}）：{len(book.chapters)} 章 -> {author_path.name} / {kb_path.name}",
        fg=typer.colors.GREEN,
    )
    typer.echo(f"作者版（含复核坐标）：{author_path}")
    typer.echo(f"知识库版（只有数值与手法）：{kb_path}")
    for warning in book.warnings:
        typer.secho(f"告警：{warning}", fg=typer.colors.YELLOW)

    if to_kb:
        target = ingest_into_kb(workspace, book)
        typer.secho(f"已写入知识库目录：{target}（下次 ndecon kb index 会收录它）", fg=typer.colors.GREEN)

    if deconstructor is None:
        typer.echo(
            "当前是纯规则产物：冲突三角/核心事件等语义位留空是**如实留白**，"
            "加 --provider openai-compat 才会填。"
        )


@app.command()
def plan(
    project_id: str = typer.Argument(..., help="创作项目 ID（面板新建或用 workspace 里的 id）"),
    kind: str = typer.Option("settings", "--kind", help="settings（设定候选）或 outline（细纲候选）"),
    provider: str = typer.Option("fake", "--provider", help="fake（离线确定性）或 openai-compat（需 key）"),
    model: str = typer.Option("", "--model", help="openai-compat 的模型名"),
    workspace: Path = typer.Option(Path(".ndecon-workspace"), "--workspace", help="工作区目录"),
) -> None:
    """生成**设定**或**细纲**候选（两套学习方法不同，见 creation/plans.py）。

    `settings`：收敛式——先定边界与代价，再定人；检索知识库里"规则/代价/势力"的做法。
    `outline`：铺开式——沿时间轴排张力；检索知识库里"节奏/密度/钩子"的做法，
    并把已确认设定当硬约束传入。

    **只给候选，不代笔**：本命令不写回项目，产物打印为 JSON，
    你在面板里确认（accept）后才会成为权威内容。
    """
    from ndecon.creation import plans as creation_plans
    from ndecon.creation.plan_providers import (
        FakeOutlinePlanner,
        FakeSettingPlanner,
        OpenAICompatOutlinePlanner,
        OpenAICompatSettingPlanner,
        PlanError,
    )
    from ndecon.kb.corpus import search_knowledge

    store = WorkspaceStore(workspace)
    try:
        project = store.get(project_id)
    except KeyError as exc:
        raise typer.BadParameter(f"找不到项目：{project_id}") from exc
    if not isinstance(project, CreationProject):
        raise typer.BadParameter("plan 只能用于创作项目；参考书项目没有设定与细纲")

    use_ai = provider in ("ai", "openai-compat")
    if kind == "settings":
        try:
            planner = (
                OpenAICompatSettingPlanner(OpenAICompatProvider(model=model or None))
                if use_ai
                else FakeSettingPlanner()
            )
        except ProviderError as exc:
            raise typer.BadParameter(str(exc)) from exc
        query = creation_plans.SETTING_KB_QUERY
    elif kind == "outline":
        try:
            planner = (
                OpenAICompatOutlinePlanner(OpenAICompatProvider(model=model or None))
                if use_ai
                else FakeOutlinePlanner()
            )
        except ProviderError as exc:
            raise typer.BadParameter(str(exc)) from exc
        query = creation_plans.OUTLINE_KB_QUERY
    else:
        raise typer.BadParameter(f"未知 kind：{kind}（可选 settings / outline）")

    snippets = [snippet.model_dump() for snippet in search_knowledge(store.root, query)]
    try:
        if kind == "settings":
            plan_result = planner.plan_settings(
                project.meta.title, project.genre, project.premise, snippets
            )
        else:
            confirmed = [entry.model_dump() for entry in project.settings]
            finger = None
            if project.draft is not None and project.draft.golden_finger is not None:
                finger = project.draft.golden_finger.model_dump()
            plan_result = planner.plan_outline(
                project.meta.title,
                project.genre,
                project.premise,
                confirmed,
                snippets,
                golden_finger=finger,
            )
    except PlanError as exc:
        typer.secho(f"候选生成失败：{exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(json.dumps(plan_result.model_dump(), ensure_ascii=False, indent=2))
    typer.secho(
        f"\n候选来源 {plan_result.model_id}（{plan_result.prompt_version}）；"
        f"知识库命中 {len(snippets)} 条。这是候选，未在面板确认前不会写进项目。",
        fg=typer.colors.YELLOW,
    )


if __name__ == "__main__":
    app()

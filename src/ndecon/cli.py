"""ndecon 命令行入口（v0.1：split / analyze / version / doctor）。"""

from __future__ import annotations

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
    index = build_project_index(project)
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


if __name__ == "__main__":
    app()

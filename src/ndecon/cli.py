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
        f"拆解完成：{len(bundle.entries)} 章 / 摘要 {len(bundle.summaries)} 份 / 黄金三章报告 {len(bundle.golden_reports)} 份 -> {out}",
        fg=typer.colors.GREEN,
    )
    for warning in bundle.warnings:
        typer.secho(f"告警：{warning}", fg=typer.colors.YELLOW)
    if provider == "openai-compat":
        diag = active_provider.diagnostics  # type: ignore[attr-defined]
        if diag.http_retries or diag.dropped_plot_points or diag.truncated_chapters or diag.empty_optional_quotes:
            typer.secho(
                f"Provider 诊断：HTTP 重试 {diag.http_retries} 次；"
                f"丢弃无证据情节点 {diag.dropped_plot_points} 个；"
                f"截断章节 {diag.truncated_chapters or '无'}；"
                f"未锚定可选 quote {diag.empty_optional_quotes} 处",
                fg=typer.colors.YELLOW,
            )
        active_provider.close()  # type: ignore[attr-defined]


if __name__ == "__main__":
    app()

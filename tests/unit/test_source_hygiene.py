"""源码卫生：仓库里所有 .py 都必须是「无 BOM 的 UTF-8」。

为什么值得一条测试：
0.2.1 / 0.2.2 的插件 `package.json` 被写进了 UTF-8 BOM，宿主 `JSON.parse` 直接抛
`Unexpected token '\\uFEFF'`，插件一行代码都没跑到就显示成「包不可用」。Python 这一侧
BOM 恰好是合法的（PEP 263 明确允许），所以问题不会立刻炸——但它会让 `ast` 里的
首个字符串字面量带上 `\\ufeff`，diff 里看不见，工具链里偶发怪问题。

两边的教训是同一个：**看不见的字节比看得见的 bug 贵**。
"""

from __future__ import annotations

import pathlib

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[2]
_SCAN_DIRS = ("src", "tests")
_BOM = b"\xef\xbb\xbf"


def _python_files() -> list[pathlib.Path]:
    found: list[pathlib.Path] = []
    for name in _SCAN_DIRS:
        base = _ROOT / name
        if not base.is_dir():
            continue
        found.extend(sorted(base.rglob("*.py")))
    return found


def test_no_python_file_starts_with_bom() -> None:
    files = _python_files()
    assert files, "没扫到任何 .py，说明路径算错了"
    offenders = [str(path.relative_to(_ROOT)) for path in files if path.read_bytes()[:3] == _BOM]
    assert offenders == [], f"这些文件带了 UTF-8 BOM，请去掉：{offenders}"


@pytest.mark.parametrize("relative", ["pyproject.toml", "src/ndecon/cli.py"])
def test_key_metadata_files_are_plain_utf8(relative: str) -> None:
    path = _ROOT / relative
    assert path.is_file(), f"{relative} 不存在"
    assert path.read_bytes()[:3] != _BOM, f"{relative} 带了 UTF-8 BOM"

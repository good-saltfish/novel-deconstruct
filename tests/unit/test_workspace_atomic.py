"""并发读写的回归测试（Windows 文件竞争）。

这组测试针对 2026-11-04 的真事故：连写偶发 ``written=0``，台账里那一章的
error 是 ``[WinError 5] 拒绝访问: 'project.json.tmp' -> 'project.json'``，
再挖下去发现**读**也会 ``PermissionError``。

根因三条（缺一条都治不好）：所有写入者共用固定临时名 ``.json.tmp``、
Windows 的 ``os.replace`` 撞上任何打开句柄就失败、``os.replace`` 的瞬间
目标处于删除挂起状态时读会拿到 ACCESS_DENIED。

修法是 ``ndecon.workspace.atomic``：按路径加锁 + 唯一临时名 + 重试。
这里直接压并发，断言「一次都不许失败」——修前这组测试几乎必然失败。
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

from ndecon.workspace.atomic import atomic_write_json, atomic_write_text, path_lock, read_text_with_retry


def _hammer(tmp_path: Path, workers: int, rounds: int) -> list[str]:
    """多线程同时写同一文件并读回，返回所有异常描述（空列表 = 全成功）。"""
    target = tmp_path / "project.json"
    failures: list[str] = []
    # 线程数是 workers + 1（多一个读者），Barrier 必须等同样多的人，
    # 少一个就永远等不齐——这里踩过一次，整个测试挂死。
    start = threading.Barrier(workers + 1)

    def writer(index: int) -> None:
        try:
            start.wait()
            for round_no in range(rounds):
                atomic_write_json(target, {"worker": index, "round": round_no})
        except BaseException as exc:  # noqa: BLE001 - 测试要把任何异常都记下来
            failures.append(f"write[{index}] {type(exc).__name__}: {exc}")

    def reader() -> None:
        try:
            start.wait()
            for _ in range(rounds):
                try:
                    text = read_text_with_retry(target)
                except FileNotFoundError:
                    # 还没写过任何一版：这不是竞争，是「本来就没有」。
                    # store 里的读路径都先 is_file() 判断，所以这里照同样口径跳过。
                    continue
                if text.strip():
                    json.loads(text)
        except BaseException as exc:  # noqa: BLE001
            failures.append(f"read {type(exc).__name__}: {exc}")

    threads = [threading.Thread(target=writer, args=(i,)) for i in range(workers)]
    threads.append(threading.Thread(target=reader))
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    return failures


def test_concurrent_writes_never_fail(tmp_path: Path) -> None:
    """8 个写入者 + 1 个读者同时压同一文件：一次都不许失败。

    修前：固定临时名让两个写入者共用同一个 ``.tmp``，``os.replace`` 报
    ``WinError 5``；读还会撞上删除挂起拿到 ``PermissionError``。
    """
    failures = _hammer(tmp_path, workers=8, rounds=20)
    assert failures == [], failures
    # 文件最终必须是完整合法 JSON，不能是半个
    payload = json.loads((tmp_path / "project.json").read_text(encoding="utf-8"))
    assert payload["worker"] in range(8)


def test_atomic_write_leaves_no_tmp_files(tmp_path: Path) -> None:
    """写完后目录里不许留临时文件（重试用尽也要清掉，别在用户目录堆垃圾）。"""
    target = tmp_path / "x.json"
    for index in range(30):
        atomic_write_json(target, {"i": index})
    leftovers = [p.name for p in tmp_path.iterdir() if p.name.endswith(".tmp")]
    assert leftovers == [], leftovers


def test_read_text_with_retry_uses_same_lock(tmp_path: Path) -> None:
    """读与写共用同一把锁：持有锁时另一个线程拿不到。"""
    target = tmp_path / "y.txt"
    atomic_write_text(target, "hello")
    lock = path_lock(target)
    seen: list[bool] = []

    def probe() -> None:
        # 主线程持锁期间，这个线程必须阻塞在锁上
        acquired = lock.acquire(timeout=0.2)
        seen.append(acquired)
        if acquired:
            lock.release()

    with lock:
        thread = threading.Thread(target=probe)
        thread.start()
        thread.join(timeout=5)
    assert seen == [False], seen


def test_path_lock_is_case_insensitive(tmp_path: Path) -> None:
    """Windows 路径不区分大小写，同一文件必须落到同一把锁上。"""
    lower = tmp_path / "Project.json"
    upper = tmp_path / "PROJECT.json"
    assert path_lock(lower) is path_lock(upper)

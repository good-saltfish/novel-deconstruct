"""原子读写：按路径加锁 + 唯一临时名 + 重试，专治 Windows 上的并发文件竞争。

## 为什么需要这个模块（真事故，2026-11-04）

连写偶发 ``written=0``，台账里那一章的 error 是：

    提交失败并已回滚：[WinError 5] 拒绝访问。:
    '…\\project.json.tmp' -> '…\\project.json'

进一步排查还发现**读**也会失败：

    PermissionError: [Errno 13] Permission denied: '…\\project.json'

三个叠加原因，缺一条都治不好：

1. **所有写入者共用同一个临时文件名**（``path.with_suffix(".json.tmp")``）。
   HTTP 请求线程（保存骨架部件、确认骨架）与后台连写线程同时写同一个
   ``project.json`` 时，两个线程都在写同一个 ``.tmp``：内容互相覆盖（最坏写出
   半个 JSON），先完成的那个把 tmp 移走，另一个的 ``os.replace`` 就找不到源
   文件或撞上仍被占用的目标文件。
2. **Windows 的 ``os.replace`` 不是「总能成功」的**：目标文件只要被任何句柄
   打开（读也算），替换就报 ``WinError 5``/``WinError 32``。杀毒软件、索引器、
   另一个线程的 ``read_text`` 都能造成这个瞬时失败。
3. **读也会被写挡住**：``os.replace`` 让目标短暂处于「删除挂起」状态，此时
   其它线程打开它读会拿到 ``ERROR_ACCESS_DENIED``。

## 三条纪律（别退化回去）

1. **进程内按路径串行**（``path_lock``）。引擎是单进程，所有项目读写都经过
   同一个 ``WorkspaceStore``，所以进程内按路径加锁就能彻底消除竞争——这比
   「失败了重试」更根本：重试只是把概率压低，加锁是把竞争去掉。
2. **临时文件名必须唯一**（pid + 随机后缀）。跨进程（用户自己另开一个
   ``ndecon panel``）时锁管不着，唯一临时名至少保证两个进程不会互相踩掉内容。
3. **读写都允许短暂失败后重试**，兜住外部句柄（杀毒/索引器）。**不重试等于
   把系统抖动直接变成「这一章失败」**，用户看到的是「写了 0 章」。
"""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from pathlib import Path

# Windows 上文件竞争的瞬时失败码：5=拒绝访问，32=文件被另一进程占用。
_RETRYABLE_WINERRORS = {5, 32}
_ATTEMPTS = 6
_BACKOFF_SECONDS = (0.01, 0.02, 0.04, 0.08, 0.15)

_LOCKS: dict[str, threading.RLock] = {}
_LOCKS_GUARD = threading.Lock()


def path_lock(path: Path) -> threading.RLock:
    """取某个文件的进程内锁（同路径共享一把）。

    键用 ``resolve()`` 之后的绝对路径并转小写：Windows 路径不区分大小写，
    ``Project.json`` 与 ``project.json`` 必须落到同一把锁上。
    """
    key = str(path.resolve()).lower()
    with _LOCKS_GUARD:
        lock = _LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _LOCKS[key] = lock
        return lock


def _is_retryable(exc: OSError) -> bool:
    """判断是否是「等一会儿就好」的文件竞争错误。"""
    return getattr(exc, "winerror", None) in _RETRYABLE_WINERRORS


def _retry(operation, *, attempts: int = _ATTEMPTS):
    """执行 operation，竞争类错误退避重试；其它错误立刻抛出。"""
    last: OSError | None = None
    for attempt in range(attempts):
        try:
            return operation()
        except OSError as exc:
            last = exc
            if not _is_retryable(exc):
                raise
            if attempt < len(_BACKOFF_SECONDS):
                time.sleep(_BACKOFF_SECONDS[attempt])
    assert last is not None
    raise last


def _unique_tmp(path: Path) -> Path:
    """同目录下唯一的临时文件名（同盘才能保证 replace 是原子的）。"""
    return path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp")


def atomic_write_text(path: Path, text: str, *, encoding: str = "utf-8") -> None:
    """原子写文本：按路径加锁 → 写唯一临时文件 → 替换（带重试）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = path_lock(path)
    with lock:
        tmp = _unique_tmp(path)
        try:
            tmp.write_text(text, encoding=encoding)
            _retry(lambda: os.replace(tmp, path))
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise


def atomic_write_json(path: Path, payload: object, *, indent: int = 2) -> None:
    """原子写 JSON（``ensure_ascii=False``，中文直接可读）。"""
    atomic_write_text(
        path,
        json.dumps(payload, ensure_ascii=False, indent=indent),
    )


def read_text_with_retry(path: Path, *, encoding: str = "utf-8") -> str:
    """读文本，遇到「删除挂起」造成的瞬时拒绝访问时重试。

    读也要加同一把锁：不锁的话，读线程可能在写线程 ``os.replace`` 的瞬间
    打开目标，拿到 ``ERROR_ACCESS_DENIED``，整章就被判成失败。
    """
    lock = path_lock(path)
    with lock:
        return _retry(lambda: path.read_text(encoding=encoding))

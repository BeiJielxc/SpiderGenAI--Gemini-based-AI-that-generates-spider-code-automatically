"""
任务队列管理器 — 多人在线排队 & 并发控制

功能：
- 基于 asyncio.Queue + asyncio.Semaphore 实现任务排队与并发限制
- 提供队列位置查询 / 全局队列信息 / 预估等待时间
- 仅在 config.yaml 中 server.enable_queue = true 时启用

设计原则：
- 本文件为独立模块，不修改已有 api.py 的核心逻辑
- api.py 通过条件导入集成：队列关闭时完全不加载此模块
"""

import asyncio
import time
from typing import Dict, Any, Optional, Callable, Awaitable, List
from collections import OrderedDict


class TaskQueue:
    """
    任务排队与并发控制管理器

    使用方式（在 api.py 中）：
        queue = TaskQueue(max_concurrency=2)
        await queue.start()
        ...
        await queue.enqueue(task_id, coro_factory)
        ...
        await queue.shutdown()
    """

    def __init__(self, max_concurrency: int = 1):
        self._max_concurrency = max(1, max_concurrency)
        self._semaphore = asyncio.Semaphore(max_concurrency)

        # 等待队列：保持插入顺序  task_id -> QueueItem
        self._waiting: OrderedDict[str, "_QueueItem"] = OrderedDict()

        # 正在运行的任务集
        self._running: Dict[str, asyncio.Task] = {}

        # 已完成计数（用于估算平均耗时）
        self._completed_count: int = 0
        self._total_elapsed: float = 0.0  # 已完成任务的累计用时（秒）

        # 内部 asyncio.Queue 用于 worker 循环
        self._queue: asyncio.Queue = asyncio.Queue()

        # worker task
        self._worker_task: Optional[asyncio.Task] = None

        # 关闭标记
        self._shutdown = False

    # ── 公共方法 ──

    async def start(self):
        """启动后台 worker 协程"""
        if self._worker_task is None or self._worker_task.done():
            self._shutdown = False
            self._worker_task = asyncio.create_task(self._worker_loop())

    async def shutdown(self):
        """优雅关闭"""
        self._shutdown = True
        # 往 queue 里放一个哨兵让 worker 退出
        await self._queue.put(None)
        if self._worker_task and not self._worker_task.done():
            await self._worker_task

    async def enqueue(
        self,
        task_id: str,
        coro_factory: Callable[[], Awaitable[None]],
    ) -> int:
        """
        将任务放入队列

        Args:
            task_id: 任务唯一标识
            coro_factory: 一个 **无参** 的 async 工厂函数，被调用时才创建协程
                          （不能直接传 coroutine，否则排队期间就泄漏了）

        Returns:
            当前队列位置（从 1 开始）
        """
        item = _QueueItem(task_id=task_id, coro_factory=coro_factory, enqueue_time=time.time())
        self._waiting[task_id] = item
        await self._queue.put(item)
        return self.get_position(task_id)

    def get_position(self, task_id: str) -> int:
        """
        获取任务在等待队列中的位置（1=排第一个，0=已经在运行/不在队列中）
        """
        if task_id in self._running:
            return 0
        keys = list(self._waiting.keys())
        if task_id in keys:
            return keys.index(task_id) + 1
        return 0

    def get_queue_info(self, task_id: Optional[str] = None) -> Dict[str, Any]:
        """
        获取队列全局信息（以及可选的指定任务位置）
        """
        avg_time = (self._total_elapsed / self._completed_count) if self._completed_count else 120.0
        position = self.get_position(task_id) if task_id else 0

        return {
            "queueEnabled": True,
            "waitingCount": len(self._waiting),
            "runningCount": len(self._running),
            "maxConcurrency": self._max_concurrency,
            "position": position,
            "estimatedWaitSeconds": int(position * avg_time) if position > 0 else 0,
            "averageTaskSeconds": int(avg_time),
        }

    def cancel(self, task_id: str) -> bool:
        """
        从队列中取消一个等待中的任务

        Returns:
            True = 成功取消（在等待队列中）
            False = 未找到（可能已在运行或不存在）
        """
        if task_id in self._waiting:
            item = self._waiting.pop(task_id)
            item.cancelled = True
            return True
        return False

    def is_task_running(self, task_id: str) -> bool:
        return task_id in self._running

    @property
    def waiting_task_ids(self) -> List[str]:
        return list(self._waiting.keys())

    @property
    def running_task_ids(self) -> List[str]:
        return list(self._running.keys())

    # ── 内部实现 ──

    async def _worker_loop(self):
        """后台 worker：从队列中取任务，受 Semaphore 控制并发"""
        while not self._shutdown:
            item: Optional[_QueueItem] = await self._queue.get()

            if item is None:
                # 哨兵：退出
                break

            if item.cancelled:
                # 在排队期间被取消了
                self._queue.task_done()
                continue

            # 获取信号量（阻塞直到有空位）
            await self._semaphore.acquire()

            # 从等待队列移除、加入运行集
            self._waiting.pop(item.task_id, None)

            # 启动任务
            run_task = asyncio.create_task(self._run_item(item))
            self._running[item.task_id] = run_task
            self._queue.task_done()

    async def _run_item(self, item: "_QueueItem"):
        """运行单个任务并在完成后释放信号量"""
        start_time = time.time()
        try:
            # 调用工厂函数创建协程并 await
            await item.coro_factory()
        except asyncio.CancelledError:
            pass
        except Exception:
            # 任务内部异常应由 _run_generation_task 自己处理
            pass
        finally:
            elapsed = time.time() - start_time
            self._completed_count += 1
            self._total_elapsed += elapsed

            self._running.pop(item.task_id, None)
            self._semaphore.release()


class _QueueItem:
    """队列内部条目"""

    __slots__ = ("task_id", "coro_factory", "enqueue_time", "cancelled")

    def __init__(
        self,
        task_id: str,
        coro_factory: Callable[[], Awaitable[None]],
        enqueue_time: float,
    ):
        self.task_id = task_id
        self.coro_factory = coro_factory
        self.enqueue_time = enqueue_time
        self.cancelled = False

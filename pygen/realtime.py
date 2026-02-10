"""
SSE（Server-Sent Events）实时推送模块

功能：
- 任务级别的事件广播（队列位置变化、日志更新、状态变化）
- 基于 asyncio.Queue 的发布-订阅模式
- 仅在 config.yaml 中 server.enable_sse = true 时启用

设计原则：
- 独立文件，不修改已有业务逻辑
- api.py 通过条件导入集成：SSE 关闭时完全不加载此模块
"""

import asyncio
import json
import time
from typing import Dict, Any, Optional, AsyncGenerator, Set


class EventBroadcaster:
    """
    SSE 事件广播器

    使用方式（在 api.py 中）：
        broadcaster = EventBroadcaster()
        # 推送事件
        await broadcaster.publish(task_id, "queue_position", {"position": 3})
        await broadcaster.publish(task_id, "log", {"message": "[INFO] xxx"})
        await broadcaster.publish(task_id, "status", {"status": "running"})
        # SSE 端点中订阅
        async for event_str in broadcaster.subscribe(task_id):
            yield event_str   # 已经是 "data: ...\n\n" 格式
    """

    def __init__(self):
        # task_id -> set of subscriber queues
        self._subscribers: Dict[str, Set[asyncio.Queue]] = {}

    async def publish(self, task_id: str, event_type: str, data: Dict[str, Any]):
        """
        向指定任务的所有订阅者推送一条事件

        Args:
            task_id: 任务 ID
            event_type: 事件类型（queue_position / log / status / complete）
            data: 事件数据（会被 JSON 序列化）
        """
        if task_id not in self._subscribers:
            return

        payload = json.dumps({
            "type": event_type,
            "taskId": task_id,
            "timestamp": time.time(),
            **data
        }, ensure_ascii=False)

        dead_queues = []
        for q in self._subscribers[task_id]:
            try:
                q.put_nowait(payload)
            except asyncio.QueueFull:
                # 消费太慢，丢弃旧消息
                try:
                    q.get_nowait()
                    q.put_nowait(payload)
                except Exception:
                    dead_queues.append(q)

        # 清理失效的订阅
        for dq in dead_queues:
            self._subscribers[task_id].discard(dq)

    async def subscribe(self, task_id: str, timeout: float = 600) -> AsyncGenerator[str, None]:
        """
        订阅指定任务的事件流（SSE 格式）

        Args:
            task_id: 任务 ID
            timeout: 总超时时间（秒），超过后自动结束流

        Yields:
            SSE 格式的字符串: "event: {type}\ndata: {json}\n\n"
        """
        q: asyncio.Queue = asyncio.Queue(maxsize=100)

        if task_id not in self._subscribers:
            self._subscribers[task_id] = set()
        self._subscribers[task_id].add(q)

        start = time.time()
        try:
            while True:
                remaining = timeout - (time.time() - start)
                if remaining <= 0:
                    break

                try:
                    payload = await asyncio.wait_for(q.get(), timeout=min(30, remaining))
                except asyncio.TimeoutError:
                    # 发送心跳保持连接
                    yield ": heartbeat\n\n"
                    continue

                # 解析 type 以设置 SSE event 字段
                try:
                    parsed = json.loads(payload)
                    event_type = parsed.get("type", "message")
                except Exception:
                    event_type = "message"

                yield f"event: {event_type}\ndata: {payload}\n\n"

                # 如果是终止事件，结束流
                if event_type in ("complete", "failed", "cancelled"):
                    break

        finally:
            # 取消订阅
            if task_id in self._subscribers:
                self._subscribers[task_id].discard(q)
                if not self._subscribers[task_id]:
                    del self._subscribers[task_id]

    def cleanup(self, task_id: str):
        """清理指定任务的所有订阅（任务完成后调用）"""
        self._subscribers.pop(task_id, None)

    @property
    def active_subscriptions(self) -> int:
        """当前活跃的订阅总数"""
        return sum(len(subs) for subs in self._subscribers.values())

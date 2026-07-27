"""In-process pub/sub with a decided backpressure policy.

The rule from the plan: the publisher never blocks. Backpressure has to be
decided, or it decides itself, wrongly (an unbounded queue is a memory leak the
moment one dashboard socket lags while `module.log` spits stack traces).

Two policies per topic:
  * DROP_OLDEST     bounded queue; on overflow drop the oldest, count it.
  * DISCONNECT_SLOW bounded queue; on overflow drop the *subscriber*, not the
                    message. The UI reconnects and reloads state over REST.

Either way `publish()` is synchronous and returns immediately.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

from . import metrics

DROP_OLDEST = "drop_oldest"
DISCONNECT_SLOW = "disconnect_slow"

# topic -> (policy, maxsize). "no drop" topics use DISCONNECT_SLOW so a lagging
# consumer never stalls a producer or loses a message silently.
_TOPIC_POLICY: dict[str, tuple[str, int]] = {
    "module.state_changed": (DISCONNECT_SLOW, 256),
    "module.log": (DROP_OLDEST, 512),
    "tool.called": (DISCONNECT_SLOW, 256),
    "policy.confirmation_required": (DISCONNECT_SLOW, 64),
    "conversation.message": (DROP_OLDEST, 256),
    "schedule.fired": (DISCONNECT_SLOW, 64),
    "budget.exceeded": (DISCONNECT_SLOW, 64),
}
_DEFAULT_POLICY = (DROP_OLDEST, 128)


@dataclass(eq=False)  # identity-based: subscriptions live in a set, keyed by object
class Subscription:
    topic: str
    policy: str
    queue: asyncio.Queue = field(default_factory=asyncio.Queue)
    _closed: bool = False

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            # wake any consumer parked on get()
            try:
                self.queue.put_nowait(_SENTINEL)
            except asyncio.QueueFull:
                pass

    @property
    def closed(self) -> bool:
        return self._closed

    async def events(self) -> AsyncIterator[Any]:
        while not self._closed:
            item = await self.queue.get()
            if item is _SENTINEL:
                break
            yield item


_SENTINEL = object()


class EventBus:
    def __init__(self) -> None:
        self._subs: dict[str, set[Subscription]] = {}

    def subscribe(self, topic: str) -> Subscription:
        policy, maxsize = _TOPIC_POLICY.get(topic, _DEFAULT_POLICY)
        sub = Subscription(topic=topic, policy=policy, queue=asyncio.Queue(maxsize=maxsize))
        self._subs.setdefault(topic, set()).add(sub)
        return sub

    def unsubscribe(self, sub: Subscription) -> None:
        subs = self._subs.get(sub.topic)
        if subs and sub in subs:
            subs.discard(sub)
        sub.close()

    def publish(self, topic: str, payload: Any) -> None:
        """Non-blocking. Applies the topic's backpressure policy on overflow."""
        for sub in list(self._subs.get(topic, ())):
            if sub.closed:
                self._subs.get(topic, set()).discard(sub)
                continue
            try:
                sub.queue.put_nowait(payload)
            except asyncio.QueueFull:
                if sub.policy == DROP_OLDEST:
                    try:
                        sub.queue.get_nowait()
                        sub.queue.put_nowait(payload)
                    except (asyncio.QueueEmpty, asyncio.QueueFull):
                        pass
                    metrics.BUS_DROPPED.labels(topic=topic).inc()
                else:  # DISCONNECT_SLOW
                    self.unsubscribe(sub)
                    metrics.BUS_DROPPED.labels(topic=topic).inc()

    def subscriber_count(self, topic: str) -> int:
        return len(self._subs.get(topic, ()))

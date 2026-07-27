import asyncio

import pytest

from vahub.core import bus as busmod
from vahub.core.bus import EventBus


async def test_delivery_and_snapshot_order():
    b = EventBus()
    sub = b.subscribe("conversation.message")
    b.publish("conversation.message", {"n": 1})
    b.publish("conversation.message", {"n": 2})
    got = []
    async for ev in sub.events():
        got.append(ev)
        if len(got) == 2:
            b.unsubscribe(sub)
    assert got == [{"n": 1}, {"n": 2}]


async def test_publish_never_blocks_on_full_drop_oldest():
    b = EventBus()
    sub = b.subscribe("module.log")  # DROP_OLDEST, maxsize 512
    # overflow the queue; publish must stay non-blocking
    for i in range(600):
        b.publish("module.log", {"i": i})
    # newest survive, oldest dropped
    assert sub.queue.qsize() == 512


async def test_disconnect_slow_drops_subscriber():
    b = EventBus()
    sub = b.subscribe("module.state_changed")  # DISCONNECT_SLOW, maxsize 256
    for i in range(300):  # overflow -> subscriber gets disconnected
        b.publish("module.state_changed", {"i": i})
    assert sub.closed
    assert b.subscriber_count("module.state_changed") == 0


async def test_slow_subscriber_does_not_stall_others():
    b = EventBus()
    slow = b.subscribe("module.state_changed")
    fast = b.subscribe("module.state_changed")
    for i in range(300):
        b.publish("module.state_changed", {"i": i})
        # fast keeps up, so it never overflows; slow never drains
        while not fast.queue.empty():
            fast.queue.get_nowait()
    # slow was disconnected on overflow, fast stayed attached
    assert slow.closed
    assert not fast.closed
    assert b.subscriber_count("module.state_changed") == 1

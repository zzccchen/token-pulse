"""Synthetic, explicitly labeled preview data. Never used by live collection."""

import time
from dataclasses import replace

from token_pulse.collector import Snapshot
from token_pulse.domain import Context, Sample, Task


def demo_snapshot() -> Snapshot:
    now = time.time()
    context = Context(
        model="demo-model",
        effort="high",
        provider="openai",
        auth_mode="Chatgpt",
        submitted_tier="priority",
        submitted_tier_state="set",
    )
    rates = [32, 34, 31, 35, 33, 36, 34, 32, 35, 34, 33, 36, 35, 33, 34, 35, 34, 32, 35, 34]
    samples = tuple(
        Sample(
            "demo-task" if i % 2 else "demo-other",
            "demo-turn",
            f"demo-response-{i}",
            now - (19 - i) * 85 - 6,
            170,
            now - (19 - i) * 85 - 6 - 170 / rate,
            replace(
                context,
                submitted_tier="default" if i % 5 == 1 else None if i % 5 == 2 else "priority",
                submitted_tier_state=None if i % 5 == 2 else "set",
            ),
            source="synthetic-demo",
        )
        for i, rate in enumerate(rates)
    )
    tasks = (
        Task("demo-task", "重构数据采集器", "tool", now - 2, context, samples[-1]),
        Task("demo-other", "整理项目文档", "generating", now - 3, context, samples[-2]),
    )
    return Snapshot(tasks, samples, (), now)

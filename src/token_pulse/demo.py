"""Synthetic, explicitly labeled preview data. Never used by live collection."""

import time
from dataclasses import replace

from token_pulse.collector import Snapshot
from token_pulse.domain import Context, Sample, Task, bind_thread_config


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
            bind_thread_config(
                replace(
                    context,
                    submitted_tier=("priority", "default", None, None, None)[i % 5],
                    submitted_tier_state=("set", "set", None, None, "unspecified")[i % 5],
                    config_tag_tier="ultrafast" if i % 5 == 3 else None,
                    config_tag_state="set" if i % 5 == 3 else None,
                    thread_config_tier="priority" if i % 5 in {2, 4} else None,
                    thread_config_state="set" if i % 5 in {2, 4} else None,
                    thread_config_at=now - 3600 if i % 5 in {2, 4} else None,
                    thread_config_source="synthetic-demo" if i % 5 in {2, 4} else None,
                    thread_config_turn_at=now - 3500 if i % 5 in {2, 4} else None,
                    thread_config_turn_observed_at=now - 3500 if i % 5 in {2, 4} else None,
                    thread_config_relation="before_turn" if i % 5 in {2, 4} else None,
                    turn_override_state="none" if i % 5 == 4 else None,
                    turn_override_at=now - 3550 if i % 5 == 4 else None,
                )
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

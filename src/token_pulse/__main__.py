import argparse
import json
import tempfile
from collections import Counter
from pathlib import Path

from token_pulse import __version__
from token_pulse.collector import Collector, codex_home, data_home
from token_pulse.i18n import LANGUAGES, set_language, tr
from token_pulse.preferences import load


def main() -> int:
    # Resolve language before building help, without reading any Codex data.
    preliminary = argparse.ArgumentParser(add_help=False, allow_abbrev=False)
    preliminary.add_argument("--language")
    preliminary.add_argument("--data-dir", type=Path)
    preliminary.add_argument("--demo", action="store_true")
    preliminary.add_argument("--smoke-test", action="store_true")
    options, _ = preliminary.parse_known_args()
    saved_language = (
        "system"
        if options.demo or options.smoke_test
        else load((options.data_dir or data_home()) / "settings.json").language
    )
    set_language(options.language or saved_language)
    parser = argparse.ArgumentParser(
        description=tr("TokenPulse 词脉 — 本机 Codex 只读托盘监视器"), allow_abbrev=False
    )
    parser.add_argument(
        "--language", choices=LANGUAGES, help=tr("界面语言（覆盖已保存设置，仅本次运行有效）")
    )
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument(
        "--codex-home", type=Path, help=tr("Codex 数据目录，默认 CODEX_HOME 或 ~/.codex")
    )
    parser.add_argument("--data-dir", type=Path, help=tr("词脉自己的数据目录"))
    parser.add_argument("--demo", action="store_true", help=tr("使用隔离的合成演示数据"))
    parser.add_argument("--no-tray", action="store_true", help=tr("仅使用普通窗口"))
    parser.add_argument(
        "--hidden", action="store_true", help=tr("启动时仅显示托盘；无托盘则显示窗口")
    )
    parser.add_argument(
        "--diagnose", action="store_true", help=tr("只读诊断，输出不含任务标题的统计后退出")
    )
    parser.add_argument(
        "--smoke-test", action="store_true", help=tr("仅供验证：演示窗口启动后自动退出")
    )
    args = parser.parse_args()
    if args.diagnose:
        collector = Collector(args.codex_home or codex_home())
        for _ in range(6):
            snapshot = collector.poll()
        print(
            json.dumps(
                {
                    "scope": tr("最后一轮已读样本，不代表完整历史"),
                    "backfill_completed": snapshot.backfill_completed,
                    "backfill_pending": snapshot.backfill_pending,
                    "tasks": len(snapshot.tasks),
                    "samples": len(snapshot.samples),
                    "valid_samples": sum(s.tps is not None for s in snapshot.samples),
                    "tier_evidence": {
                        name: dict(
                            Counter(
                                getattr(s.context, name)
                                for s in snapshot.samples
                                if getattr(s.context, name) is not None
                            )
                        )
                        for name in (
                            "submitted_tier",
                            "submitted_tier_state",
                            "requested_tier",
                            "actual_tier",
                        )
                    },
                    "excluded": dict(Counter(s.exclusion for s in snapshot.samples if s.exclusion)),
                    "timing_sources": dict(
                        Counter(s.timing_source for s in snapshot.samples if s.tps is not None)
                    ),
                    "rejected": dict(Counter(s.issue for s in snapshot.rejected_samples)),
                    "diagnostics": snapshot.diagnostics,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    from token_pulse.app import run

    if args.demo or args.smoke_test:
        with tempfile.TemporaryDirectory(prefix="token-pulse-demo-") as temporary:
            return run(
                Path(temporary),
                demo=True,
                no_tray=args.no_tray,
                hidden=args.hidden,
                smoke_test=args.smoke_test,
                language=args.language,
            )
    return run(
        args.data_dir or data_home(),
        source=args.codex_home,
        no_tray=args.no_tray,
        hidden=args.hidden,
        language=args.language,
    )


if __name__ == "__main__":
    raise SystemExit(main())

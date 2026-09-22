# Contributing to TokenPulse

English and Chinese reports are welcome. Public technical documentation, code comments, and commit messages use English; the app supports both languages.

## Start here

1. Read the [measurement contract](docs/measurement.md) before changing collection or statistics.
2. Follow [development setup](docs/development.md) and try `uv run token-pulse --demo`.
3. Report a minimal reproduction for bugs. Discuss scope in an issue before implementing a larger feature.

Good first contributions include translation corrections, reproducible UI fixes, documentation improvements, and testing on a Linux desktop. The [roadmap](ROADMAP.md) describes priorities, not delivery promises.

## Changes and review

- Keep each PR focused on one problem. Explain the trigger, changed behavior, verification, and limitations.
- Use English Conventional Commit titles such as `fix: preserve measurements when logs rotate`. Add a body when the reason or validation is not obvious.
- Keep identifiers and comments in English. Explain invariants and surprising constraints rather than narrating straightforward code. Avoid repetitive bilingual comments.
- Preserve unrelated working changes. Avoid broad refactors alongside format adaptations or translation work.
- Run relevant [checks](docs/development.md#checks). Pure documentation changes need link and consistency review, not new business tests.
- For UI changes, inspect synthetic screenshots in both languages and themes. Record the actual OS and desktop used for native interaction checks.

The [agent instructions](AGENTS.md) summarize repository invariants and commands. Data correctness and privacy rules apply to human and automated contributions alike.

## Tests and data

Use deterministic, synthetic inputs. Parser changes should cover normal output, missing and conflicting fields, resets, duplicates, retries, interleaved tasks, and incomplete timing. File readers need rotation, truncation, partial-line, and recovery cases. Use precise expected tokens and timing, not personal performance thresholds.

Do not attach raw logs, conversation text, tool arguments, titles, credentials, private paths, or databases. Review diagnostic summaries before sharing. Format reports should contain the smallest invented example that reproduces the structure and failure. Report sensitive vulnerabilities using [SECURITY.md](SECURITY.md).

## Translation

Messages go through `i18n.tr()`. Chinese templates are keys and fallback text; English translations live in `src/token_pulse/locales/en.py`. This is intentional.

Translate the complete template before formatting:

```python
tr("最近输出 {stamp}", stamp=stamp)
```

Keep placeholder names, conversions, and format specifications identical. Do not translate already formatted f-strings, model names, raw evidence, identifiers, or CSV fields. Use stable business values and translate only labels.

Language previews immediately. Save persists it; Cancel or failed saving restores the previous display language, including a command-line override. Refresh existing windows with `retranslate_ui()`, retaining model, range, filters, sorting, and selected records. Do not rebuild the collector or freeze translations at import time.

Run `uv run pytest tests/test_i18n.py` and inspect [screenshots](docs/development.md#screenshots). New languages also need resolution, registration, and a Settings option. Update both READMEs for capability or installation changes; keep technical details in linked English guides.

## Attribution and conduct

Contributions are made under the repository's MIT license. Preserve attribution and document new third-party material and licenses. Be considerate, discuss the work rather than the person, and keep reports actionable. This community project has no guaranteed response times.

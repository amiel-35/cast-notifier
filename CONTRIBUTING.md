# Contributing

Thanks for considering a contribution to Cast Notifier.

## Ground rules

- **English only** in code, comments, commit messages, and documentation.
  Translation files (`translations/*.json`) are the exception.
- **Pull requests only.** `main` is protected; nothing is pushed directly.
  One PR = one reviewed increment.
- **Conventional commits.** Prefix commit subjects with `feat:`, `fix:`,
  `docs:`, `test:`, `chore:`, `refactor:`, or `ci:`, e.g.
  `feat: add per-call voice override`.
- **Tests are required** for any behavioral change: config flow changes,
  new `data` payload keys, volume handling, `deny_domains` logic. CI must
  be green (hassfest, HACS validation, lint, tests) before a PR is merged.
- **The security rule is not configurable away silently.** `deny_domains`
  defaults to `alarm_control_panel` and `lock`; changing that default, or
  the refusal behavior, needs its own PR with a clear rationale.

## Development setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements_dev.txt
```

```bash
ruff format --check .
ruff check .
mypy custom_components/cast_notifier
pytest --cov=custom_components.cast_notifier
```

## Adding or changing user-facing strings

Update `custom_components/cast_notifier/strings.json` (the source of
truth, English) and `translations/en.json` together, then update
`translations/fr.json` and `translations/es.json` with real, natural
translations -- not machine-literal ones. `tests/test_translations.py`
checks that all three files expose the same set of keys.

## Reporting issues

Open an issue with your Home Assistant version, the integration version,
relevant logs (`custom_components.cast_notifier: debug` in `logger:`), and
steps to reproduce.

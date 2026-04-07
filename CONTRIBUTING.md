# Contributing

Thanks for contributing to **X Archive Explorer**.

## Ground Rules

- Keep changes focused and production-ready.
- Preserve existing behavior unless the change explicitly modifies it.
- Add tests for new behavior and bug fixes whenever possible.
- Keep documentation aligned with code changes.

## Local Setup

1. Create a virtual environment.
2. Install dependencies:

```bash
pip install -r requirements.txt
pip install -r requirements-dev.txt
```

3. Apply migrations:

```bash
flask --app run.py db upgrade
```

4. Run tests:

```bash
python -m pytest -q
```

## Pull Request Checklist

- Clear title and scope.
- Reproducible steps for bug fixes.
- Tests added or updated.
- No secrets or local files committed.
- README updated when behavior changes.

## Code Style

- Follow existing project style and naming.
- Prefer small, readable functions.
- Keep user-facing text consistent with i18n strategy.

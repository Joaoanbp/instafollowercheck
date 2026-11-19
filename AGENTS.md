# Repository Guidelines

## Project Structure & Module Organization

InstaFollowCheck is a local Flask/Playwright app. Python lives in `src/instafollowcheck/`:
`app.py` (Flask routes and local security), `collector.py` (Playwright collection and
parsers), `db.py` (SQLite), `domain.py` (10-day window and relations), `importer.py`
(official export), `jobs.py` (background collection), and `cli.py` (commands). Templates
are in `src/instafollowcheck/templates/`; CSS and JavaScript are in
`src/instafollowcheck/static/`. Offline tests live in `tests/`.

## Build, Test, and Development Commands

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e .
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m ruff check .
.venv/bin/python -m ruff format --check .
```

Run the app with `bash iniciar.sh` or `.venv/bin/python -m instafollowcheck serve`.
No cron, external service, or environment variable is required. The app uses a dedicated
Chrome profile under `~/.local/share/instafollowcheck/browser/`.

## Coding Style & Naming Conventions

Use four spaces, `snake_case` for functions and modules, and `PascalCase` for classes.
Keep comments and user-facing messages in Portuguese. Ruff config lives in
`pyproject.toml` (line length 100, rules E/F/I). Frontend JavaScript is framework-free,
uses `"use strict"`, and must not load external assets. Keep HTML/CSS/JS package data
listed in `pyproject.toml`.

## Testing Guidelines

Use `unittest`; name test files `tests/test_<feature>.py`. Tests must be offline and must
not read real cookies, open Instagram, or launch Chrome. Use temporary data directories,
synthetic export files, and injected fetch/page functions. Cover success paths and
failure cases such as incomplete lists, invalid ZIPs, CSRF failures, and untrusted hosts.
Real collection is validated separately with a manual login in the dedicated profile.

## Commits & Pull Requests

Use short, imperative subjects such as `Add export import`. Pull requests should describe
the change, how it was checked, and any user-visible behavior. Include screenshots for UI
changes and note security or privacy implications. Never commit databases, browser
profiles, cookies, logs, or real account data.

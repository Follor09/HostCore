# Contributing to PiCore

Thanks for taking a look! Bug reports, ideas and pull requests are welcome – in English or German.

## Reporting bugs

Open an issue with the *Bug report* template. Most helpful: the version (footer of the dashboard or **Settings → Auto-update**), the Pi model and OS, what you did and what happened, and the output of **Settings → Self-test** (*Selbstdiagnose*) if it shows something.
Security issues: please **not** as a public issue – see [SECURITY.md](SECURITY.md).

## Ideas

PiCore aims to be a productive, professional tool for running a Raspberry Pi or a small homelab well: monitoring, operations, documentation, security and compliance. Ideas in that direction fit best. Gamification and novelty features don't.

## Project layout

```
app.py            entry point: the FastAPI app and its routes
backend/          one Python module per feature (auth, containers, gitops, compliance …)
static/           pages, scripts and styles (no build step)
docs/             guides and screenshots
*.sh              install, update and helper scripts (root parts run from a root-owned copy)
*.service/.timer  systemd units installed by install.sh
```

Data files (`settings.json`, `auth.json`, `history.db`, `tasks/` …) are created next to `app.py` and are never committed.

## Pull requests

- Keep the style of the surrounding code: plain FastAPI + Python modules (one module per feature), vanilla JavaScript without a build step, no inline scripts (strict CSP), CSS in `static/style.css`.
- Every new changing endpoint needs an entry in `backend/access.py` (otherwise it is admin-only) and, if it should appear in the activity log, a label in `backend/audit.py`.
- UI texts are German; add the English translation to `static/i18n.js`.
- Avoid class names starting with `ad-` – ad blockers hide them.
- New dependencies only if really needed (it runs on a Pi); `cryptography` and `httpx` are already there.
- Test on a Pi if you can, and describe what you tested in the pull request.

## Development

```bash
python3 -m venv venv && venv/bin/pip install -r requirements.txt
venv/bin/python -m uvicorn app:app --port 8000 --reload
venv/bin/python backend/auth.py set-password      # first user
```

Many features need Linux tools (systemd, apt, Docker); on other systems those sections stay empty.

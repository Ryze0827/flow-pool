# Repository Guidelines

## Project Structure & Module Organization

FlowPool is a FastAPI service with a Vue 3 client. Backend modules live in `backend/`; `main.py` creates the app, while `scheduler.py`, `store.py`, and feature modules handle scheduling, SQLite persistence, and domain operations. Frontend source is under `frontend/src/`; Vite serves `frontend/index.html`. Unit tests are in `tests/`. Operational scripts are `start.sh`, `stop.sh`, `restart.sh`, and `status.sh`; deployment helpers are in `scripts/`. Runtime state belongs in ignored `data/`.

## Build, Test, and Development Commands

- `./start.sh` — create/update `.venv`, install dependencies, build the frontend, and start port 8765.
- `./restart.sh` — repeat setup and rebuild, then restart.
- `./stop.sh` / `./status.sh` — stop the service or inspect its process state.
- `cd frontend && npm run dev` — run the Vite development server for UI work.
- `cd frontend && npm run build` — produce the production bundle consumed by FastAPI.
- `python -m unittest discover -s tests -p 'test_*.py'` — run the local mocked tests without contacting PostgreSQL or Sub2API.

## Coding Style & Naming Conventions

Use four spaces in Python and follow surrounding import and helper patterns. Prefer `snake_case` for Python files, functions, and variables; use `PascalCase` for Vue components and `camelCase` for JavaScript helpers and state. Keep API payloads explicit and preserve existing error handling. There is no repository-wide formatter or linter; avoid unrelated reformatting.

## Testing Guidelines

Tests use standard-library `unittest`, including `IsolatedAsyncioTestCase`, mocks, temporary SQLite stores, and `httpx.MockTransport`. Name files `test_*.py` and methods `test_<behavior>`. Keep tests deterministic and offline; mock upstream HTTP and use temporary data.

## Commit & Pull Request Guidelines

Use short imperative Conventional Commit subjects such as `feat: add ...`, `fix: ...`, or `style: ...`; keep commits focused. Pull requests should explain user-visible behavior and affected areas, link an issue when available, and include screenshots or a recording for UI changes. State validation commands and configuration or migration considerations. Never commit `.env`, runtime data, database files, keys, or generated `frontend/dist/`.

## Security & Configuration Tips

Copy `.env.example` to a local `.env` and keep it out of version control; use restrictive permissions for secrets. Configure `FLOWPOOL_SUB2API_URL` and PostgreSQL settings for the target environment. Do not paste tokens, SMTP passwords, or customer data into commits, tests, or logs. Run one worker per data directory because the service uses a lock file.

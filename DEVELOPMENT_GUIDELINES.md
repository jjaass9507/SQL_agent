# DEVELOPMENT_GUIDELINES.md

Project-specific development rules for SQL Agent. Applies to all feature work, maintenance, and extensions.

**Stack:** Python 3.11 / Flask / Jinja2 / Vanilla JS — AI via Pensieve API (internal LLM gateway).

---

## 1. Before You Start

**Clarify scope before touching code.**

For every feature or fix:
- State what problem this solves and what "done" looks like.
- Check if the change affects session JSON structure — if yes, add a default in `create_session()` and read with `.get("field", default)`.
- Check if the change affects API response shape — if yes, coordinate with callers.

Pre-implementation checklist:
```
□ What is the minimum change required?
□ Does this touch session data structure?
□ Does this add or change an API endpoint?
□ Does this affect frontend JS?
□ Does this need new tests?
```

## 2. Layer Boundaries

**Each layer owns one responsibility. Don't cross the lines.**

- `app.py` — URL routing, request parsing, response formatting. No business logic. No direct DB access.
- `web/` — Session management, generation coordination, DB introspection. No direct AI API calls.
- `agents/` — AI conversation logic, requirement extraction, document writing. No direct session file access.
- `models/` — Data structure definitions (dataclasses only). No business logic.
- `utils/` — HTTP wrapper, file I/O. Stateless.
- `templates/` — HTML structure and Jinja2 rendering. No business logic.
- `static/js/` — Page interactions, API calls. No direct session data manipulation.

When adding a session field:
1. Add a default in `web/session_store.py` → `create_session()`
2. Read with `.get("field", default)` everywhere — never assume the key exists
3. Update the Session Field Reference below

## 3. API Design

**Consistent naming. Consistent responses. No surprises.**

URL structure:
```
GET    /api/sessions                    # list
POST   /api/sessions                    # create
GET    /api/sessions/<id>               # read
POST   /api/sessions/<id>/messages      # sub-resource action
POST   /api/sessions/<id>/confirm       # named action (verb)
GET    /api/sessions/<id>/outputs       # sub-resource read
GET    /api/sessions/<id>/outputs/zip   # derived resource
```

Responses:
- Success: return the data object directly. Never wrap in `{ "data": ... }`.
- Error: always `{ "error": "human-readable message" }` with the right status code.
- `201` for creates, `400` for bad input, `404` for missing resources, `500` for server faults (log these).

Never:
- Return a Python exception message or traceback to the frontend.
- Return large text fields (AI-generated documents) in list endpoints — only in single-resource endpoints.
- Skip pagination for list endpoints that could exceed 100 items (`page` / `per_page`).

## 4. Session Field Reference

Sessions are stored in `data/{session_id}.json`.

| Field | Type | Description |
|---|---|---|
| `id` | str | UUID, unique session identifier |
| `title` | str | User-provided design title |
| `mode` | str | `"design"` or `"review"` |
| `phase` | str | `collecting` → `confirming` → `generating` → `done` |
| `messages` | list | Chat history `[{role, content, created_at}]` |
| `tables` | list | Confirmed TableSpec objects (JSON) |
| `context_tables` | list | Imported existing DB structure |
| `context_text` | str | Existing DB description sent to AI |
| `key_points` | list | AI-extracted requirements summary |
| `outputs` | dict | Generated documents `{"filename": "content"}` |
| `generation_status` | dict | Per-document generation status |
| `table_versions` | list | Design version snapshots |
| `created_at` | str | ISO 8601 creation time |
| `updated_at` | str | ISO 8601 last update time |

## 5. Frontend Rules

**One JS file per page. Errors must be visible. Polling must stop.**

Each JS file handles exactly one page:
- `home.js` → session list, create session
- `chat.js` → message send, AI response rendering, Markdown
- `confirm.js` → schema preview, diff display, version restore, confirm generation
- `docs.js` → document polling, Mermaid rendering, SQL highlighting, download
- `review.js` → review report rendering, progress polling

Standard fetch pattern:
```javascript
const res = await fetch('/api/...', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
});
const data = await res.json();
if (!res.ok) {
    showError(data.error); // display to user — never only console.error
    return;
}
```

Rules:
- Every async operation must disable its trigger button while in-flight.
- Polling interval must be ≥ 2 seconds. Stop polling when complete.
- Render long-form AI output with `marked.js`.

## 6. Agent Rules

**All AI calls go through `PensieveAPI`. Prompts live in files. Failures stay isolated.**

- Never call the AI directly with `requests` or `httpx` — always use `utils/client.py`.
- All prompt text lives in `prompts/`. Never hardcode prompts in Python.
- A failed AI call must not crash the session. Catch and record the error in `generation_status`.

`interviewer.chat()` returns a 3-tuple `(reply_text, tables, summary)`:
- `tables is None` → still collecting requirements
- `tables is not None` → requirements complete, advance to confirming phase
- `summary` is `list[str]` of extracted requirement points

Writers in `web/generation_worker.py` run in parallel via `ThreadPoolExecutor`. Each writer must be a pure function: `tables → str`. No session state. Raise on failure — the worker catches it.

## 7. Error Handling and Logging

**Log what broke. Never expose internals to the frontend.**

Always log:
- AI API failures (include HTTP status and response body, exclude tokens)
- DB connection failures (exclude password)
- Document generation failures (include which writer failed)
- Session file read/write failures

Never send to the frontend:
- Python tracebacks
- Database connection strings or passwords
- API tokens

Log format:
```python
logger.error("generation failed session=%s writer=%s error=%s", session_id, writer_name, str(e))
```

## 8. Testing

**New behaviour needs a test. No exceptions.**

- All tests in `tests/`, named `test_*.py`.
- Unit tests cover `agents/`, `web/`, `models/`, `utils/` — no API connection required.
- Integration tests cover Flask routes via `app.test_client()`.
- Run before every PR: `pytest tests/ -v`

## 9. Version Control

**Branch for every change. Commit messages explain why.**

Branch naming:
- `feat/<description>` — new features
- `fix/<description>` — bug fixes
- `docs/<description>` — documentation only
- `refactor/<description>` — structural changes with no behaviour change

Commit format: `<type>: <description>`

Pre-merge checklist:
```
□ pytest tests/ -v passes
□ Design mode full flow tested manually (chat → confirm → generate → download)
□ Review mode full flow tested manually
□ .env.example updated if new env vars added
□ requirements.txt updated if new packages added
□ Old session JSON files still load without KeyError
```

## 10. Hard Rules

**These are never negotiable.**

1. No secrets in code — all tokens and passwords go in `.env`.
2. No real user data in AI prompts.
3. Never commit `data/` session files.
4. Never develop directly on `main` — always branch and PR.
5. Never skip tests before merging.
6. No silent exception swallowing (`except: pass` is always wrong).

## 11. Known Limitations

- **Concurrency** — `threading.Lock` only partially protects session writes. Race conditions are possible under high concurrency. (Priority: medium)
- **Session cleanup** — `data/` files accumulate indefinitely with no TTL or cleanup job. (Priority: low)
- **Pagination** — `/api/sessions` has no pagination; performance degrades with many sessions. (Priority: low)
- **Error tracking** — No centralised error tracking (e.g. Sentry). Errors are only in local logs. (Priority: low)

---

**These guidelines are working if:** layer boundaries stay clean in diffs, API responses are consistent across all endpoints, test coverage grows with the codebase, and no secrets ever appear in git history.

import json as _json
import logging
import os
import sys

from dotenv import load_dotenv
from flask import Flask, jsonify

load_dotenv()

# When this file is executed directly (`python app.py`), Python registers it
# in sys.modules as "__main__", not "app" — so web/routes/sessions.py's
# `import app as app_module` would otherwise trigger a second, independent
# execution of this file (infinite-recursion-shaped circular import). Alias
# "app" to whichever module object is currently running so that lookup finds
# this same instance. No-op when already imported normally as "app" (pytest,
# `gunicorn app:app`).
sys.modules.setdefault("app", sys.modules[__name__])

VERSION = "0.5.0"

# SECRET_KEY placeholder shipped in .env.example — starting in non-debug mode
# without overriding it is refused (see __main__ below).
DEFAULT_SECRET_KEY = "change-me-to-a-random-32-char-string"


# ── Structured JSON logging ─────────────────────────────

class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry: dict = {
            "ts": self.formatTime(record, datefmt="%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            entry["exc"] = self.formatException(record.exc_info)
        # Merge any extra fields attached via extra={...}
        for key, val in vars(record).items():
            if key not in logging.LogRecord.__dict__ and not key.startswith("_"):
                entry[key] = val
        return _json.dumps(entry, ensure_ascii=False, default=str)


def _setup_logging() -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(_JsonFormatter())
    logging.root.setLevel(logging.INFO)
    logging.root.handlers = [handler]


_setup_logging()
logger = logging.getLogger(__name__)


# generation_worker functions are re-exported here (rather than imported
# directly by web/routes/sessions.py) so tests can keep patching them as
# "app.run_generation" etc.; web/routes/sessions.py calls them via
# `import app as app_module` and looks the attribute up at request time.
from web.generation_worker import run_generation, run_incremental, run_review, run_single_file


def create_app() -> Flask:
    app = Flask(__name__)
    app.secret_key = os.environ.get("SECRET_KEY", DEFAULT_SECRET_KEY)

    @app.errorhandler(404)
    def _not_found(e):
        return jsonify({"error": "not found"}), 404

    @app.get("/health")
    def health():
        return jsonify({"status": "ok", "version": VERSION})

    from web.routes.agent import bp as db_agent_bp
    from web.routes.changes import bp as changes_bp
    from web.routes.pages import bp as pages_bp
    from web.routes.sessions import bp as sessions_bp
    from web.routes.settings import bp as settings_bp
    from web.routes.workbench import bp as workbench_bp

    app.register_blueprint(pages_bp)
    app.register_blueprint(sessions_bp)
    app.register_blueprint(workbench_bp)
    app.register_blueprint(settings_bp)
    app.register_blueprint(db_agent_bp)
    app.register_blueprint(changes_bp)

    return app


app = create_app()

# Re-exported for tests/test_api.py's `application._interviewer_store.clear()`
# fixture — same dict object web/routes/sessions.py mutates, not a copy.
from web.routes.sessions import _interviewer_store  # noqa: E402

# Names below are not referenced directly in this module — they're re-exports
# for dynamic attribute access (web/routes/sessions.py's `app_module.run_x`)
# and for tests/test_api.py's `patch("app.run_x")` / `_interviewer_store.clear()`.
__all__ = ["app", "run_generation", "run_incremental", "run_review", "run_single_file",
           "_interviewer_store"]


if __name__ == "__main__":
    debug = os.environ.get("FLASK_DEBUG", "").strip().lower() in ("1", "true", "yes")
    if not debug and app.secret_key == DEFAULT_SECRET_KEY:
        sys.exit(
            "SECRET_KEY is still the default placeholder. Set a real SECRET_KEY "
            "in .env before starting outside debug mode (or set FLASK_DEBUG=1 for local dev)."
        )
    host = os.environ.get("HOST", "127.0.0.1")
    app.run(debug=debug, host=host, port=5000)

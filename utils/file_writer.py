from datetime import datetime
from pathlib import Path

OUTPUT_ROOT = Path(__file__).parent.parent / "output"


def create_session_dir() -> Path:
    session_dir = OUTPUT_ROOT / datetime.now().strftime("%Y%m%d_%H%M%S")
    session_dir.mkdir(parents=True, exist_ok=True)
    return session_dir


def write_outputs(session_dir: Path, outputs: dict[str, str]) -> None:
    for filename, content in outputs.items():
        (session_dir / filename).write_text(content, encoding="utf-8")

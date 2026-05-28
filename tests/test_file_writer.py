"""Tests for file_writer utilities."""
import pytest
from pathlib import Path
from utils.file_writer import create_session_dir, write_outputs


def test_create_session_dir_creates_directory(tmp_path, monkeypatch):
    import utils.file_writer as fw
    monkeypatch.setattr(fw, "OUTPUT_ROOT", tmp_path)
    session_dir = create_session_dir()
    assert session_dir.exists()
    assert session_dir.is_dir()
    assert session_dir.parent == tmp_path


def test_create_session_dir_name_format(tmp_path, monkeypatch):
    import utils.file_writer as fw
    monkeypatch.setattr(fw, "OUTPUT_ROOT", tmp_path)
    session_dir = create_session_dir()
    # Name should be YYYYMMDD_HHMMSS (15 chars)
    assert len(session_dir.name) == 15
    assert session_dir.name[8] == "_"


def test_write_outputs(tmp_path):
    outputs = {
        "01_spec.md": "# Spec\nsome content",
        "02_ddl.sql": "CREATE TABLE foo (id INT);",
    }
    write_outputs(tmp_path, outputs)
    for filename, content in outputs.items():
        written = (tmp_path / filename).read_text(encoding="utf-8")
        assert written == content


def test_write_outputs_utf8(tmp_path):
    outputs = {"spec.md": "欄位說明：主鍵 UUID"}
    write_outputs(tmp_path, outputs)
    assert (tmp_path / "spec.md").read_text(encoding="utf-8") == "欄位說明：主鍵 UUID"

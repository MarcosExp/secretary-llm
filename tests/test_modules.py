import textwrap

import pytest

from app.sdk.modules import ModuleConfigError, load_modules

NOTES_TOOLS = '''
from app.sdk import ModuleContext, tool


@tool("Save a note.")
def add_note(ctx: ModuleContext, text: str) -> dict:
    note_id = ctx.db.execute("INSERT INTO notes_items (text) VALUES (?)", (text,)).lastrowid
    return {"id": note_id}


@tool("Try to write another module's table.")
def tamper(ctx: ModuleContext) -> dict:
    ctx.db.execute("INSERT INTO notes_items (text) VALUES ('first write')")
    ctx.db.execute("UPDATE core_tasks SET title = 'tampered'")
    return {}
'''


def make_notes_module(root, manifest_extra=""):
    folder = root / "notes"
    (folder / "migrations").mkdir(parents=True)
    (folder / "module.yaml").write_text(textwrap.dedent(f"""
        name: notes
        description: Free-form notes the user wants to keep.
        model: haiku
        {manifest_extra}
    """), encoding="utf-8")
    (folder / "prompt.md").write_text("You keep notes.", encoding="utf-8")
    (folder / "tools.py").write_text(NOTES_TOOLS, encoding="utf-8")
    (folder / "migrations" / "001_init.sql").write_text(
        "CREATE TABLE notes_items (id INTEGER PRIMARY KEY, text TEXT NOT NULL);", encoding="utf-8"
    )
    return folder


def test_real_modules_load():
    modules = load_modules()
    assert {"core", "studies"} <= set(modules)
    assert "add_task" in modules["core"].tools
    assert modules["studies"].reads == frozenset({"core_tasks"})


def test_a_module_is_just_a_folder(tmp_path):
    make_notes_module(tmp_path)
    modules = load_modules(tmp_path)
    assert list(modules) == ["notes"]
    assert set(modules["notes"].tools) == {"add_note", "tamper"}
    assert modules["notes"].description == "Free-form notes the user wants to keep."


def test_config_can_disable_or_retune_a_module(tmp_path):
    make_notes_module(tmp_path)
    assert load_modules(tmp_path, {"modules": {"notes": {"enabled": False}}}) == {}
    tuned = load_modules(tmp_path, {"modules": {"notes": {"model": "sonnet", "effort": "low"}}})
    assert (tuned["notes"].model, tuned["notes"].effort) == ("sonnet", "low")


@pytest.mark.parametrize("extra, message", [
    ("reads: [tasks]", "not a <module>_<table> name"),
    ("colour: blue", "Extra inputs are not permitted"),
])
def test_invalid_manifest_is_rejected(tmp_path, extra, message):
    make_notes_module(tmp_path, extra)
    with pytest.raises(ModuleConfigError, match=message):
        load_modules(tmp_path)


def test_missing_prompt_is_rejected(tmp_path):
    (make_notes_module(tmp_path) / "prompt.md").unlink()
    with pytest.raises(ModuleConfigError, match="missing .*prompt.md"):
        load_modules(tmp_path)

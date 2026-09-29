import shutil
import subprocess

import pytest

from app.agent.loop import LoopRunner
from app.agent.orchestrator import ORCHESTRATOR_PROMPT, Agent
from app.llm.fake import ScriptedProvider, text, tool_call
from app.sdk import NotFound
from app.sdk.modules import load_modules
from app.sdk.registry import AWAITING, Registry
from modules.wiki.store import WikiStore

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git is required for the wiki")


@pytest.fixture
def wiki(tmp_path):
    store = WikiStore(tmp_path / "wiki")
    store.init()
    return store


def commits(store: WikiStore) -> list[str]:
    log = subprocess.run(["git", "-c", f"safe.directory={store.root}", "log", "--format=%s"],
                         cwd=store.root, capture_output=True, text=True)
    return log.stdout.splitlines()


def test_init_creates_template_once_and_never_overwrites(wiki):
    assert {"index.md", "rules.md", "profile.md", "decisions.md", "journal/README.md"} <= set(wiki.pages())
    wiki.write("rules.md", "# Rules\n- mine", "my rules")
    assert wiki.init() == []
    assert "- mine" in wiki.read("rules.md")
    assert commits(wiki) == ["my rules", "Create wiki from template"]


@pytest.mark.parametrize("path", ["../escape.md", "/etc/passwd.md", ".git/config.md", "notes.txt",
                                  "a/../../b.md", "", "sub/.hidden/x.md"])
def test_paths_stay_inside_the_wiki(wiki, path):
    with pytest.raises(ValueError, match="invalid wiki path"):
        wiki.resolve(path)


def test_write_append_and_search(wiki):
    wiki.write("courses/algebra.md", "# Algebra\nExam in January", "algebra notes")
    wiki.append("decisions.md", "## 2027-01-05 — Drop the elective\nToo many hours.", "log decision")
    assert wiki.read("decisions.md").endswith("## 2027-01-05 — Drop the elective\nToo many hours.\n")
    [match] = wiki.search("exam january")
    assert (match.path, match.line) == ("courses/algebra.md", 2)
    assert commits(wiki)[:2] == ["log decision", "algebra notes"]


def test_missing_page_lists_what_exists(wiki):
    with pytest.raises(NotFound, match="rules.md"):
        wiki.read("nope.md")


def test_rules_and_profile_changes_wait_for_confirmation(conn, wiki):
    registry = Registry(load_modules(), conn, {"wiki": wiki})
    with conn:
        proposed = registry.call("wiki", "wiki_write",
                                 {"path": "rules.md", "content": "# Rules\n- new", "reason": "r"})
        direct = registry.call("wiki", "wiki_append",
                               {"path": "decisions.md", "text": "## today", "reason": "d"})
    assert proposed["status"] == AWAITING and "- new" in proposed["summary"]
    assert "- new" not in wiki.read("rules.md")  # untouched until confirmed
    assert direct["commit"] and "## today" in wiki.read("decisions.md")


def test_orchestrator_reads_the_memory_pages(conn, wiki):
    wiki.write("rules.md", "# Rules\n<!-- template hint -->\n- Protect Sunday afternoons.", "rules")
    provider = ScriptedProvider([text("ok")])
    Agent(LoopRunner(provider), load_modules(), conn, "haiku", services={"wiki": wiki}).ask("hi")
    system = provider.requests[0]["system"]
    assert system.startswith(ORCHESTRATOR_PROMPT)
    assert '<page path="rules.md">' in system and "Protect Sunday afternoons." in system
    assert "template hint" not in system


def test_modules_get_the_rules_and_empty_templates_are_skipped(conn, wiki):
    wiki.write("rules.md", "# Rules\n- No work after 21:00.", "rules")
    provider = ScriptedProvider([
        tool_call("delegate_to_core", {"task": "x"}), text("module report"), text("done"),
    ])
    Agent(LoopRunner(provider), load_modules(), conn, "haiku", services={"wiki": wiki}).ask("hi")
    orchestrator, module = provider.requests[0]["system"], provider.requests[1]["system"]
    assert "No work after 21:00." in module
    assert '<page path="profile.md">' not in orchestrator  # untouched template: nothing to say


def test_no_wiki_means_the_plain_prompt(conn):
    provider = ScriptedProvider([text("ok")])
    Agent(LoopRunner(provider), load_modules(), conn, "haiku").ask("hi")
    assert provider.requests[0]["system"] == ORCHESTRATOR_PROMPT

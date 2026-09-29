from typing import Annotated

from pydantic import Field

from app.sdk import ModuleContext, tool
from modules.wiki.store import Match, WikiStore

# The agent reads these on every request; changing them changes its behaviour,
# so it needs the user's confirmation (a text read elsewhere must not rewrite them).
PROTECTED = {"rules.md", "profile.md"}

Path_ = Annotated[str, Field(description="Relative path of a .md page, e.g. decisions.md or courses/algebra.md")]


def _wiki(ctx: ModuleContext) -> WikiStore:
    return ctx.service("wiki")


def _protected(args: dict) -> bool:
    return args["path"].strip().lstrip("./") in PROTECTED


def _summarize_write(ctx: ModuleContext, path: str, content: str, reason: str) -> str:
    return f"Rewrite wiki page {path} ({reason}). New content:\n\n{content.strip()}"


def _summarize_append(ctx: ModuleContext, path: str, text: str, reason: str) -> str:
    return f"Add to wiki page {path} ({reason}):\n\n{text.strip()}"


@tool("List the wiki's pages.")
def wiki_list(ctx: ModuleContext) -> list[str]:
    return _wiki(ctx).pages()


@tool("Read a wiki page.")
def wiki_read(ctx: ModuleContext, path: Path_) -> str:
    return _wiki(ctx).read(path)


@tool("Find lines in the wiki containing all the given words (case-insensitive).")
def wiki_search(ctx: ModuleContext, query: str) -> list[Match]:
    return _wiki(ctx).search(query)


@tool(
    """
    Replace a wiki page with new content (read it first and keep what should stay).
    The change is committed to git. rules.md and profile.md change only after the user confirms.
    """,
    confirm=_protected,
    summarize=_summarize_write,
)
def wiki_write(ctx: ModuleContext, path: Path_, content: str,
               reason: Annotated[str, Field(description="Short reason, used as the commit message")]) -> dict:
    return {"path": path, "commit": _wiki(ctx).write(path, content, reason)}


@tool(
    """
    Add text at the end of a wiki page (created if missing), e.g. a dated entry in
    decisions.md. rules.md and profile.md change only after the user confirms.
    """,
    confirm=_protected,
    summarize=_summarize_append,
)
def wiki_append(ctx: ModuleContext, path: Path_, text: str,
                reason: Annotated[str, Field(description="Short reason, used as the commit message")]) -> dict:
    return {"path": path, "commit": _wiki(ctx).append(path, text, reason)}

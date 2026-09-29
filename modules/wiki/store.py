"""The user's Markdown wiki: a folder of .md files under git.

Every write is committed, so any change can be inspected and undone with git;
nothing is deleted from here. Paths are relative to the wiki root and limited to
Markdown files outside hidden folders.
"""

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from app.sdk.errors import NotFound, ToolError

TEMPLATE = Path(__file__).parent / "template"
MAX_FILE_CHARS = 100_000
GIT_IDENTITY = ["-c", "user.name=secretary", "-c", "user.email=secretary@localhost"]


@dataclass(frozen=True)
class Match:
    path: str
    line: int
    text: str


class WikiStore:
    def __init__(self, root: Path):
        self.root = root.resolve()

    @classmethod
    def from_env(cls) -> "WikiStore | None":
        root = os.getenv("WIKI_DIR")
        return cls(Path(root)) if root and Path(root).is_dir() else None

    # --- Paths ---

    def resolve(self, path: str) -> Path:
        relative = PurePosixPath(path.strip().replace("\\", "/"))
        if (relative.is_absolute() or not relative.parts or relative.suffix != ".md"
                or any(part in ("..", "") or part.startswith(".") for part in relative.parts)):
            raise ValueError(f"invalid wiki path '{path}' (use a relative path to a .md file, e.g. notes/topic.md)")
        full = (self.root / relative).resolve()
        if self.root not in full.parents:
            raise ValueError(f"invalid wiki path '{path}'")
        return full

    def pages(self) -> list[str]:
        return sorted(
            str(p.relative_to(self.root).as_posix())
            for p in self.root.rglob("*.md")
            if not any(part.startswith(".") for part in p.relative_to(self.root).parts)
        )

    # --- Reading ---

    def read(self, path: str) -> str:
        full = self.resolve(path)
        if not full.is_file():
            raise NotFound(f"wiki page '{path}' does not exist (pages: {', '.join(self.pages()) or 'none'})")
        return full.read_text(encoding="utf-8")

    def read_optional(self, path: str) -> str | None:
        try:
            return self.read(path)
        except NotFound:
            return None

    def search(self, query: str, limit: int = 30) -> list[Match]:
        words = [w.lower() for w in query.split() if w.strip()]
        if not words:
            raise ValueError("empty search")
        matches = []
        for page in self.pages():
            for number, line in enumerate((self.root / page).read_text(encoding="utf-8").splitlines(), 1):
                lowered = line.lower()
                if all(word in lowered for word in words):
                    matches.append(Match(page, number, line.strip()[:200]))
                    if len(matches) >= limit:
                        return matches
        return matches

    # --- Writing (always committed) ---

    def write(self, path: str, content: str, message: str) -> str:
        if len(content) > MAX_FILE_CHARS:
            raise ValueError(f"page too long ({len(content)} characters, max {MAX_FILE_CHARS})")
        full = self.resolve(path)
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(content if content.endswith("\n") else content + "\n", encoding="utf-8")
        return self._commit(path, message)

    def append(self, path: str, text: str, message: str) -> str:
        existing = self.read_optional(path) or ""
        separator = "" if not existing or existing.endswith("\n\n") else ("\n" if existing.endswith("\n") else "\n\n")
        return self.write(path, existing + separator + text.strip() + "\n", message)

    def _git(self, *args: str) -> subprocess.CompletedProcess:
        if shutil.which("git") is None:
            raise ToolError("git is not installed; wiki changes cannot be versioned")
        return subprocess.run(
            ["git", "-c", f"safe.directory={self.root}", *GIT_IDENTITY, *args],
            cwd=self.root, capture_output=True, text=True, timeout=30,
        )

    def _commit(self, path: str, message: str) -> str:
        self._git("add", "--", str(self.resolve(path).relative_to(self.root).as_posix()))
        result = self._git("commit", "-q", "-m", message.strip() or f"Update {path}")
        if result.returncode != 0 and "nothing to commit" not in result.stdout + result.stderr:
            raise ToolError(f"could not commit the wiki change: {result.stderr.strip()[:200]}")
        head = self._git("rev-parse", "--short", "HEAD")
        return head.stdout.strip()

    # --- Setup ---

    def init(self) -> list[str]:
        """Create the git repository and any missing template pages. Never overwrites."""
        self.root.mkdir(parents=True, exist_ok=True)
        if not (self.root / ".git").exists():
            self._git("init", "-q")
        created = []
        for source in sorted(TEMPLATE.rglob("*.md")):
            relative = source.relative_to(TEMPLATE).as_posix()
            target = self.root / relative
            if not target.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)
                self._git("add", "--", relative)
                created.append(relative)
        if created:
            self._git("commit", "-q", "-m", "Create wiki from template")
        return created

"""
Docstrings state each rule in plain words, without design-note citations.

The API reference is built from docstrings and published with the library, so a
docstring reads on its own. Where a rule comes from belongs in a comment under the
module docstring, which the reference never renders. A ruling's status is never
copied into code at all: it changes in the design repository, and a copy goes stale.

The tests are held to the same rule. A test's docstring is the claim it checks, read by
whoever is looking at a failure, so it says what is true rather than where that was
decided. Their comments and strings, and the CI workflows, follow it too, since nothing
renders them apart from the code they sit in.
"""

from __future__ import annotations

import ast
import re
import tokenize
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src" / "anagrapheus"
TESTS = ROOT / "tests"
WORKFLOWS = ROOT / ".github" / "workflows"

CITATION = re.compile(
    r"\bR\d+\b"  # a ruling number
    r"|\bU\d+\b"  # an open-question number
    r"|\b(?:[Dd]oc|[Nn]ote)s? \d+"  # a design document or note
    r"|§"  # a section of one
    r"|\bNVM \d+:\d+"  # a line in an upstream design document
    r"|_design/"  # a path into the design notes
    r"|\b(?:PROPOSED|RATIFIED)\b"  # a ruling's status
)

type Docstring = tuple[int, str]


def _literal(node: ast.stmt) -> str | None:
    if (
        isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    ):
        return node.value.value
    return None


def _docstrings(tree: ast.Module) -> list[Docstring]:
    """Module, class and function docstrings, and attribute docstrings in any body."""
    found: list[Docstring] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        body = node.body
        if body and (text := _literal(body[0])) is not None:
            found.append((body[0].lineno, text))
        for previous, statement in zip(body, body[1:], strict=False):
            if not isinstance(previous, ast.Assign | ast.AnnAssign | ast.TypeAlias):
                continue
            if (text := _literal(statement)) is not None:
                found.append((statement.lineno, text))
    return found


def _sites(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    sites: list[str] = []
    for start, text in _docstrings(tree):
        for offset, line in enumerate(text.splitlines()):
            if matches := [match.group(0) for match in CITATION.finditer(line)]:
                where = path.relative_to(ROOT)
                sites.append(f"{where}:{start + offset}: {', '.join(matches)}: {line.strip()}")
    return sites


def test_docstrings_carry_no_design_citations():
    sites = [site for path in sorted(SRC.rglob("*.py")) for site in _sites(path)]
    assert not sites, "design citations in docstrings:\n" + "\n".join(sites)


def test_test_docstrings_carry_no_design_citations():
    sites = [site for path in sorted(TESTS.rglob("*.py")) for site in _sites(path)]
    assert not sites, "design citations in test docstrings:\n" + "\n".join(sites)


def _cited(where: Path, number: int, line: str) -> str | None:
    if matches := [match.group(0) for match in CITATION.finditer(line)]:
        return f"{where.relative_to(ROOT)}:{number}: {', '.join(matches)}: {line.strip()}"
    return None


def _token_sites(path: Path) -> list[str]:
    """Citations in a module's comments and string literals, docstrings included."""
    sites: list[str] = []
    with path.open("rb") as source:
        for token in tokenize.tokenize(source.readline):
            if token.type not in (tokenize.COMMENT, tokenize.STRING, tokenize.FSTRING_MIDDLE):
                continue
            for offset, line in enumerate(token.string.splitlines()):
                if (site := _cited(path, token.start[0] + offset, line)) is not None:
                    sites.append(site)
    return sites


def test_test_comments_and_strings_carry_no_design_citations():
    # This module is skipped: its pattern and samples are citations on purpose.
    here = Path(__file__).resolve()
    paths = [path for path in sorted(TESTS.rglob("*.py")) if path.resolve() != here]
    sites = [site for path in paths for site in _token_sites(path)]
    assert not sites, "design citations in test comments or strings:\n" + "\n".join(sites)


def test_workflows_carry_no_design_citations():
    sites = [
        site
        for path in sorted(WORKFLOWS.glob("*.y*ml"))
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if (site := _cited(path, number, line)) is not None
    ]
    assert not sites, "design citations in workflows:\n" + "\n".join(sites)


def test_the_pattern_matches_citations_and_not_prose():
    cited = ("R58", "U42", "doc 00", "Doc 00", "notes 07", "§3a", "NVM 16:96", "_design/v2")
    prose = ("RGB", "a Reader", "U+2014", "the docs", "a note", "proposed", "ratified")
    for text in (*cited, "RATIFIED", "PROPOSED"):
        assert CITATION.search(text), text
    for text in prose:
        assert not CITATION.search(text), text

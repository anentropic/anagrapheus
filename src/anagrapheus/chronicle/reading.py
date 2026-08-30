"""
The chronicle's two read surfaces, at two grades.

R61 clause 4 forbids a **binding** read of generated prose — any read that *settles*
something: canon, a gate, a validator's verdict, a mechanical outcome. Downstream
generation may and must consume it; that is what the chronicle is for.

A rule written down is a rule that gets forgotten, so the split is in the API rather
than in a docstring:

- :class:`Anchors` — deterministic. Ranges, boundaries, supersession, watermark. A
  rebuild reproduces all of it, so a binding read is safe here.
- :class:`Prose` — generated. The words. Advisory only, and it cannot be reached
  through :class:`Anchors` at all.

Neither can be obtained from the other, which is the whole point: a caller that needs a
verdict takes ``Anchors`` and finds there is no prose to misuse.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, NamedTuple, Protocol

if TYPE_CHECKING:
    from collections.abc import Sequence

    from anagrapheus.chronicle.sections import Anchor, Section, SectionId
    from anagrapheus.chronicle.store import ChronicleStore
    from anagrapheus.timeline.envelope import StoredEvent
    from anagrapheus.timeline.upcast import EventReader

__all__ = ["Anchors", "Brief", "Prose", "Recent", "Retriever", "substring_retriever"]


class Anchors:
    """
    The deterministic surface: what was consolidated, and over what.

    Safe for a binding read — deciding what to consolidate next, gating on coverage,
    asserting an invariant — because every value here is a pure function of the log.
    """

    def __init__(self, store: ChronicleStore) -> None:
        self._store = store

    def all(self, *, upto_seq: int | None = None) -> Sequence[Anchor]:
        """Every current anchor, oldest first."""
        return self._store.anchors(upto_seq=upto_seq)

    def consolidated_through(self) -> int:
        """Where the consolidated past ends and the verbatim tail begins."""
        return self._store.consolidated_through()

    def covers(self, seq: int) -> Anchor | None:
        """The anchor whose range contains ``seq``, if one does."""
        return next(
            (a for a in self._store.anchors() if a.from_seq <= seq <= a.to_seq),
            None,
        )


class Prose:
    """
    The generated surface: the words themselves.

    **Advisory only.** Everything reachable here was written by a model over a range of
    the log, and consolidating that range again would produce different words. Feed it
    to a reasoning step; never let it settle anything.
    """

    def __init__(self, store: ChronicleStore) -> None:
        self._store = store

    def section(self, section_id: SectionId) -> Section | None:
        """One section by id, superseded or not."""
        return self._store.section(section_id)

    def covering(self, seq: int) -> Section | None:
        """The current section covering ``seq``, if any range does."""
        return self._store.covering(seq)

    def all(self, *, upto_seq: int | None = None) -> Sequence[Section]:
        """Every current section, oldest first."""
        return self._store.sections(upto_seq=upto_seq)


class Retriever(Protocol):
    """
    Ranked lookup over the chronicle. A port, deliberately.

    Doc 00 §7 calls the hybrid sparse/dense/fusion stack *"well-served commodity; it
    belongs behind a port"* — so this is the seam, and :func:`substring_retriever` is a
    placeholder that makes the layer usable and testable without one. Anything serious
    replaces it.

    Returns **sections**, never raw events: §7's fourth bullet asks retrieval to hand
    back a distilled, ranked result rather than chunks, and the type says so.
    """

    def __call__(self, sections: Sequence[Section], query: str) -> Sequence[Section]:
        """The sections matching ``query``, best first."""
        ...


class Recent(NamedTuple):
    """The verbatim tail: the last events, straight from the log."""

    events: Sequence[StoredEvent]
    since_seq: int


class Brief:
    """
    The recency/depth split: verbatim tail plus consolidated past.

    Doc 00 §7's third bullet, and the reason consolidation lag is *invisible* — the tail
    covers whatever the chronicle has not reached yet, so a chronicle running behind
    self-heals rather than showing a gap.

    The tail is read **from the log, never from the chronicle**, which is one of
    R61 clause 4's by-construction guarantees: near-term continuity never depends on
    generated prose, whatever the chronicle does or does not contain.
    """

    def __init__(self, store: ChronicleStore, reader: EventReader) -> None:
        self._store = store
        self._reader = reader
        self.anchors = Anchors(store)
        self.prose = Prose(store)

    def tail(self, *, since_seq: int) -> Recent:
        """The log's own events above ``since_seq``, verbatim."""
        return Recent(events=list(self._reader.read_raw(since_seq=since_seq)), since_seq=since_seq)

    def depth(self, *, upto_seq: int | None = None) -> Sequence[Section]:
        """The consolidated past — advisory prose, for a reasoning step to read."""
        return self._store.sections(upto_seq=upto_seq)


def substring_retriever(sections: Sequence[Section], query: str) -> Sequence[Section]:
    """
    The naive default: case-insensitive substring, most recent first.

    Named for what it is. It is not ranked retrieval and does not pretend to be — a
    default that looked competent would discourage replacing it, and §7's whole point
    about the retrieval stack is that the real one belongs to the application.
    """
    needle = query.casefold()
    hits = [s for s in sections if needle in s.prose.casefold()]
    return sorted(hits, key=lambda s: s.anchor.to_seq, reverse=True)

"""
Slices: the only way an assembler reads, and each one remembers what it handed out.

A role is granted the slices it declares and nothing else, so what it cannot read it
cannot put in a prompt. That is the narrator firewall at runtime: NVM holds the Narrator
to "contract and continuity window, nothing else" with an import rule, and a slice is
the same rule one level down, where a role's code may import the belief types for other
reasons.

**Reading through a slice, not a store, is what makes provenance free.** Every slice
records each thing it returns, so a context knows its sources without trusting the
assembler to list them. The demo's worst prompt defect — a private remark rendered into
the speaker's own prompt through shared prose — becomes an assertion on sources, which
still holds when the wording changes.

**Slices read at one position.** A log slice is bounded by ``upto_seq``. A belief slice
reads the store live, so it is handed a way to ask how far the belief fold has got, and
it refuses to answer when that is not ``upto_seq``: a fold that moved during assembly
would otherwise tear the context, with half its beliefs from before an event and half
from after it.
"""

# Design: note 07 §3a, §6; note 06 §5b; upstream NVM R24 (firewall), R58 (class gate), R63.

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

from doxastica import BeliefFilter

from anagrapheus.assembly.errors import NotGranted, StaleRead
from anagrapheus.assembly.parts import EventRef, GivenRef, SectionRef
from anagrapheus.beliefs.ops import StateRef

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Sequence
    from uuid import UUID

    from doxastica import BeliefState, BeliefStore

    from anagrapheus.assembly.parts import Source
    from anagrapheus.chronicle.sections import Section
    from anagrapheus.chronicle.store import ChronicleStore
    from anagrapheus.timeline.envelope import DomainEvent, Envelope, StoredEvent
    from anagrapheus.timeline.upcast import EventReader

__all__ = [
    "BeliefSlice",
    "ChronicleSlice",
    "EventsSlice",
    "Given",
    "LogSlice",
    "Slice",
    "handed_out",
]


@runtime_checkable
class Slice(Protocol):
    """
    Anything a grant may hold: a reader at a position that remembers what it returned.

    ``upto_seq`` is ``None`` for a slice with no position, such as a :class:`Given`.
    An application's own reader can be a slice too — the demo's party view is one.
    """

    @property
    def upto_seq(self) -> int | None:
        """The log position this slice reads at, or ``None`` if it has none."""
        ...

    def handed_out(self) -> Sequence[Source]:
        """Everything returned so far, in the order it was returned, without repeats."""
        ...


class _Ledger:
    """What a slice has handed out, in order, once each."""

    def __init__(self) -> None:
        self._seen: dict[Source, None] = {}

    def note(self, source: Source) -> None:
        self._seen.setdefault(source, None)

    def all(self) -> tuple[Source, ...]:
        return tuple(self._seen)


class LogSlice:
    """
    Events up to ``upto_seq`` that ``admit`` accepts, decoded and upcast.

    ``admit`` sees the stored envelope only, so a refused event is never upcast or
    decoded. It is where a reader declares which kinds of event it may see, and where
    an application keeps a private event out of a shared prompt.
    """

    def __init__(
        self,
        reader: EventReader,
        *,
        upto_seq: int,
        admit: Callable[[Envelope], bool] | None = None,
    ) -> None:
        self._reader = reader
        self._upto = upto_seq
        self._admit = admit
        self._ledger = _Ledger()

    @property
    def upto_seq(self) -> int:
        """The last position this slice will return."""
        return self._upto

    def events(self, *, since_seq: int = 0) -> list[tuple[StoredEvent, DomainEvent]]:
        """Admitted events above ``since_seq`` and up to this slice's position."""
        out: list[tuple[StoredEvent, DomainEvent]] = []
        # Filtered on the stored envelope before upcast and decode, so an event past
        # this slice's position, or one `admit` refuses, is never touched at all.
        for stored, decoded in self._reader.read(since_seq, upto_seq=self._upto, where=self._admit):
            self._ledger.note(EventRef(stored.seq, stored.envelope.event_id))
            out.append((stored, decoded))
        return out

    def handed_out(self) -> tuple[Source, ...]:
        """Every event returned so far."""
        return self._ledger.all()


class EventsSlice:
    """
    A fixed run of events — what a generative fold is handed — read through ``admit``.

    The chronicle's summarizer is given its range rather than reading the log, so its
    slice is built over that range. The position is the range's last event.
    """

    def __init__(
        self,
        events: Sequence[StoredEvent],
        *,
        admit: Callable[[Envelope], bool] | None = None,
    ) -> None:
        self._events = tuple(events)
        self._admit = admit
        self._ledger = _Ledger()

    @property
    def upto_seq(self) -> int | None:
        """The last event's position, or ``None`` for an empty run."""
        return self._events[-1].seq if self._events else None

    def events(self) -> list[StoredEvent]:
        """The admitted events, in order."""
        out: list[StoredEvent] = []
        for event in self._events:
            if self._admit is not None and not self._admit(event.envelope):
                continue
            self._ledger.note(EventRef(event.seq, event.envelope.event_id))
            out.append(event)
        return out

    def handed_out(self) -> tuple[Source, ...]:
        """Every event returned so far."""
        return self._ledger.all()


class BeliefSlice:
    """
    The scopes ``visible`` accepts, read from a belief store folded to ``upto_seq``.

    ``folded_through`` reports the belief fold's position — typically
    ``lambda: store.read_position(FOLD_NAME)``. A read refuses with
    :class:`~anagrapheus.assembly.errors.StaleRead` when it is not ``upto_seq``, whether
    the fold is behind (catch up before assembling) or ahead (something was appended
    while assembling). Pass ``upto_seq=None`` and no ``folded_through`` for a live
    slice, which reads whatever the store holds now and promises no position.
    """

    def __init__(
        self,
        store: BeliefStore,
        *,
        visible: Callable[[str], bool],
        upto_seq: int | None = None,
        folded_through: Callable[[], int] | None = None,
    ) -> None:
        if (upto_seq is None) != (folded_through is None):
            raise ValueError("a belief slice takes both upto_seq and folded_through, or neither")
        self._store = store
        self._visible = visible
        self._upto = upto_seq
        self._folded_through = folded_through
        self._ledger = _Ledger()

    @property
    def upto_seq(self) -> int | None:
        """The fold position this slice reads at, or ``None`` if it reads live."""
        return self._upto

    def scope(self, scope_id: str) -> list[BeliefState]:
        """Every current belief ``scope_id`` holds, if it is granted."""
        states = self._read(scope_id)
        for state in states:
            self._ledger.note(StateRef.of(state))
        return states

    def belief(self, scope_id: str, belief_id: str) -> BeliefState | None:
        """
        One belief by exact id, or ``None``. Only the state returned is recorded.

        By exact id, always: "the first belief in the scope" reads correctly while the
        scope holds one thing and answers a different question once it holds two.
        """
        found = next((s for s in self._read(scope_id) if s.belief_id == belief_id), None)
        if found is not None:
            self._ledger.note(StateRef.of(found))
        return found

    def _read(self, scope_id: str) -> list[BeliefState]:
        if not self._visible(scope_id):
            raise NotGranted(f"scope {scope_id!r} is not in this grant")
        if self._folded_through is not None:
            folded = self._folded_through()
            if folded != self._upto:
                raise StaleRead(
                    f"the belief fold is at {folded} and this slice reads at {self._upto}; "
                    f"a context read across a moving fold would mix two positions"
                )
        return list(self._store.query_scope(scope_id, BeliefFilter()))

    def handed_out(self) -> tuple[Source, ...]:
        """Every belief state returned so far."""
        return self._ledger.all()


class ChronicleSlice:
    """
    Chronicle sections covering nothing past ``upto_seq``.

    Everything returned is model-written prose, so a part citing one must be
    :attr:`~anagrapheus.assembly.parts.Register.generated`; ``assemble`` refuses a part
    that cites a section under any other register.
    """

    def __init__(self, store: ChronicleStore, *, upto_seq: int) -> None:
        self._store = store
        self._upto = upto_seq
        self._ledger = _Ledger()

    @property
    def upto_seq(self) -> int:
        """The position no returned section reaches past."""
        return self._upto

    def sections(self) -> list[Section]:
        """Every current section, oldest first."""
        sections = list(self._store.sections(upto_seq=self._upto))
        for section in sections:
            anchor = section.anchor
            self._ledger.note(SectionRef(anchor.section_id, anchor.from_seq, anchor.to_seq))
        return sections

    def handed_out(self) -> tuple[Source, ...]:
        """Every section returned so far."""
        return self._ledger.all()


class Given[T]:
    """
    A value handed in by the application, recorded the first time it is read.

    For what a role is shown that no projection produced: the Narrator's contract,
    which is the Thinker's output, or a line the caller computed in memory.
    """

    def __init__(self, name: str, value: T, *, event_id: UUID | None = None) -> None:
        self._value = value
        self._ref = GivenRef(name, event_id)
        self._ledger = _Ledger()

    @property
    def upto_seq(self) -> None:
        """A given value has no log position."""
        return None

    @property
    def value(self) -> T:
        """The value, recorded as read."""
        self._ledger.note(self._ref)
        return self._value

    @property
    def ref(self) -> GivenRef:
        """How a part cites this value."""
        return self._ref

    def handed_out(self) -> tuple[Source, ...]:
        """The value's reference, once it has been read."""
        return self._ledger.all()


def handed_out(slices: Iterable[Slice]) -> tuple[Source, ...]:
    """Everything a set of slices handed out, in order, once each."""
    ledger = _Ledger()
    for s in slices:
        for source in s.handed_out():
            ledger.note(source)
    return ledger.all()

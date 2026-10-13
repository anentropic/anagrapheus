"""Prompt assembly: a role reads only its grant, and a context knows where it came from."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING

import pytest

from anagrapheus.assembly import (
    AssemblyError,
    BeliefSlice,
    Budget,
    BudgetExceeded,
    ChronicleSlice,
    EventRef,
    Given,
    LogSlice,
    MislabelledPart,
    NotGranted,
    Part,
    Register,
    StaleRead,
    UncitedSource,
    assemble,
    render_labelled,
)
from anagrapheus.beliefs import FOLD_NAME, StateRef
from anagrapheus.chronicle import Anchor, Section, SQLiteChronicleStore
from anagrapheus.timeline import (
    DomainEvent,
    EventReader,
    PinLedger,
    Projector,
    UpcastRegistry,
    is_bookkeeping,
    pin_key,
)
from tests.beliefs.conftest import Rig, build_rig

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Sequence
    from pathlib import Path

    from anagrapheus.timeline import Envelope


@pytest.fixture
def rig(tmp_path: Path) -> Iterator[Rig]:
    rig = build_rig(tmp_path / "assembly.db")
    yield rig
    rig.store.close()


def _beliefs(rig: Rig, *, visible: frozenset[str]) -> BeliefSlice:
    return BeliefSlice(
        rig.core,
        visible=visible.__contains__,
        upto_seq=rig.store.last_seq(),
        folded_through=lambda: rig.projector.position(FOLD_NAME),
    )


def _log(rig: Rig, admit: Callable[[Envelope], bool] | None = None) -> LogSlice:
    reader = EventReader(rig.store, UpcastRegistry(), rig.types)
    return LogSlice(reader, upto_seq=rig.store.last_seq(), admit=admit)


@dataclass(frozen=True)
class Narrating:
    """The Narrator's whole grant under R24: the contract, and nothing else."""

    contract: Given[str]


@dataclass(frozen=True)
class Thinking:
    beliefs: BeliefSlice


def test_a_role_cannot_read_a_scope_its_grant_does_not_hold(rig: Rig):
    rig.believer("secret").revise("location", (uuid.uuid7(),), "London")
    grant = Thinking(_beliefs(rig, visible=frozenset({"world"})))

    with pytest.raises(NotGranted):
        assemble(
            "thinker",
            lambda g: [Part("x", Register.considered, len(g.beliefs.scope("secret")))],
            grant,
        )


def test_a_grant_holding_a_store_is_refused(rig: Rig):
    """A store in a grant is a way round every other check, so it never gets that far."""

    @dataclass(frozen=True)
    class Leaky:
        contract: Given[str]
        store: object

    with pytest.raises(TypeError, match="not a slice"):
        assemble("narrator", lambda _g: [], Leaky(Given("contract", "c"), rig.core))


def test_the_narrator_firewall_holds_by_what_the_grant_holds():
    """R24: the contract and nothing else, and the context says so."""
    grant = Narrating(Given("contract", "Describe the harbour at dusk."))

    context = assemble(
        "narrator",
        lambda g: [Part("contract", Register.binding, g.contract.value, sources=(g.contract.ref,))],
        grant,
    )

    assert context.read == (grant.contract.ref,)
    assert context.upto_seq is None


def test_a_context_records_exactly_what_was_read(rig: Rig):
    alice, bob = uuid.uuid7(), uuid.uuid7()
    seen = rig.believer("world").revise("location", (alice,), "London")
    rig.believer("world").revise("location", (bob,), "Paris")
    grant = Thinking(_beliefs(rig, visible=frozenset({"world"})))
    belief_id = rig.predicates.belief_id("location", alice)

    def assembler(g: Thinking) -> list[Part]:
        state = g.beliefs.belief("world", belief_id)
        assert state is not None
        return [Part("context", Register.considered, state.value, sources=(StateRef.of(state),))]

    context = assemble("thinker", assembler, grant)

    assert context.read == (StateRef.of(seen),), "the belief about bob was never handed out"


def test_a_part_cannot_cite_what_its_grant_never_handed_out(rig: Rig):
    grant = Thinking(_beliefs(rig, visible=frozenset({"world"})))
    invented = StateRef("world", "v1:crm:location(x)", uuid.uuid7())

    with pytest.raises(UncitedSource):
        assemble(
            "thinker",
            lambda _g: [Part("context", Register.considered, "?", sources=(invented,))],
            grant,
        )


def test_a_belief_slice_refuses_to_read_across_a_moving_fold(rig: Rig):
    """Half a context from before an event and half from after it describes no moment."""
    slice_ = _beliefs(rig, visible=frozenset({"world"}))
    rig.believer("world").revise("location", (uuid.uuid7(),), "London")

    with pytest.raises(StaleRead):
        slice_.scope("world")


def _over(rig: Rig, *, upto_seq: int | None = None) -> BeliefSlice:
    return BeliefSlice.over(
        rig.fold,
        rig.projector,
        EventReader(rig.store, UpcastRegistry(), rig.types),
        visible=frozenset({"world"}).__contains__,
        upto_seq=rig.store.last_seq() if upto_seq is None else upto_seq,
    )


def _pin_and_commit(rig: Rig) -> int:
    """Draw one pin and promote it into the log, as a finished unit of work does."""
    ledger = PinLedger(rig.store, rig.types)
    ledger.resolve(
        pin_key("app", {"q": "anything"}), kind="draw", effect=lambda: "drawn", interpret=str
    )
    return ledger.commit()


def test_the_belief_fold_claims_belief_events_and_nothing_else(rig: Rig):
    rig.commit(rig.asserted("location", uuid.uuid7()))
    _pin_and_commit(rig)
    envelopes = [event.envelope for event in rig.store.read()]

    assert [rig.fold.claims(env) for env in envelopes] == [True, False]


@pytest.mark.parametrize("durable", [False, True], ids=["fold-owned", "store-owned"])
def test_promoted_pins_do_not_make_a_belief_read_stale(tmp_path: Path, durable: bool):
    """A pin lands in the log after the fold caught up, and no belief comes from it."""
    rig = build_rig(tmp_path / "pins.db", durable=durable)
    try:
        held = rig.believer("world").revise("location", (uuid.uuid7(),), "London")
        last = _pin_and_commit(rig)
        assert rig.projector.position(FOLD_NAME) < last, "the fold has not seen the pin"

        assert _over(rig, upto_seq=last).scope("world") == [held]
    finally:
        rig.store.close()


def test_an_append_no_belief_comes_from_does_not_tear_a_slice(rig: Rig):
    held = rig.believer("world").revise("location", (uuid.uuid7(),), "London")
    slice_ = _over(rig)
    _pin_and_commit(rig)
    rig.projector.catch_up()

    assert slice_.scope("world") == [held], "the fold is ahead, by nothing it reads"


def test_a_belief_event_the_fold_has_not_reached_makes_a_read_stale(rig: Rig):
    rig.believer("world").revise("location", (uuid.uuid7(),), "London")
    rig.append(rig.asserted("location", uuid.uuid7(), scope="world"))
    _pin_and_commit(rig)

    with pytest.raises(StaleRead, match="changes beliefs"):
        _over(rig).scope("world")


def test_a_belief_event_folded_past_the_slice_makes_a_read_stale(rig: Rig):
    slice_ = _over(rig)
    _pin_and_commit(rig)
    rig.believer("world").revise("location", (uuid.uuid7(),), "London")

    with pytest.raises(StaleRead, match="changes beliefs"):
        slice_.scope("world")


def test_a_slice_past_the_end_of_the_log_refuses(rig: Rig):
    """Nothing is there yet to find, and a belief event appended there later would pass."""
    rig.believer("world").revise("location", (uuid.uuid7(),), "London")

    with pytest.raises(StaleRead, match="the log ends before"):
        _over(rig, upto_seq=rig.store.last_seq() + 1).scope("world")


def test_an_ignored_event_at_the_slice_position_is_never_upcast(rig: Rig):
    """Whether the log reaches the slice is asked of the store, not of an upcast read."""

    class Noted(DomainEvent, frozen=True):
        text: str

    class NotedV2(DomainEvent, frozen=True):
        text: str
        by: str

    held = rig.believer("world").revise("location", (uuid.uuid7(),), "London")
    rig.types.event("demo", "Noted")(Noted)
    last = rig.append(rig.types.pending(Noted(text="an aside")))
    rig.types.event("demo", "Noted", version=2)(NotedV2)  # and no upcast from version 1
    assert rig.projector.position(FOLD_NAME) < last, "the fold has not seen the aside"

    assert _over(rig, upto_seq=last).scope("world") == [held]


def test_a_slice_over_a_fold_the_projector_does_not_run_is_refused(rig: Rig):
    """Its position would be asked of something that does not know it, so not even once."""
    reader = EventReader(rig.store, UpcastRegistry(), rig.types)

    with pytest.raises(KeyError):
        BeliefSlice.over(
            rig.fold, Projector(rig.store, reader, []), reader, visible=bool, upto_seq=0
        )


def test_a_log_slice_never_hands_out_what_admit_refuses(rig: Rig):
    """The shape of the demo's leak: a private event kept out of a shared prompt."""
    rig.commit(rig.asserted("location", uuid.uuid7()))
    rig.commit(rig.asserted("location", uuid.uuid7(), scope="secret"))

    def revisions(env: Envelope) -> bool:
        return env.type == "BeliefRevised"

    def none(_env: Envelope) -> bool:
        return False

    log = _log(rig, revisions)
    nothing = _log(rig, none)

    assert len(log.events()) == 2
    assert nothing.events() == [] and nothing.handed_out() == ()
    assert all(isinstance(s, EventRef) for s in log.handed_out())


def test_a_grant_must_be_frozen():
    """Otherwise the assembler could swap a slice for a store after the check."""

    @dataclass
    class Mutable:
        contract: Given[str]

    with pytest.raises(TypeError, match="not frozen"):
        assemble("narrator", lambda _g: [], Mutable(Given("contract", "c")))


def test_a_grant_is_read_for_one_assembly_only():
    """A reused grant would carry one call's reads into the next call's context."""
    grant = Narrating(Given("contract", "c"))
    assemble("narrator", lambda g: [Part("c", Register.binding, g.contract.value)], grant)

    with pytest.raises(AssemblyError, match="already read"):
        assemble("narrator", lambda _g: [], grant)


def test_a_refused_event_is_never_decoded(rig: Rig):
    """
    A log slice filters before upcasting and decoding.

    Read through a registry that does not know the library's belief events, every one
    of them would fail to decode. Refused by `admit`, none of them is touched.
    """
    from anagrapheus.timeline import EventTypeRegistry

    rig.commit(rig.asserted("location", uuid.uuid7()))
    stranger = EventReader(rig.store, UpcastRegistry(), EventTypeRegistry())

    def none(_env: Envelope) -> bool:
        return False

    assert LogSlice(stranger, upto_seq=rig.store.last_seq(), admit=none).events() == []


def test_slices_at_two_positions_are_refused(rig: Rig):
    @dataclass(frozen=True)
    class Torn:
        before: LogSlice
        after: LogSlice

    before = _log(rig)
    rig.commit(rig.asserted("location", uuid.uuid7()))

    with pytest.raises(AssemblyError, match="different positions"):
        assemble("thinker", lambda _g: [], Torn(before, _log(rig)))


# -- prose is labelled as prose --------------------------------------------------------


@dataclass(frozen=True)
class Recalling:
    chronicle: ChronicleSlice


def _chronicle(rig: Rig) -> ChronicleSlice:
    rig.commit(rig.asserted("location", uuid.uuid7()))
    store = SQLiteChronicleStore(rig.store.connection)
    store.append(Section(Anchor("s1", 1, 1, 1, None), "Alice went to London."))
    return ChronicleSlice(store, upto_seq=rig.store.last_seq())


def _cite(sections: Sequence[Section], register: Register) -> list[Part]:
    from anagrapheus.assembly import SectionRef

    return [
        Part(
            "recall",
            register,
            s.prose,
            sources=(SectionRef(s.anchor.section_id, s.anchor.from_seq, s.anchor.to_seq),),
        )
        for s in sections
    ]


def test_chronicle_prose_cited_as_canon_is_refused(rig: Rig):
    """R61 clause 4: model-written prose is advisory, and a prompt must not say otherwise."""
    chronicle = _chronicle(rig)

    def again() -> Recalling:
        return Recalling(
            ChronicleSlice(SQLiteChronicleStore(rig.store.connection), upto_seq=chronicle.upto_seq)
        )

    with pytest.raises(MislabelledPart, match="cites chronicle prose"):
        assemble("thinker", lambda g: _cite(g.chronicle.sections(), Register.considered), again())

    context = assemble(
        "thinker", lambda g: _cite(g.chronicle.sections(), Register.generated), again()
    )
    assert [p.register for p in context.parts] == [Register.generated]


def test_prose_cannot_lose_its_label_by_losing_its_citation(rig: Rig):
    """Read a section and show it uncited, and it is still refused: it was read."""
    grant = Recalling(_chronicle(rig))

    def uncited(g: Recalling) -> list[Part]:
        return [Part("recall", Register.considered, s.prose) for s in g.chronicle.sections()]

    with pytest.raises(MislabelledPart, match="no generated part cites"):
        assemble("thinker", uncited, grant)


# -- the budget ------------------------------------------------------------------------


def _parts() -> list[Part]:
    return [
        Part("floor", Register.binding, "aaaa"),
        Part("arc", Register.considered, "bbb", rank=2),
        Part("bias", Register.considered, "cc", rank=1),
        Part("recap", Register.generated, "dd"),
        Part("skill", Register.craft, "e"),
    ]


def _size(part: Part) -> int:
    assert isinstance(part.content, str)
    return len(part.content)


def test_the_budget_sheds_by_register_then_rank_and_says_what_it_shed():
    # Twelve against eight: the considered parts go first, the lower rank before the
    # higher, and the prose and the craft survive because that was enough.
    kept, shed = Budget(total=8, count=_size).fit(_parts())
    assert [p.slot for p in kept] == ["floor", "recap", "skill"]
    assert [p.slot for p in shed] == ["arc", "bias"]

    # Tighter still, and the prose goes next.
    kept, shed = Budget(total=6, count=_size).fit(_parts())
    assert [p.slot for p in kept] == ["floor", "skill"]


def test_the_budget_never_sheds_a_binding_part():
    with pytest.raises(BudgetExceeded):
        Budget(total=3, count=_size).fit(_parts())
    with pytest.raises(ValueError, match="never shed binding"):
        Budget(total=3, count=_size, order=(Register.binding,))


def test_a_shed_part_is_in_the_context_as_not_shown():
    @dataclass(frozen=True)
    class Nothing:
        contract: Given[str]

    context = assemble(
        "thinker", lambda _g: _parts(), Nothing(Given("c", "")), budget=Budget(8, _size)
    )

    assert [p.slot for p in context.shed] == ["arc", "bias"]
    assert "cc" not in render_labelled(context)


# -- determinism and the default renderer ---------------------------------------------


def test_the_same_position_assembles_the_same_context(rig: Rig):
    """The contract a replay rests on: same log, same position, same parts and sources."""
    for n in range(3):
        rig.believer("world").revise("location", (uuid.UUID(int=n + 1),), f"city {n}")

    def assembler(g: Thinking) -> list[Part]:
        return [
            Part("context", Register.considered, s.value, sources=(StateRef.of(s),))
            for s in sorted(g.beliefs.scope("world"), key=lambda s: s.belief_id)
        ]

    first = assemble("thinker", assembler, Thinking(_beliefs(rig, visible=frozenset({"world"}))))
    second = assemble("thinker", assembler, Thinking(_beliefs(rig, visible=frozenset({"world"}))))

    assert first.describe() == second.describe()
    assert first.upto_seq == rig.store.last_seq()


def test_the_default_renderer_labels_each_register():
    @dataclass(frozen=True)
    class Nothing:
        contract: Given[str]

    text = render_labelled(assemble("thinker", lambda _g: _parts(), Nothing(Given("c", ""))))

    assert text.index("MUST HOLD") < text.index("CONSIDER") < text.index("CRAFT")
    assert "advisory prose, not canon" in text


def test_a_log_slice_can_leave_the_pin_ledger_out(rig: Rig):
    """The stock bookkeeping predicate works as a log slice's admit."""
    rig.commit(rig.asserted("location", uuid.uuid7()))
    _pin_and_commit(rig)

    everything = _log(rig).events()
    content = _log(rig, admit=lambda e: not is_bookkeeping(e)).events()

    assert [stored.envelope.type for stored, _ in everything][-1] == "PinPromoted"
    assert [stored.seq for stored, _ in content] == [everything[0][0].seq]

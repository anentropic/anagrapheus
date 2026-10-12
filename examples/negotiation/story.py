"""
The demo, as a story that runs: four rounds, two agents, and no model on replay.

``python -m examples.negotiation`` prints what this yields, and
``tests/test_acceptance_m8.py`` asserts on the same object. So the demo **is** what is
tested and cannot drift into a script that describes behaviour the code no longer has —
which is the ordinary fate of a README's worked example, and the reason this is a module
rather than a section of prose.

**The scene.** One clause, ``fisheries``. The UK will privately concede it and says
otherwise. The EU wants a settlement. Three rounds:

1. Both open by holding firm, neither having any read of the other. Then the UK lets
   something slip — **and believes nobody heard it.** The EU believes both sides heard.
   That is doc 24 §1's guard and courier: the two sides now disagree about *access*, not
   about the world, and no first-order record anywhere can express the disagreement.
2. The EU, holding a derived model that says the UK will move, holds firm and extracts.
   The UK, believing the EU learned nothing, holds firm too — **and is wrong**, at a
   level its own beliefs cannot represent.
3. The EU lets something slip in turn, this time overheard by a UK that knows it was.
4. The UK, its model of the EU now reading ``never``, concedes. The EU — which knows
   the UK was always going to — holds firm and takes the clause.

Round 4 is the one worth the milestone. The UK reads the *world* correctly — the EU
really will not move — and settles because of it, while holding a **false belief about
what the EU knows** from round one onwards: it thinks its slip went unheard, and it did
not. Doc 24 §5's second-order point, in the story rather than only in a unit test: a
party right about the world can be wrong about a belief, and the error lives at a level
no first-order record can reach.

**Be precise about how long the two levels stay apart**, because it is easy to overclaim
and this docstring did. The *measured* mismatch — what the EU concludes about the UK
against what the UK imagines it concludes — is wide after round one and closes in round
two, when the UK states its position publicly and last-evidence-wins re-buries the leak.
What persists throughout is the **false access-belief** the mismatch came from. Those
are two claims, only the second holds all the way, and :class:`Mismatch` records the
first round by round so the difference is visible rather than asserted.

**A public statement re-buries a leak**, and that falls out rather than being decided:
the fold is last-evidence-wins, so a party that states a position after letting
something slip is modelled on the statement again. Doc 24 §4 is explicit that this is
allowed — a model of another mind is *"deliberately best-effort"*, and the core's
revision operator stays out of it — but it is why a leak has to be acted on in the round
after it lands, and why there are four rounds rather than three.

**Everything nondeterministic is pinned**, including the chronicle's consolidations and
the archive lookup, so :func:`negotiate` re-run against
:class:`~anagrapheus.timeline.ReplayPins` reaches no model at all — on a *fresh store*,
not the one it recorded into. That is a stronger claim than replaying in place, and it
constrains the prompts: a prompt containing a UUID would key differently on every run
and every pin would miss. See :class:`RoundSummary` for where that bites.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from examples.negotiation.agent import Negotiator
from examples.negotiation.domain import ClauseTabled, PositionStated, RoundClosed
from examples.negotiation.lineup import Lineup
from examples.negotiation.nesting import belief_of
from examples.negotiation.rounds import Leak, play_round
from examples.negotiation.scopes import held
from examples.negotiation.table import Table
from pydantic_ai import Agent

from anagrapheus.chronicle import Consolidation
from anagrapheus.provider import PinnedModel
from anagrapheus.timeline import PinLedger

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence
    from pathlib import Path

    from examples.negotiation.rounds import Round
    from pydantic_ai.models import Model

    from anagrapheus.timeline import StoredEvent
    from anagrapheus.timeline.pins import PinKey, PinResolver

__all__ = [
    "ARCHIVE",
    "CLAUSE",
    "Mismatch",
    "Negotiation",
    "RoundSummary",
    "Step",
    "negotiate",
]

#: The clause under negotiation. One, because the demo is about two minds rather than
#: about a treaty, and a second clause would multiply the rounds without adding a case.
CLAUSE = "fisheries"

#: What the archive says, reached through a pinned tool. A dict here; a fetch in a real
#: consumer, which is the version the pin is for.
ARCHIVE = {CLAUSE: "settled 60/40 in favour of the coastal state at the last summit"}


@dataclass(frozen=True, slots=True)
class Step:
    """One line of the story, for a reader. The assertions read the objects instead."""

    label: str
    detail: str


@dataclass(frozen=True, slots=True)
class Negotiation:
    """What the story produced: the table it played on, and what happened."""

    table: Table
    rounds: tuple[Round, ...]
    steps: tuple[Step, ...]
    #: Where the round's draws went: a ledger on a live run, a replay source otherwise.
    pins: PinResolver
    #: Which model filled which role. Recorded so a result can be attributed to the
    #: models that produced it rather than to the ones somebody meant to pass.
    lineup: Lineup
    #: One per round: what the EU thought the UK wanted, against what the UK thought the
    #: EU thought that. The milestone's claim, measured.
    second_order: tuple[Mismatch, ...] = ()

    def commit(self) -> None:
        """
        Promote the run's pins into the log, if this run drew any.

        Until this, every draw lives in the scratch — persistent, but never canon — and
        nothing in the log records that it happened, so there is nothing for a replay to
        read. A replay run has nothing to commit and says so by having no ``commit``:
        the check is ``isinstance`` rather than ``hasattr`` because a silently skipped
        commit on a live run would produce a log that replays to zero pins and looks
        like a passing test.
        """
        if isinstance(self.pins, PinLedger):
            self.pins.commit()


class RoundSummary:
    """
    The chronicle's summarizer for a negotiation — bound to its ledger *late*.

    **A real ordering knot in the composition root, named rather than worked around.**
    :meth:`~examples.negotiation.table.Table.open` constructs the fold, the summarizer
    draws through the same :class:`~anagrapheus.timeline.PinLedger` as the agents, and
    that ledger needs the store the table has not opened yet. Every other fold in this
    library is pure and has no such dependency; the generative one has, and the knot is
    what the fold that spends money costs. So the resolver arrives afterwards, and
    drawing before it does raises rather than quietly making an unpinned call.

    **The prompt contains no identifiers, and that is a constraint rather than taste.**
    A consolidation is keyed on what the model is asked (§4c), so a prompt carrying a
    UUID — an entity id, a minted belief id, an event id — keys differently on every run
    and every pin misses. The renderer therefore reads the domain's own four events and
    skips the bookkeeping entirely. That is also the better summary: what the chronicle
    is for is what the parties did, not which rows were minted doing it.
    """

    def __init__(self, model: Model) -> None:
        self._model = model
        self._resolver: PinResolver | None = None

    def draws_through(self, resolver: PinResolver) -> None:
        """Bind the ledger. Called once, by the composition root, after the table opens."""
        self._resolver = resolver

    def __call__(self, events: Sequence[StoredEvent]) -> Consolidation:
        """Consolidate one round into a sentence, drawing through the bound ledger."""
        if self._resolver is None:
            raise RuntimeError(
                "the chronicle's summarizer was asked to consolidate before a ledger was "
                "bound: call `draws_through` after opening the table. Drawing here would "
                "make an unpinned model call, and the round would never replay."
            )
        keys: list[PinKey] = []
        told = "; ".join(line for line in map(_render, events) if line)
        output = (
            Agent(model=PinnedModel(self._model, self._resolver, on_key=keys.append))
            .run_sync(f"Summarise this round of a negotiation in one sentence: {told}")
            .output
        )
        return Consolidation(prose=output, pins=tuple(keys))


#: Who sits at the table, in seat order.
PARTIES = ("eu", "uk")


def negotiate(
    path: Path,
    *,
    models: Model | Lineup,
    archive: Mapping[str, str] | None = None,
    resolver: Callable[[Table], PinResolver] | None = None,
    observe: Callable[[Table], None] | None = None,
) -> Negotiation:
    """
    Play the whole negotiation and hand back everything it produced.

    ``models`` is one model for every role, or a
    :class:`~examples.negotiation.lineup.Lineup` choosing one per role and per party.

    ``resolver`` builds the pin source from the opened table — a
    :class:`~anagrapheus.timeline.PinLedger` for a live run, a
    :class:`~anagrapheus.timeline.ReplayPins` for a replay. It takes the table because
    both need the store, and the store is the table's. A replay in ``"record"`` mode
    with a *different* lineup is how one role is evaluated with the rest held fixed;
    see :mod:`examples.negotiation.lineup`.

    ``observe`` is handed the table as soon as it opens, before anything is said at
    it -- the place to attach an ``on_append`` listener and watch the story unfold.
    """
    lineup = models if isinstance(models, Lineup) else Lineup.of(models)
    lookup = dict(ARCHIVE if archive is None else archive)
    summary = RoundSummary(lineup.model_for("summarize"))
    # Before the table opens: a lineup naming a party that is not seated is a
    # configuration error, and refusing it here leaves no half-built database behind.
    lineup.check_parties(PARTIES)
    table = Table.open(path, *PARTIES, summarize=summary)
    try:
        if observe is not None:
            observe(table)
        return _play(table, lineup, summary, lookup, resolver)
    except BaseException:
        # A failed run hands back no `Negotiation`, so nothing else holds the table to
        # close it.
        table.close()
        raise


def _play(
    table: Table,
    lineup: Lineup,
    summary: RoundSummary,
    lookup: Mapping[str, str],
    resolver: Callable[[Table], PinResolver] | None,
) -> Negotiation:
    """The negotiation itself, on a table :func:`negotiate` opened and owns."""
    pins = PinLedger(table.store, table.types) if resolver is None else resolver(table)
    summary.draws_through(pins)

    clause = table.table_clause(CLAUSE)
    steps = [
        Step("lineup", lineup.describe(table.parties)),
        Step("tabled", f"{CLAUSE} is on the table"),
    ]

    # -- what each side actually wants, before anyone says anything --------------------
    #
    # Seeded by the table, which is omniscient by necessity: somebody has to give both
    # sides something to want. Neither agent can read the other's row.
    tabled = table.tabled_at[CLAUSE]
    table.believe(held("eu"), "position", (table.seats["eu"], clause), "settle", tabled)
    table.believe(held("uk"), "position", (table.seats["uk"], clause), "concede", tabled)
    table.projector.catch_up()
    steps.append(Step("seeded", "the EU wants a settlement; the UK will privately concede"))

    agents = {
        party: Negotiator(
            lineup.model_for("propose", party),
            pins,
            view=table.view(party),
            archive=lambda c: lookup.get(c, _NO_PRECEDENT),
            update=lineup.model_for("update", party),
        )
        for party in ("eu", "uk")
    }

    wanted = table.predicates.belief_id("position", table.seats["uk"], clause)
    played: list[Round] = []
    errors: list[Mismatch] = []
    for number, leak in enumerate(_LEAKS, start=1):
        round_played = play_round(table, agents, clause=CLAUSE, number=number, leak=leak)
        played.append(round_played)
        mismatch = _second_order(table, wanted, after=number)
        errors.append(mismatch)
        for turn in round_played.turns:
            steps.append(
                Step(
                    f"round {number} / {turn.party}",
                    f"offered {'concede' if turn.offer.concede else 'hold firm'} "
                    f"({turn.offer.because}); concluded "
                    f"{turn.reading.predicate}({turn.reading.about}) = {turn.reading.value}",
                )
            )
        if leak is not None:
            steps.append(Step(f"round {number} / leak", _who_heard(leak)))
        steps.append(Step(f"round {number} / depth 2", str(mismatch)))

    # -- what each side ended up thinking the other believes ---------------------------
    #
    # Derived here and discarded with the loop variable. Nothing wrote it, nothing can.
    for holder, other in (("eu", "uk"), ("uk", "eu")):
        modelled = table.model_held_by(holder, other)
        steps.append(
            Step(
                f"{holder}'s model of {other}",
                f"{len(modelled.beliefs)} belief(s) over {len(modelled.over)} event(s) — "
                f"derived, never stored",
            )
        )

    return Negotiation(
        table=table,
        rounds=tuple(played),
        steps=tuple(steps),
        pins=pins,
        lineup=lineup,
        second_order=tuple(errors),
    )


#: What the archive says when it has nothing. A sentence rather than an empty string,
#: because a tool returning "" reads to a model as a broken tool.
_NO_PRECEDENT = "no precedent on record"

#: Round by round: who lets something slip, and who believes it was heard.
#:
#: Round 1's asymmetry is the whole demo. `{"eu": ("eu", "uk")}` and no `uk` key at all:
#: the EU believes both sides heard the remark, and the UK believes **nobody** did --
#: not even itself. R27 allows exactly that, and calls the divergence the engine.
#:
#: Round 3 reverses it with both sides agreeing, so the contrast is visible: an
#: overheard remark that everyone knows was overheard changes the world, and one that
#: the speaker thinks went unheard changes what the speaker can predict.
_LEAKS: tuple[Leak | None, ...] = (
    Leak(
        speaker="uk",
        overheard_by="eu",
        clause=CLAUSE,
        suggests="concede",
        believed_by={"eu": ("eu", "uk")},
    ),
    None,
    Leak(
        speaker="eu",
        overheard_by="uk",
        clause=CLAUSE,
        suggests="never",
        believed_by={"eu": ("eu", "uk"), "uk": ("eu", "uk")},
    ),
    None,
)


@dataclass(frozen=True, slots=True)
class Mismatch:
    """
    The second-order error, after one round: two levels, and whether they agree.

    ``truly`` is what the EU's model of the UK says the UK wants — depth 1, derived from
    what the EU believes the UK let slip. ``imagined`` is what the *UK* thinks that model
    says — depth 2, derived from what the UK believes the EU witnessed, which since round
    one has been wrong.

    They are two different questions and they can differ, which is doc 24 §5's
    second-order Sally-Anne point: a party right about the world can be wrong about a
    belief, and no first-order record anywhere expresses the gap. That is not a claim
    about this implementation being clever. It is the reason R28 forbids storing the
    thing at all — a stored ``uk_believes_eu_believes`` row updates when the world moves
    and quietly converges on the truth, and this column would read ``False`` forever.
    """

    after_round: int
    #: Depth 1: what the EU's model of the UK holds.
    truly: str | None
    #: Depth 2: what the UK thinks the EU's model of the UK holds.
    imagined: str | None

    @property
    def wrong(self) -> bool:
        """Whether the UK is mistaken about what the EU has concluded."""
        return self.truly != self.imagined

    def __str__(self) -> str:
        verdict = "the UK is wrong about what the EU concluded" if self.wrong else "agreed"
        return f"EU reads {self.truly!r}; UK imagines {self.imagined!r} — {verdict}"


def _second_order(table: Table, wanted: str, *, after: int) -> Mismatch:
    """
    Ask both levels the same question, and keep the pair.

    Both sides go through :class:`~examples.negotiation.view.PartyView`, so neither
    reads the other's scope (R29), and both are discarded the moment this returns
    (R28) — the ``Mismatch`` holds two values, not two models.
    """
    return Mismatch(
        after_round=after,
        truly=table.model_held_by("eu", "uk").get(wanted),
        imagined=belief_of(table.view("uk"), ("eu", "uk"), wanted, interpret=table.interpret()),
    )


def _who_heard(leak: Leak) -> str:
    """
    The remark, and who each side believes heard it.

    Rendered from ``believed_by`` rather than asserted in prose, because the two can
    disagree and the disagreement is the point. A line hard-coding "the speaker thinks
    it went unheard" would go on reading correctly after the staging changed under it —
    the class of assertion note 06 §2d records catching three times.
    """
    heard = ", ".join(
        f"{holder} believes {' and '.join(leak.believed_by[holder]) or 'nobody'} heard it"
        for holder in sorted(leak.believed_by)
    )
    silent = ", ".join(
        f"{p} believes nobody heard it" for p in ("eu", "uk") if p not in leak.believed_by
    )
    return f"{leak.speaker} let slip {leak.suggests!r} — " + "; ".join(
        x for x in (heard, silent) if x
    )


def _render(event: StoredEvent) -> str:
    """
    One event as a sentence, or nothing — the minutes of the room, and only those.

    Only the domain's own events, and no identifiers — see :class:`RoundSummary`. An
    unrecognised event is skipped rather than rendered generically: a summary listing
    ``BeliefMinted`` fourteen times is noise the model then has to be told to ignore.

    **``SideRemarkOverheard`` is skipped, and that is a constraint rather than a
    choice.** A chronicle is *one* projection, and its prose is appended to every
    party's brief. There is no vantage point from which a shared summary can describe a
    private event, because a party that must not know it is one of the readers — and
    here it is the *speaker*, who believes the remark went unheard.

    Rendering it was a real defect, caught in review: from round two the UK's own brief
    read "uk let slip they would concede, overheard by eu", handing it the exact truth
    its access-belief denies and that
    :class:`~examples.negotiation.view.PartyView` raises rather than answer. R29 held on
    the belief path and broke one layer below it, in the prompt. The scripted
    counterpart reads the belief lines and ignores the prose, so nothing failed.

    What a party believes it overheard still reaches it — through
    :func:`~examples.negotiation.rounds._observed`, which is built per party from that
    party's own beliefs. Note 06 §9a.
    """
    payload = event.payload
    match event.envelope.type:
        case ClauseTabled.__name__:
            return f"the clause {payload['title']!r} was tabled"
        case PositionStated.__name__:
            return f"{payload['party']} stated {payload['position']}"
        case RoundClosed.__name__:
            return f"round {payload['round_number']} closed"
        case _:
            return ""

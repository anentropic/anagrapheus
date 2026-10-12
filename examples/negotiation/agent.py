"""
An agent negotiating for one party: what it proposes, and what it concludes.

The demo's provider half. What makes it worth writing is not that it calls a model —
tiers 1 and 2 already prove that path — but *what it is given*: a brief assembled from
its own :class:`~examples.negotiation.view.PartyView`, so the concession it proposes
depends on what its party believes the other side actually wants, not on what the other
side said.

**Two pinned calls per party per round**, and the split is the design:

- :meth:`Negotiator.propose` **acts**. It may reach the archive through a pinned tool,
  so a replay sees the precedent the run saw rather than the precedent that is true now.
- :meth:`Negotiator.update` **reads**. A separate call, with a separate output type,
  because "what should I offer" and "what did that tell me about them" are different
  questions and folding them into one call makes the second invisible: an agent that
  only proposes never revises, and a negotiation of agents that never revise is a
  sequence of independent turns wearing a round's clothes.

**The update call cannot write a nested belief, by schema.** :class:`Reading` has a
``Literal`` predicate naming ``position`` and ``red_line`` — both first-order beliefs
about another party's *preferences*. There is no predicate here that says "what they
believe", so R28 is enforced by the output type rather than by the prompt. A model
cannot be trusted to remember a ruling; a type does not have to.

That is the other half of the M8 correction, and it is the constructive half. M8a said
what may not be stored. This says what may: R28's own exception is a belief about
another mind with no witness trail — a hunch about disposition — and it **is** an
ordinary stored first-order belief. What a party *believes* another believes stays
derived, in :mod:`examples.negotiation.nesting`, and is never written anywhere.

**The model never names a belief id.** It names a party, a clause and a value; the
application mints the id from them. Doc 00 §6a's rule, and the reason the schema carries
``because`` as a field the belief never sees: the prose is for the transcript, and a
belief whose value was prose would be a belief nothing could ever compare.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from examples.negotiation import domain
from examples.negotiation.nesting import reconstruct
from examples.negotiation.scopes import held, stated
from pydantic import BaseModel, Field
from pydantic_ai import Agent, ModelRetry

from anagrapheus.assembly import Given, Part, Register, SectionRef, assemble
from anagrapheus.beliefs import StateRef
from anagrapheus.provider import PinnedModel, pinned_tool

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine

    from doxastica import BeliefState
    from examples.negotiation.table import Table
    from examples.negotiation.view import PartyView
    from pydantic_ai.models import Model
    from pydantic_ai.tools import RunContext

    from anagrapheus.assembly import Context
    from anagrapheus.timeline.pins import PinResolver

__all__ = [
    "BottomLine",
    "Negotiator",
    "Offer",
    "Reading",
    "UnrecordableOutput",
    "Proposing",
    "Updating",
    "brief_for",
    "brief_parts",
    "proposing",
    "reading",
    "render_proposing",
    "render_reading",
    "precedent_tool",
    "record_offer",
    "check_reading",
    "record_reading",
]


class Offer(BaseModel):
    """What the agent proposes, and the belief it rests on."""

    clause: str
    concede: bool
    because: str


#: What a bottom line can be. The same words a position is stated and leaked in, so a
#: reading compares with everything else the belief layer holds about a party -- which
#: is why it is the domain's vocabulary rather than this module's, and why the registry
#: refuses anything else on write.
type BottomLine = domain.Disposition


class Reading(BaseModel):
    """
    What a party concludes about another after a round — first-order, by schema.

    ``predicate`` is a ``Literal`` with one member, ``red_line``: a disposition, held
    with no witness trail, which is R28's exception and the one belief about another
    party that may be stored. Nothing here can say "what they believe": R28 forbids
    storing that, and this type is where the forbidding happens, because a prompt asking
    a model not to is a request and a schema is a wall.

    **It also allowed ``position``, and that was a second gap.** ``position`` is a
    legitimate first-order belief, but nothing reads a stored ``position`` about the
    other side — the brief derives that line instead — so a conclusion written there
    reached no decision. The scripted model always chose ``red_line``; the first real
    model to drive this harness chose ``position`` for seven of its eight conclusions,
    because the prompt asked "what they actually want", which is a position. Note 06
    §11. The prompt now asks for what the brief reads, and the schema allows nothing
    else.

    The fields are the *arguments* to a belief id, never the id itself — §6a's
    mint-from-structured-data rule. ``because`` is the exception that proves it: it is
    the model's prose, it goes to the transcript, and it never reaches a belief value.
    A belief whose value was a sentence is a belief no divergence query can compare, and
    the whole belief layer exists to compare them.

    **And ``value`` was a bare ``str``, so a sentence is what it got.** The docstring
    above said so and the type did not: asked for a bottom line, Claude Haiku 5.5 wrote
    *"unknown: no confirmed furthest movement; 60/40 coastal-state split is a reference
    range, not a stated limit"* -- a fine sentence, stored as a belief value, read back
    into the next brief, and compared with nothing. The scripted model and the recorded
    real route had only ever used three words, so the vocabulary existed in the data and
    nowhere else. It is :data:`BottomLine` now, described in the schema the model is
    shown, and a value outside it is sent back like a misnamed clause.
    """

    #: A seated party, by the name the brief used.
    about: str
    #: A tabled clause, by title.
    clause: str
    predicate: Literal["red_line"]
    value: BottomLine = Field(
        description=(
            "Their bottom line, as one of three words. 'concede': they will give way on "
            "this clause. 'hold_firm': they will not move yet, but have not ruled it "
            "out. 'never': they will not give way at all. Put any nuance in `because`."
        )
    )
    #: Why, in the model's words. For the reader, not for the store.
    because: str


class UnrecordableOutput(ValueError):
    """
    A model's structured output named something the round cannot record.

    A party or clause that is not at the table, or a clause other than the one under
    negotiation. Raised rather than allowed to fall through as a ``KeyError`` — or,
    worse, silently ignored — because the three read very differently at three in the
    morning. A ``KeyError`` on ``seats`` looks like a bug in the round loop; a dropped
    field looks like nothing at all; and this is a model naming something that does not
    fit, which is an ordinary and expected thing for a model to do and the
    application's job to catch.

    **The silent case was the real one, and review found it.** ``record_offer`` took
    the round's clause and never looked at ``offer.clause``, so a model proposing on a
    different clause produced a ``Turn`` whose offer disagreed with the event
    persisted for it — a disagreement nothing would ever surface.
    """

    def __init__(self, what: str, named: str, expected: tuple[str, ...]) -> None:
        super().__init__(
            f"the model named {what} {named!r}, which this round cannot record. "
            f"Expected one of: {', '.join(expected) or '(none)'}."
        )
        self.what = what
        self.named = named
        self.expected = expected


def brief_for(view: PartyView, other: str, clause: str) -> str:
    """
    What ``view``'s party knows going into a round about ``other``'s position.

    Four lines from four sources, and the difference between them is the whole brief:
    what we want, what they said, what we derive they actually want, and what we
    concluded about their bottom line last round. A brief built from one source would
    read identically whether or not we had any model of them.

    **The fourth line closes a loop M8b left open.** The update call writes a
    ``red_line`` reading every round, and until note 06 §11 nothing read it back — so
    the second pinned call ran, replayed, and could not affect a single offer. A model
    swapped into that role would have changed nothing measurable.

    **The third line is derived, not read.** Since M8 there is no
    ``party:{a}:model-of:{b}`` scope to query — R28 forbids storing it — so it is
    reconstructed from this party's access-beliefs at the moment the brief is assembled
    and discarded with the brief. Doc 24 calls that abduction on demand applied to
    minds: never materialise the whole tower of what everyone could think about
    everyone, reconstruct the one nested query actually asked.

    Everything here comes through the :class:`~examples.negotiation.view.PartyView`, so
    the brief physically cannot include the other side's private beliefs. That is R29
    holding by construction rather than by care — and it matters most here, because the
    brief is exactly where a peek would be invisible and devastating: an agent handed
    the truth about its counterpart looks like an agent that reads them well.

    Each line is looked up by its **exact belief id**. Taking the first belief in a
    scope reads correctly while a scope holds one thing and starts answering with an
    unrelated clause the moment it holds two, at which point the agent negotiates
    confidently about the wrong proposition — the kind of wrong that never raises. It
    has happened here once; note 06 §5b records it.
    """
    return "\n".join(str(part.content) for part in brief_parts(view, other, clause))


def brief_parts(view: PartyView, other: str, clause: str) -> list[Part]:
    """
    :func:`brief_for` as parts: one per line, each citing the belief it read.

    All ``considered``. A brief is what this party believes, and believing is not
    binding: the model may weigh any line against the others. The modelled line cites
    nothing, because it is derived from many beliefs rather than read from one; the
    view still records every belief the derivation read.
    """
    wanted = view.position_id(view.party, clause)
    theirs = view.position_id(other, clause)

    ours = view.own_belief(wanted)
    said = view.public_belief(other, theirs)
    modelled = reconstruct(
        view,
        (other,),
        interpret=view.interpret,
    ).get(theirs)
    # Our own scope, so readable; a belief *about* them, so R28's exception applies.
    concluded = view.own_belief(view.red_line_id(other, clause))

    def line(slot: str, text: str, state: BeliefState | None = None) -> Part:
        cites = () if state is None else (StateRef.of(state),)
        return Part(slot, Register.considered, text, sources=cites)

    return [
        line("clause", f"Clause: {clause}"),
        line("ours", f"Our position: {'unknown' if ours is None else ours.value}", ours),
        line(
            "theirs",
            f"Their stated position: {'unknown' if said is None else said.value}",
            said,
        ),
        line(
            "modelled",
            f"What we believe they actually want: {'unknown' if modelled is None else modelled}",
        ),
        line(
            "concluded",
            f"Our read of their bottom line: {'unknown' if concluded is None else concluded.value}",
            concluded,
        ),
    ]


# ---- what each call is shown -----------------------------------------------------------
#
# One grant and one assembler per pinned call. The grant is what the call may read and
# nothing else, and the context it produces knows every source it was built from (note
# 07). The renderers reproduce the prompts M8 sent byte for byte, which is the bar the
# committed recordings hold this to: a different byte is a pin miss.


@dataclass(frozen=True)
class Proposing:
    """What a party is granted to propose: its own view. Nothing else at the table."""

    view: PartyView


@dataclass(frozen=True)
class Updating:
    """What a party is granted to read the round: its view, and what it saw happen."""

    view: PartyView
    observed: Given[str]


def proposing(view: PartyView, other: str, clause: str) -> Context:
    """
    The propose call's context: the ask, the brief, and the rounds so far as summarised.

    The summaries are ``generated``: chronicle prose is advisory, and the label is
    structure now rather than a sentence in the prompt (note 07 §1).
    """

    def assembler(grant: Proposing) -> list[Part]:
        ask = Part(
            "ask",
            Register.binding,
            f"You are negotiating for {grant.view.party}. Given the brief, propose whether "
            f"to concede this clause.",
        )
        recap = [
            Part(
                "recap",
                Register.generated,
                section.prose,
                sources=(
                    SectionRef(
                        section.anchor.section_id, section.anchor.from_seq, section.anchor.to_seq
                    ),
                ),
            )
            for section in grant.view.summaries()
        ]
        return [ask, *brief_parts(grant.view, other, clause), *recap]

    return assemble("propose", assembler, Proposing(view))


def render_proposing(context: Context) -> str:
    """The propose prompt, as M8 sent it."""
    ask = _content(context, "ask")
    brief = "\n".join(_content(context, slot) for slot in _BRIEF)
    recap = [str(p.content) for p in context.parts if p.slot == "recap"]
    if not recap:
        return f"{ask}\n\n{brief}"
    prose = "\n".join(f"- {line}" for line in recap)
    return (
        f"{ask}\n\n{brief}\n\nEarlier rounds, as summarised (advisory prose, not canon):\n{prose}"
    )


def reading(view: PartyView, other: str, clause: str, *, observed: str) -> Context:
    """
    The update call's context: what this party saw, its brief, and what to conclude.

    ``observed`` is handed in rather than read, because the round loop computed it from
    the offers in hand and this party's own belief about who overheard what. It is a
    :class:`~anagrapheus.assembly.Given` with no event behind it, and the context says
    so.
    """

    def assembler(grant: Updating) -> list[Part]:
        party = grant.view.party
        return [
            Part(
                "ask",
                Register.binding,
                f"You are negotiating for {party}. This round you observed:",
            ),
            Part(
                "observed",
                Register.considered,
                grant.observed.value,
                sources=(grant.observed.ref,),
            ),
            *brief_parts(grant.view, other, clause),
            Part(
                "conclude",
                Register.binding,
                f"Record one conclusion about {other}: their bottom line, the furthest "
                f"they will move on this clause -- concede, hold_firm or never. Your "
                f"reasoning goes in `because`, not in the value.",
            ),
        ]

    return assemble("update", assembler, Updating(view, Given("observed", observed)))


def render_reading(context: Context) -> str:
    """The update prompt, as M8 sent it."""
    brief = "\n".join(_content(context, slot) for slot in _BRIEF)
    return (
        f"{_content(context, 'ask')}\n\n"
        f"{_content(context, 'observed')}\n\n"
        f"{brief}\n\n"
        f"{_content(context, 'conclude')}"
    )


_BRIEF = ("clause", "ours", "theirs", "modelled", "concluded")


def _content(context: Context, slot: str) -> str:
    return str(next(p.content for p in context.parts if p.slot == slot))


def precedent_tool(
    resolver: PinResolver, lookup: Callable[[str], str]
) -> Callable[[str], Coroutine[object, object, str]]:
    """
    The archive, as a tool the negotiator may reach for while proposing.

    ``lookup`` is a **port**: how this clause was settled at the last summit. The demo
    passes a dict-backed one, and a real consumer passes a fetch against a treaty
    database that answers differently next month.

    **The pin belongs on the port, not on the demo's stand-in.** Pinning a dict lookup
    buys nothing — it is pure, and it recomputes identically for free, which
    :mod:`~anagrapheus.provider.tools` names as a case *not* to pin. What is being
    demonstrated here is the wiring for the real one, where the archive is an external
    read and a replay must see the answer the run acted on rather than today's. Stating
    that plainly is better than contriving a nondeterministic dictionary to justify it.

    Without this, a replay reproduces what the agents *said* while re-running what they
    *did*, and the model half looks perfect throughout.
    """

    def precedent(clause: str) -> str:
        """How this clause was settled at the last summit, if it was."""
        return lookup(clause)

    return pinned_tool(resolver, precedent)


@dataclass(frozen=True, slots=True)
class _Asked:
    """What a call was about, handed to its output validator as the run's deps."""

    party: str
    clause: str


def _retry(what: str, named: str, expected: str) -> ModelRetry:
    return ModelRetry(
        f"{what} must be exactly {expected!r}, the name the brief uses, and nothing "
        f"else; this answer gave {named!r}."
    )


def _check_offer(ctx: RunContext[_Asked], offer: Offer) -> Offer:
    if offer.clause != ctx.deps.clause:
        raise _retry("clause", offer.clause, ctx.deps.clause)
    return offer


def _check_reading_named(ctx: RunContext[_Asked], reading: Reading) -> Reading:
    if reading.about != ctx.deps.party:
        raise _retry("about", reading.about, ctx.deps.party)
    if reading.clause != ctx.deps.clause:
        raise _retry("clause", reading.clause, ctx.deps.clause)
    return reading


class Negotiator:
    """
    One party's agent. Pinned, so a round replays without reaching a model.

    Constructed with a :class:`~examples.negotiation.view.PartyView` rather than the
    table. That is the whole of R29 on the reasoning path: an agent holding the table
    could read its counterpart's private scope in one line and would look better for
    it, so it is never handed one.

    **Two calls, and a model for each if you want one.** ``model`` proposes and
    ``update`` reads; ``update`` defaults to ``model``, which is what the demo did before
    roles could differ. Each gets its own
    :class:`~anagrapheus.provider.model.PinnedModel`, over one shared ledger. The pins
    cannot collide whether or not the models are the same, for the reason §4a gives for
    keying per *request*: the two calls send different tool schemas and prompts, so they
    key differently — and when the models differ, their ids differ too. See
    :mod:`examples.negotiation.lineup` for choosing them.

    **A misnamed field is sent back, once.** Both outputs name the clause, and a reading
    names the party it is about; each has exactly one right answer, which the call
    already knows. A model that writes anything else there is told what was expected
    and asked again (``ModelRetry``), and the retry is a model call like any other, so
    it is pinned and replays. The first Claude model to drive this demo wrote a
    sentence describing its offer where the clause's name goes. :func:`record_offer`
    and :func:`check_reading` still refuse what they cannot record: the retry gives a
    model the chance to fix its answer, and they are what stands if it does not.
    """

    def __init__(
        self,
        model: Model,
        resolver: PinResolver,
        *,
        view: PartyView,
        archive: Callable[[str], str] | None = None,
        update: Model | None = None,
        on_context: Callable[[Context], None] | None = None,
    ) -> None:
        # The tool is offered only when there is an archive to reach. An agent given a
        # tool that answers nothing is worse than an agent given none: it spends a turn
        # discovering that, and the discovery is pinned forever.
        tools = [] if archive is None else [precedent_tool(resolver, archive)]
        self._propose = Agent(
            model=PinnedModel(model, resolver),
            output_type=Offer,
            deps_type=_Asked,
            tools=tools,
        )
        self._propose.output_validator(_check_offer)
        self._read = Agent(
            model=PinnedModel(model if update is None else update, resolver),
            output_type=Reading,
            deps_type=_Asked,
        )
        self._read.output_validator(_check_reading_named)
        self._view = view
        self._assembled = on_context or _ignore

    @property
    def party(self) -> str:
        """Who this agent negotiates for."""
        return self._view.party

    @property
    def view(self) -> PartyView:
        """The information set this agent holds — read-only, and all it was given."""
        return self._view

    def propose(self, other: str, clause: str) -> Offer:
        """
        Read the brief and offer a move. The model call is pinned like any other.

        The chronicle's consolidated rounds come in through this party's own view, as
        ``generated`` parts — advisory prose, appended rather than interleaved, because
        a section that read like a belief would be a generative projection wearing
        canon's clothes, and note 05 exists to keep those apart.
        """
        context = proposing(self._view.settled(), other, clause)
        self._assembled(context)
        return self._propose.run_sync(render_proposing(context), deps=_Asked(other, clause)).output

    def update(self, other: str, clause: str, *, observed: str) -> Reading:
        """
        Read what just happened, and conclude one thing about ``other``.

        The second pinned call of the round. ``observed`` is what this party saw of the
        round — its own view of it, not the round's transcript, since handing an agent
        the transcript would hand it the other side's private reasoning and undo R29 one
        prompt below where :class:`~examples.negotiation.view.PartyView` defends it.
        """
        context = reading(self._view.settled(), other, clause, observed=observed)
        self._assembled(context)
        return self._read.run_sync(render_reading(context), deps=_Asked(other, clause)).output


def record_offer(
    table: Table, offer: Offer, *, party: str, clause: str, heard_by: tuple[str, ...] = ()
) -> BeliefState:
    """
    Say the offer in the room: a ``PositionStated`` event, and the belief it mints.

    Takes the table, not a view: writing is the composition root's job. A view is a
    read surface by construction, which is why an agent cannot record its own offer and
    the round loop has to do it — the same split that keeps `Table.believe`'s
    mint-and-append pairing in one place (note 06 §4).

    **The event comes first, and the belief cites it.** M7 wrote the belief against an
    unrelated ``uuid7()`` because there was no event to cite: ``PositionStated`` was
    defined and never emitted, so a stated position had no cause anywhere in the log.
    Now it does, and that matters twice over — the belief's ``source_event_id`` names
    what caused it, and the derived read in :mod:`~examples.negotiation.nesting` can
    only gate on events that exist. An offer nobody could have witnessed is an offer no
    party can form a belief about.

    ``heard_by`` is who was in the room, and each of them comes to believe each of them
    witnessed the statement. It defaults to nobody, which is why the round loop passes
    it explicitly: an offer stated to an empty room is one a *modelled* mind
    learns the content of and nothing about who else knows it, and the second rung of
    the tower goes quiet over public events. See
    :class:`~examples.negotiation.domain.PositionStated`.

    **``offer.clause`` is checked against the clause being negotiated**, and used to be
    ignored. A model proposing on some other clause had its answer recorded under this
    one, so the returned :class:`Offer` and the ``PositionStated`` written for it
    disagreed — and nothing anywhere compared them.
    """
    if offer.clause != clause:
        raise UnrecordableOutput("clause", offer.clause, (clause,))
    said = table.types.pending(
        domain.PositionStated(
            party=party,
            clause_id=table.clauses[clause],
            position=_position(offer),
            heard_by=heard_by,
        )
    )
    table.append(said)
    event_id = said.envelope.event_id
    state = table.believe(
        stated(party),
        "position",
        (table.canonical(table.seats[party]), table.canonical(table.clauses[clause])),
        _position(offer),
        event_id,
    )
    # Said in the room, so everybody in it believes everybody in it heard it. Written out
    # as ordinary access-beliefs rather than assumed, because the derived read gates on
    # beliefs and nothing else: a public statement no party believes was witnessed is a
    # statement no party can build a model from, and every derived model would come back
    # empty for a reason that looked like a bug in the gate. It is also the baseline the
    # leak's asymmetry is visible against -- everything is shared except the one thing
    # that is not. The belief names the ``PositionStated`` itself, which is the event
    # the derived read asks about.
    for holder in heard_by:
        for witness in heard_by:
            table.believe(
                held(holder),
                "witnessed",
                (table.canonical(table.seats[witness]), event_id),
                True,
                event_id,
            )
    return state


def check_reading(table: Table, reading: Reading, *, about: str, clause: str) -> None:
    """
    Whether ``reading`` is recordable, without writing anything.

    Separate from :func:`record_reading` so a round can validate **every** party's
    output before it writes **any** of it. Validating inside the write loop leaves the
    first party's conclusion persisted when the second party's is rejected, and a round
    that never closes — a half-written round is worse than a rejected one, because the
    log then says something happened that did not.

    It also checks the reading is about the party this agent was briefed on. Without it
    a model answering about *itself* is a perfectly valid ``Reading`` that gets stored
    as a belief about the wrong mind.
    """
    if reading.about != about:
        raise UnrecordableOutput("party", reading.about, (about,))
    if reading.clause != clause:
        raise UnrecordableOutput("clause", reading.clause, (clause,))
    if reading.about not in table.seats:
        raise UnrecordableOutput("party", reading.about, table.parties)
    if reading.clause not in table.clauses:
        raise UnrecordableOutput("clause", reading.clause, tuple(table.clauses))


def record_reading(
    table: Table, reading: Reading, *, party: str, premise: BeliefState
) -> BeliefState:
    """
    Write what ``party`` concluded into its own ``held`` scope, and say why.

    First-order, always — :class:`Reading` cannot express anything else. The scope is
    the concluding party's own, never the party being concluded about: a belief about
    what the UK wants lives in the EU's head, and writing it into the UK's scope would
    be the EU editing the UK's mind.

    **``premise`` is the observation this rests on, and is required.** The edge is half
    the point of taking it: a ``DERIVED_FROM`` edge from the reading to the premise,
    carried in the reading's own event, so when the premise turns out to have been a
    bluff and is retracted, ``core.get_impact(premise.state_id)`` reports exactly what it
    compromised rather than leaving a conclusion standing on a retracted fact — on this
    store and on any store rebuilt from the log. That query is doxastica's, reached
    around this library — see note 06 §7.

    The other half is **provenance**, and it is why the parameter stopped being
    optional. The reading's cause is the premise's event, so "why does the EU believe
    this" is answered by the log's ``causation_id`` with an event that really happened;
    the earlier fallback minted a bare ``uuid7()``, which answered it with one that
    never did.

    A conclusion in this domain always rests on something observed, so requiring it
    costs nothing and closes both holes at once.

    **Every entity reference is canonicalized**, for the third time in this example and
    the second time it was nearly missed: `seats` and `clauses` keep the pre-merge names,
    and a reading recorded against an absorbed id would sit in the scope answering
    nothing. Note 06 §2a.
    """
    if reading.about not in table.seats:
        raise UnrecordableOutput("party", reading.about, table.parties)
    if reading.clause not in table.clauses:
        raise UnrecordableOutput("clause", reading.clause, tuple(table.clauses))
    state = table.believe(
        held(party),
        reading.predicate,
        (
            table.canonical(table.seats[reading.about]),
            table.canonical(table.clauses[reading.clause]),
        ),
        reading.value,
        premise.source_event_id,
        # dependent -> source: `get_impact` walks against the arrows, so this is the
        # direction that makes retracting the premise report the reading. Carried in the
        # event rather than laid on the store, so a rebuilt store still reports it.
        rests_on=premise,
    )
    return state


def _position(offer: Offer) -> str:
    """The offer as the domain's vocabulary, which is what a belief compares."""
    return "concede" if offer.concede else "hold_firm"


def _ignore(_context: Context) -> None:
    """The default ``on_context``: nobody is watching."""

"""
What one party thinks another believes — derived on demand, never stored.

This module is the correction at the centre of M8. The M7 demo stored
``party:{a}:model-of:{b}`` as an ordinary scope and note 06 §2 called that *"higher-
order belief is scope naming"*. **NVM ruled against that shape a year earlier**, and
[doc 24 §1](https://github.com/anentropic/narrative-vm/blob/main/_design/v2/24-nvm-higher-order-beliefs.md)
opens by naming it *"the wrong frame"*:

> **R28 — Higher-order belief is derived, never stored.** RATIFIED 2026-06-17. *"nested
> mental-state beliefs are reconstructed on demand from access-beliefs (R27) and
> discarded — abduction-on-demand applied to minds; **no `BeliefState` ever stores 'X
> believes Y believes …'**."*

**Why storing it breaks, precisely.** The trap is not the content — a proposition may
mention another mind, and nothing about the record is special. It is the *update
trigger*. A first-order belief updates when its owner observes a **world** event; a
higher-order belief updates when its owner observes a **meta** event, a change in what
the other actor had access to. A flat stored record is driven by the wrong one. Doc 24
§1's case: the guard watches a courier stash a letter, watches the courier leave, then
watches a thief move it. The guard's own belief updates on the move. The guard's belief
about the *courier's* belief must specifically **not** — the guard saw the courier
leave first. A stored ``courier_believes: letter_in_original_spot`` sitting in the
guard's scope gets helpfully folded forward when the world changes, *"and
deception/surprise silently break."*

So the nested belief is not stored. It is rebuilt when a turn needs it, queried, and
thrown away.

**The hard constraint (R29).** The reconstruction reads only the observer's scope,
never the target's. Peeking at the target would yield the *true* answer rather than the
observer's possibly-wrong model, and false belief would be unrepresentable. Here that
is not a rule to remember: :class:`~examples.negotiation.view.PartyView` cannot reach
another party's private scope at all, so the peek has nowhere to be written.

**The seam (R50, RATIFIED 2026-07-04).** Derivation is orchestration over existing core
*reads* — ``query_scope`` and ``get_scope_at``, both reads. The fold is example-side,
*"not a `BeliefStore` op"*, and builds *"no throwaway core scope"*. Nothing in this
module writes, and a test asserts that mechanically rather than trusting the reading.

**The tower (R30).** Depth-n needs no per-level machinery: it is one operation that
recurses, each level composing one more access gate. Realised depth caps at 3, raisable
per query — a tuning knob, not an architectural commitment.

---

**One documented simplification.** Doc 24 §3 step 3 filters the derived scope through
*A's model of B's perspective lens*, carried in the portrait A holds of B. This demo
has no portraits — that is NVM's characterological frame, which the agent-core stack
does not own — so the step is absent rather than faked. What remains is the access
gate, which is the part that carries R27–R29; the lens is a fidelity refinement whose
own accuracy is still open (U22).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence
    from uuid import UUID

    from examples.negotiation.view import PartyView

    from anagrapheus.timeline import DomainEvent

__all__ = ["DEFAULT_DEPTH", "Derived", "Interpret", "belief_of", "reconstruct"]

#: R30's cap on realised depth. *"Support recursion to arbitrary depth, but cap realised
#: depth at 3, raisable per-query as a tuning knob — not an architectural commitment.
#: The default bottoms out early because neither the humans nor the LLM can use the
#: deeper rungs."* Cost, human reality (~order 4) and LLM reality (~order 3–4) all
#: bottom out around the same place.
DEFAULT_DEPTH = 3

#: How this domain reads an event as beliefs. Supplied by the application, because the
#: events are the application's (§8: the library ships the shape, the application owns
#: the name). Returns what this event would give someone who witnessed it — empty for
#: events that carry no belief at all.
#:
#: **Several, not one**, because an event can carry who *else* was there. A remark that
#: names who overheard it gives a witness both a belief about the speaker and an
#: access-belief about the third party — and that access-belief is what lets the next
#: rung of the tower gate at all. One-belief-per-event would cap the recursion at depth
#: 1 by construction, which would be a limit of this module rather than of the design.
type Interpret = Callable[[UUID, DomainEvent], Sequence[tuple[str, Any]]]


@dataclass(frozen=True, slots=True)
class Derived:
    """
    One mind as another imagines it — ephemeral by contract.

    Returned to be queried and dropped. Nothing here is canon, nothing is authoritative,
    and writing it anywhere is the mistake R28 exists to prevent — at which point it
    starts folding forward on world events and the deception it was built to represent
    quietly stops working.

    It is deliberately *not* a ``BeliefState`` and deliberately not scope-shaped: it
    cannot be handed to ``doxastica`` even by accident, because it is not the type any
    of those calls take.
    """

    #: Whose mind, as imagined by whom: ``("uk",)`` is the viewer's model of the UK;
    #: ``("uk", "eu")`` is the viewer's model of the UK's model of the viewer.
    chain: tuple[str, ...]
    #: belief id -> value, as this modelled mind would hold it.
    beliefs: Mapping[str, Any]
    #: The events the gate admitted, in log order — the *evidence*, so a caller can say
    #: why the model looks as it does rather than only what it concluded.
    over: tuple[UUID, ...]
    #: True when the depth cap stopped the recursion before it reached ``chain``'s end.
    #: Doc 24's sketch bottoms out at ``UNKNOWN``; an empty model that *says* it was
    #: truncated is that, without pretending the mind believes nothing.
    truncated: bool = False

    def get(self, belief_id: str) -> Any | None:
        """What this modelled mind holds for ``belief_id``, or ``None``."""
        return self.beliefs.get(belief_id)


def reconstruct(
    view: PartyView,
    chain: Sequence[str],
    *,
    interpret: Interpret,
    depth: int = DEFAULT_DEPTH,
) -> Derived:
    """
    Build ``chain`` as ``view``'s party imagines it, from access-beliefs.

    ``chain`` is the tower, outermost first: ``("uk",)`` is "what I think the UK
    believes"; ``("uk", "eu")`` is "what I think the UK thinks I believe". Second-order
    is not reducible to first — a party can be right about the world and wrong about
    another's belief, and the error lives at a level no first-order record can express.

    Four steps, in doc 24 §3's order, and no writes anywhere in them.
    """
    if len(chain) > depth:
        return Derived(tuple(chain), {}, (), truncated=True)

    # -- 1. what the viewer itself believes, and which events it can speak to ----------
    #
    # R29: a party can model another's belief only over events it has some belief about
    # itself. Where it is ignorant, its model of the other is correspondingly empty --
    # which is correct and realistic, not a gap to paper over.
    own = {state.belief_id: state.value for state in view.own()}
    events = dict(view.log())

    # -- 2. the access gate, and the recursion ----------------------------------------
    #
    # Each level composes one more gate onto the event subset: gate by A's belief about
    # B's access, then within that by A's-model-of-B's belief about C's access. The
    # inner levels read the *derived* scope rather than the real one, which is what
    # makes depth-n the same operation recursed rather than n mechanisms.
    admitted = _gate(view, chain[0], own, events)
    for modelled in chain[1:]:
        # The inner mind's beliefs are what the events admitted so far would give it --
        # `witnessed(...)` beliefs among them, which is what lets the next gate run at
        # all. An event carrying who-else-was-there is how a modelled mind comes to have
        # a model of a third.
        inner = _fold(admitted, events, interpret)
        admitted = _gate(view, modelled, inner, {e: events[e] for e in admitted})

    # -- 3. the lens step is absent, deliberately. See the module docstring. -----------

    # -- 4. fold, example-side ---------------------------------------------------------
    #
    # R50: not a `BeliefStore` op, and no throwaway core scope. It is a dict.
    return Derived(tuple(chain), _fold(admitted, events, interpret), tuple(admitted))


def belief_of(
    view: PartyView,
    chain: Sequence[str],
    belief_id: str,
    *,
    interpret: Interpret,
    depth: int = DEFAULT_DEPTH,
) -> Any | None:
    """
    What ``view``'s party thinks ``chain`` believes about ``belief_id``.

    Reconstructs, queries, and discards — the whole point being that nothing survives
    the call. ``None`` where the modelled mind has no belief, which is the honest answer
    for a mind the viewer thinks saw nothing relevant.
    """
    return reconstruct(view, chain, interpret=interpret, depth=depth).get(belief_id)


def _fold(
    admitted: Sequence[UUID],
    events: Mapping[UUID, DomainEvent],
    interpret: Interpret,
) -> dict[str, Any]:
    """
    What a mind holding exactly these events would believe.

    Later events supersede earlier ones on the same belief id, which is revision in the
    only form a derived view is allowed. Doc 24 §4 is explicit that the core's formal
    operator stays out of this: resolving a contradiction here would need A's model of
    B's *entrenchment*, that is a portrait-level prose hint rather than the total
    preorder the operator requires, and *"a model of another mind is deliberately
    best-effort."* AGM rigor is reserved for first-order, real-scope revision.
    """
    beliefs: dict[str, Any] = {}
    for event_id in admitted:
        for belief_id, value in interpret(event_id, events[event_id]):
            beliefs[belief_id] = value
    return beliefs


def _gate(
    view: PartyView,
    party: str,
    beliefs: Mapping[str, Any],
    events: Mapping[UUID, DomainEvent],
) -> tuple[UUID, ...]:
    """
    Which of ``events`` the holder of ``beliefs`` thinks ``party`` witnessed.

    R27: these are *ordinary first-order* beliefs about the witness set, riding the
    normal pipeline with normal stance and provenance — and **allowed to diverge from
    the true witness set**. Nothing here consults who actually saw what, and that
    refusal is the whole mechanism: a party mis-tracking who overheard a remark is a
    party that can be deceived, and correcting it against the log would delete the
    phenomenon this module exists to represent.

    Log order, so a derived model is stable between runs and diffable in a test.
    """
    admitted: list[UUID] = []
    for event_id in events:
        held = beliefs.get(view.witnessed_id(party, event_id))
        if held is True:
            admitted.append(event_id)
    return tuple(admitted)

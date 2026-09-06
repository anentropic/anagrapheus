"""
Scope naming: two scopes per party, and the one that was a mistake.

Doc 00 §6 keeps beliefs **per scope**, so *"several holders can believe contradictory
things at once and where they diverge is a query"*, and §6c calls
:func:`~anagrapheus.beliefs.divergence` *"the whole multi-agent-disagreement case"*.

Until this example, nothing exercised that. Every use in the library's own suite is
``"agent"`` against ``"world"`` — one believer measured against ground truth, which is
the degenerate case of a multi-holder design.

**Two scopes per party**, both first-order, both stored:

- ``party:{id}:held`` — what they actually want. Private.
- ``party:{id}:stated`` — what they have said in the room. Public.

``own_gap(uk)`` — the distance between the two — is the query nobody but that party can
ask, because nobody else has their ``held`` scope. That asymmetry is what makes the
domain strategic rather than merely multi-party.

**There was a third, and removing it is the point of M8.** M7 added
``party:{a}:model-of:{b}`` — what one party believes another holds — and claimed
*"higher-order belief is scope naming"*. NVM had already ruled that shape out:

> **R28 — Higher-order belief is derived, never stored.** RATIFIED 2026-06-17. *"no
> `BeliefState` ever stores 'X believes Y believes …'."*

A stored nested record updates on the wrong event class — world events fold it forward
when it must be gated by A's model of B's **access** — so deception and surprise break
silently. It now lives in :mod:`examples.negotiation.nesting`, derived on demand from
access-beliefs and discarded, which is what R27–R30 require.

**R28 does carry an exception, and M7 sat inside it**, which is worth stating plainly
rather than claiming more of a mistake than there was: a belief about another mind with
*no witness trail* — a hunch about disposition, *"A thinks C resents her"* — **is** a
stored first-order belief in A's scope. ``position(uk, fisheries)`` is a belief about
the UK's *preference*, not about the UK's belief, so the rows M7 wrote were legitimate.
What was wrong was calling them higher-order, and building the demo so that the moment a
party formed a belief about another's *belief* it would have kept storing it.

**Why this convention lives here and not in the library.** §6b argues the predicate
registry belongs in code because it is *schema*. Scope naming is not schema — it is a
modelling choice about how many minds an application has and how they nest, which the
library cannot know. So the library ships scopes; the application names them.

§6a blesses the collision this relies on: revision history keys on the belief id alone
and is cross-scope, and *"for divergence queries that collision is the point."* Two
scopes holding ``position(uk, fisheries)`` differently is exactly the intended shape.
"""

from __future__ import annotations

__all__ = ["held", "party_of", "stated"]


def held(party: str) -> str:
    """What ``party`` actually wants. Private to them."""
    return f"party:{party}:held"


def stated(party: str) -> str:
    """What ``party`` has said publicly. Visible to everyone."""
    return f"party:{party}:stated"


def party_of(scope: str) -> str:
    """
    The party a scope belongs to.

    Needed because a re-key has to visit *every* scope a merged entity appears in, and
    the scopes are not enumerable from the belief store — only from the parties. See
    :mod:`examples.negotiation.rekeying`.
    """
    return scope.split(":")[1]

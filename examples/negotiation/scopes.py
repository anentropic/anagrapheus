"""
Scope naming: how several actors hold beliefs about each other.

Doc 00 §6 keeps beliefs **per scope**, so *"several holders can believe contradictory
things at once and where they diverge is a query"*, and §6c calls
:func:`~anagrapheus.beliefs.divergence` *"the whole multi-agent-disagreement case"*.

Until this example, nothing exercised that. Every use in the library's own suite is
``"agent"`` against ``"world"`` — one believer measured against ground truth, which is
the degenerate case of a multi-holder design.

**Higher-order belief needs no new machinery.** A scope id is an arbitrary string, so
three scopes per party are enough to say what a party wants, what it has said, and what
it thinks another party wants:

- ``party:{id}:held`` — what they actually want. Private.
- ``party:{id}:stated`` — what they have said in the room. Public.
- ``party:{id}:model-of:{other}`` — what they think another party holds.

That yields three genuinely different questions from one three-line function, and the
difference between them is the whole reason this domain was chosen:

>>> divergence(store, model_of("eu", "uk"), held("uk"))     # how wrong are we about them
>>> divergence(store, stated("uk"), held("uk"))             # their own gap, word vs intent
>>> divergence(store, model_of("eu", "uk"), stated("uk"))   # do we take them at their word

The second is the one nobody but that party can ask, because nobody else has their
``held`` scope. That asymmetry is what makes the domain strategic rather than merely
multi-party.

**Why this convention lives here and not in the library.** §6b argues the predicate
registry belongs in code because it is *schema*. Scope naming is not schema — it is a
modelling choice about how many minds an application has and how they nest, which the
library cannot know. So the library ships scopes; the application names them.

§6a blesses the collision this relies on: revision history keys on the belief id alone
and is cross-scope, and *"for divergence queries that collision is the point."* Two
scopes holding ``position(uk, fisheries)`` differently is exactly the intended shape.
"""

from __future__ import annotations

__all__ = ["held", "model_of", "party_of", "stated"]


def held(party: str) -> str:
    """What ``party`` actually wants. Private to them."""
    return f"party:{party}:held"


def stated(party: str) -> str:
    """What ``party`` has said publicly. Visible to everyone."""
    return f"party:{party}:stated"


def model_of(holder: str, other: str) -> str:
    """What ``holder`` believes ``other`` holds — the higher-order scope."""
    return f"party:{holder}:model-of:{other}"


def party_of(scope: str) -> str:
    """
    The party a scope belongs to.

    Needed because a re-key has to visit *every* scope a merged entity appears in, and
    the scopes are not enumerable from the belief store — only from the parties. See
    :mod:`examples.negotiation.rekeying`.
    """
    return scope.split(":")[1]

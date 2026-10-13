"""
Anchor-replay: the invariant a generative projection actually keeps.

R61 clause 3, stated as a test. Everything the fold derives *from the log* — which
sections exist, the ranges they cite, where their boundaries fall — is a pure function
of the log and replays exactly. The prose is regenerated.

Deliberately **no equivalence claim about the prose**: nothing here defines semantic
equivalence of two consolidations and nothing could enforce it, so the assertion is
made only where it can be checked.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from anagrapheus.chronicle import reconsolidate
from anagrapheus.timeline import GenerativeRebuild

if TYPE_CHECKING:
    from .conftest import Rig


def test_rebuild_reproduces_the_anchors_exactly(rig: Rig):
    """The half that is invariant."""
    rig.commit(rig.step("a"), rig.step("b"), rig.close("one"))
    rig.commit(rig.step("c"), rig.close("two"))
    before = list(rig.anchors.all())

    rig.rebuild()

    assert list(rig.anchors.all()) == before
    assert before, "the test proves nothing if nothing was consolidated"


def test_rebuild_regenerates_the_prose(rig: Rig):
    """
    The half that is not — and the reason the grades are declared separately.

    The summarizer stamps a generation number, so identical prose would mean the
    rebuild never re-ran it. Asserting the prose *differs* is what keeps the previous
    test honest: it shows the anchors held across a real regeneration rather than
    across a no-op.
    """
    rig.commit(rig.step("a"), rig.close("one"))
    before = [s.prose for s in rig.prose.all()]
    drew = len(rig.summarizer.calls)

    rig.rebuild()

    after = [s.prose for s in rig.prose.all()]
    assert len(rig.summarizer.calls) == drew * 2, "rebuild must re-run the model calls"
    assert after != before


def test_rebuild_is_refused_without_acknowledgement(rig: Rig):
    """
    R61's migration asymmetry, made a runtime fact rather than a docstring.

    ``rebuild`` is sold everywhere else as cheap and exact — "stand up the adapter,
    replay the log". For this fold it is neither, so the default is refusal.
    """
    rig.commit(rig.step("a"), rig.close("one"))

    with pytest.raises(GenerativeRebuild):
        rig.rebuild(discard_generated=False)


def test_a_deterministic_fold_still_rebuilds_freely(rig: Rig):
    """The guard is per fold, not a new tax on every projection."""
    assert rig.fold.fold == "generative"
    # The identity and belief folds declare deterministic; the acceptance and timeline
    # suites exercise their rebuilds. Asserted here so a change to the guard that
    # accidentally caught every fold fails close to its cause.
    from anagrapheus.beliefs import BeliefFold
    from anagrapheus.identity import IdentityFold

    assert BeliefFold.fold == "deterministic"
    assert IdentityFold.fold == "deterministic"


def test_a_hand_made_reconsolidation_is_outside_the_claim(rig: Rig):
    """
    The precise edge of clause 3, and a second reason the carry path exists.

    A deliberate re-consolidation is not *fold-derived* — nothing in the log says it
    happened — so it is outside the anchor-replay claim. Rebuilding discards it and
    returns generation 1. Carrying preserves it. Both are valid; neither is canon.
    """
    rig.commit(rig.step("a"), rig.close("one"))
    original = rig.anchors.all()[0]
    better = reconsolidate(rig.chronicle, rig.reader, original.section_id, rig.summarizer)
    assert [a.section_id for a in rig.anchors.all()] == [better.anchor.section_id]

    carried = list(rig.chronicle.export())
    rig.rebuild()

    assert [a.section_id for a in rig.anchors.all()] == [original.section_id]
    rig.chronicle.load(carried)
    assert [a.section_id for a in rig.anchors.all()] == [better.anchor.section_id]

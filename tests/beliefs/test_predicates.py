"""Minting: the arity rule that closes the relational trap, ordering, and round-trips."""

from __future__ import annotations

import uuid
from uuid import UUID

import pytest
from hypothesis import given
from hypothesis import strategies as st

from anagrapheus.beliefs import (
    CURRENT_SCHEME,
    MalformedBeliefId,
    MintedId,
    OutsideVocabulary,
    Predicate,
    PredicateKind,
    PredicateRegistry,
    UnknownPredicate,
    UnknownScheme,
    WrongArity,
)

from .conftest import PREDICATES, build_predicates


def test_a_relational_predicate_cannot_be_minted_at_lower_arity():
    """
    The trap doc 00 §6a names, closed mechanically rather than by documentation.

    Keyed as ``knows(alice)`` with ``bob`` in the value, revising to ``carol`` would
    silently retract the belief about bob. The declaration makes that spelling
    unreachable, so the retraction cannot happen by accident.
    """
    predicates = build_predicates()
    alice, bob = uuid.uuid7(), uuid.uuid7()

    with pytest.raises(WrongArity, match="takes 2 entities, got 1"):
        predicates.belief_id("knows", alice)

    # And the correct spelling puts both in the id, so the two claims are two beliefs.
    assert predicates.belief_id("knows", alice, bob) != predicates.belief_id(
        "knows", alice, uuid.uuid7()
    )


def test_an_undeclared_predicate_is_refused():
    with pytest.raises(UnknownPredicate, match="not declared"):
        build_predicates().belief_id("gossips_about", uuid.uuid7())


def test_an_unordered_predicate_mints_one_id_for_both_spellings():
    predicates = build_predicates()
    a, b = uuid.uuid7(), uuid.uuid7()

    assert predicates.belief_id("sibling", a, b) == predicates.belief_id("sibling", b, a)
    # ...and an ordered one does not: `reports_to(a, b)` is not `reports_to(b, a)`.
    assert predicates.belief_id("knows", a, b) != predicates.belief_id("knows", b, a)


def test_the_id_carries_scheme_and_namespace():
    predicates = build_predicates(namespace="tenant.a")
    entity = uuid.uuid7()

    belief_id = predicates.belief_id("location", entity)

    assert belief_id == f"v{CURRENT_SCHEME}:tenant.a:location({entity})"


def test_two_namespaces_mint_different_ids_for_the_same_proposition():
    """
    §6a's namespace warning: revision history keys on the belief id alone, cross-scope.

    For a divergence query between two holders that collision is the mechanism; for two
    tenants it is a leak. The namespace is what separates them, so it is required.
    """
    entity = uuid.uuid7()

    assert build_predicates("tenant.a").belief_id("location", entity) != build_predicates(
        "tenant.b"
    ).belief_id("location", entity)


def test_a_registry_refuses_a_namespace_that_would_break_parsing():
    with pytest.raises(ValueError, match="may contain only"):
        PredicateRegistry(PREDICATES, namespace="a:b")


def test_a_registry_refuses_the_same_predicate_twice():
    with pytest.raises(ValueError, match="declared twice"):
        PredicateRegistry(
            [
                Predicate("location", 1, PredicateKind.FUNCTIONAL),
                Predicate("location", 2, PredicateKind.RELATIONAL),
            ],
            namespace="crm",
        )


@pytest.mark.parametrize(
    "text",
    [
        "location(x)",
        "v1:crm:location()",
        "v1:crm:location(not-a-uuid)",
        "v1:crm:location",
        "",
    ],
)
def test_a_string_that_is_not_a_belief_id_is_refused(text: str):
    with pytest.raises(MalformedBeliefId):
        build_predicates().parse(text)


def test_an_unknown_scheme_refuses_to_render_rather_than_re_minting():
    """
    The point of versioning the scheme: an id this code cannot reproduce says so.

    Silently re-minting under today's rule is precisely the orphaning a version exists
    to make loud — the same failure mode as pin-key drift.
    """
    predicates = build_predicates()
    minted = MintedId(scheme=99, namespace="crm", name="location", entity_ids=(uuid.uuid7(),))

    with pytest.raises(UnknownScheme, match="v99"):
        predicates.render(minted)


def test_parse_reads_a_foreign_namespace_without_claiming_it():
    """Parsing is not adoption: a registry can read an id it would not have minted."""
    entity = uuid.uuid7()
    foreign = build_predicates("other").belief_id("location", entity)

    parsed = build_predicates("crm").parse(foreign)

    assert parsed.namespace == "other"
    assert parsed.entity_ids == (entity,)


_UUIDS = st.builds(uuid.UUID, st.binary(min_size=16, max_size=16).map(bytes.hex))

#: A predicate paired with exactly as many entities as it declares. Built together
#: rather than drawn independently, so every example is a legal mint: filtering the
#: mismatches out afterwards would leave the property exercising almost nothing.
_APPLICATIONS = st.sampled_from(PREDICATES).flatmap(
    lambda predicate: st.tuples(
        st.just(predicate.name),
        st.lists(_UUIDS, min_size=predicate.arity, max_size=predicate.arity),
    )
)


@given(application=_APPLICATIONS)
def test_mint_parse_round_trips(application: tuple[str, list[UUID]]):
    """Whatever is minted parses back to what went in, arranged as the predicate says."""
    name, entities = application
    predicates = build_predicates()
    declared = predicates.predicate(name)

    belief_id = predicates.belief_id(name, *entities)
    parsed = predicates.parse(belief_id)

    assert parsed.name == name
    assert parsed.namespace == "crm"
    assert parsed.scheme == CURRENT_SCHEME
    expected = tuple(entities) if declared.ordered else tuple(sorted(entities, key=str))
    assert parsed.entity_ids == expected
    # ...and re-rendering the parse is a fixed point.
    assert predicates.render(parsed) == belief_id


def test_an_empty_namespace_is_refused():
    """
    The namespace is required *because* an unnamespaced id collides across tenants.

    Accepting ``""`` would let a caller satisfy the signature while getting exactly the
    id the requirement exists to prevent — the decision made by not making it.
    """
    with pytest.raises(ValueError, match="namespace"):
        PredicateRegistry(PREDICATES, namespace="")


def test_parse_still_reads_an_id_with_no_namespace():
    """
    Strict on the way out, permissive on the way in.

    Minting decides what this process writes; parsing reads what the log already
    holds, which may include ids written before the rule tightened. Refusing to *read*
    them would strand exactly the beliefs a re-key needs to reach.
    """
    entity = uuid.uuid7()

    parsed = build_predicates().parse(f"v1::location({entity})")

    assert parsed.namespace == ""
    assert parsed.entity_ids == (entity,)


class TestVocabulary:
    """A functional predicate may close its answers; a relational one already is closed."""

    def test_a_value_outside_the_vocabulary_is_refused(self):
        stance = Predicate(
            "stance", 1, PredicateKind.FUNCTIONAL, values=frozenset({"concede", "hold_firm"})
        )

        stance.check_value("hold_firm")
        # The spelling a live model actually wrote, which is the case this exists for.
        with pytest.raises(OutsideVocabulary, match=r"\['concede', 'hold_firm'\].*'hold firm'"):
            stance.check_value("hold firm")
        # Not a string at all is outside a vocabulary of words, too.
        with pytest.raises(OutsideVocabulary):
            stance.check_value(1)

    def test_it_is_a_value_error_to_a_caller_who_knows_none_of_our_types(self):
        stance = Predicate("stance", 1, PredicateKind.FUNCTIONAL, values=frozenset({"a"}))

        with pytest.raises(ValueError, match="answers with one of"):
            stance.check_value("b")

    def test_an_undeclared_vocabulary_accepts_anything(self):
        Predicate("location", 1, PredicateKind.FUNCTIONAL).check_value("anywhere at all")

    def test_a_relational_predicate_cannot_declare_one(self):
        with pytest.raises(ValueError, match="relational"):
            Predicate("knows", 2, PredicateKind.RELATIONAL, values=frozenset({"yes"}))

    def test_an_empty_vocabulary_is_refused(self):
        """Declaring no possible answers would make every write fail, so it is a typo."""
        with pytest.raises(ValueError, match="empty vocabulary"):
            Predicate("stance", 1, PredicateKind.FUNCTIONAL, values=frozenset())

    def test_the_vocabulary_is_not_part_of_the_id(self):
        """
        Widening a vocabulary must not orphan a belief already held.

        The id names the question and the value answers it; adding an answer is a
        change to the answers, so every id minted before it still reaches its history.
        """
        narrow = Predicate("stance", 1, PredicateKind.FUNCTIONAL, values=frozenset({"a"}))
        wide = Predicate("stance", 1, PredicateKind.FUNCTIONAL, values=frozenset({"a", "b"}))
        entity = uuid.uuid7()

        assert PredicateRegistry([narrow], namespace="t").belief_id(
            "stance", entity
        ) == PredicateRegistry([wide], namespace="t").belief_id("stance", entity)

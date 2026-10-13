"""
Declared predicates, and the only way to mint a belief id.

doxastica keeps beliefs but deliberately does not decide what a belief *is about*:
``belief_id`` is a caller-supplied string, and choosing it is the integration cost doc
00 §6a names. The rule is short and unforgiving — **the id names the question, the
value answers it.** If two states cannot both be true at once they share an id; if
they can, they must not.

Predicates are **schema**: a small set, changed only on deploy, required to be
identical in every process that mints or reads. §6b puts them in code for that reason
— in code a disagreement fails at import, in a database it fails at runtime, acquires
a migration story for what is a constant, and drifts from what the code assumes.

Minting lives on the registry rather than beside it, because §6c's divergence query
works only if two scopes minted through the same rule. A free function taking a
predicate would leave a second way to mint; a method does not.

**Minting a belief id consumes no entropy.** It is a pure function of a predicate and
some entity ids — derivation, not a draw — so unlike
:func:`~anagrapheus.identity.decisions.mint_entity_id` it needs no pin and replays
identically by construction. The two sit one layer apart and are worth not confusing:
an *entity* id is drawn, a *belief* id is computed from ids already drawn.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Final
from uuid import UUID

from doxastica import Stance

from anagrapheus.beliefs.errors import (
    MalformedBeliefId,
    OutsideVocabulary,
    UnknownPredicate,
    UnknownScheme,
    WrongArity,
)

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

__all__ = [
    "CURRENT_SCHEME",
    "MintedId",
    "Predicate",
    "PredicateKind",
    "PredicateRegistry",
]

#: The minting rule this code writes. Bumped when the rule changes, never reused for a
#: changed rule: §6a's "version the scheme", because a silent change orphans every id
#: minted under the old one exactly as pin-key drift orphans a pin.
CURRENT_SCHEME: Final = 1

_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
# Non-empty on purpose. The namespace is required *because* an unnamespaced id
# collides across tenants, and `namespace=""` would satisfy the signature while
# producing exactly the id the requirement exists to prevent. `parse` stays
# permissive: minting decides what this process writes, parsing reads what the log
# already holds.
_NAMESPACE = re.compile(r"^[A-Za-z0-9_.-]+$")
_BELIEF_ID = re.compile(r"^v(?P<scheme>\d+):(?P<ns>[^:()]*):(?P<name>[^:()]+)\((?P<args>[^()]*)\)$")


class PredicateKind(StrEnum):
    """
    Whether a predicate holds one value at a time or relates its arguments.

    The distinction is doc 00 §6a's trap, and it is a modelling decision the
    application makes when it declares the predicate rather than one this library can
    infer:

    - ``FUNCTIONAL`` — ``location(alice)``. One value at a time; revision supersedes
      correctly, because the new place genuinely replaces the old one.
    - ``RELATIONAL`` — ``knows(alice, bob)``. Every argument belongs in the *id* and
      the value only asserts that the proposition holds, so it is a bool. (Not a
      stance: *how firmly* it is held is doxastica's own ``stance`` argument, one axis
      over.) Keyed as ``knows(alice)`` with ``bob`` in the value, revising to ``carol``
      would silently retract the belief about bob — a retraction nobody asked for and
      nothing records.

    Declaring the kind is what lets :meth:`Predicate.check_value` reject a relational
    belief carrying a substantive value, on every path that writes one; the arity check
    catches the shape, and the kind catches the intent.
    """

    FUNCTIONAL = "functional"
    RELATIONAL = "relational"


@dataclass(frozen=True, slots=True)
class Predicate:
    """
    One declared predicate: its name, how many entities it takes, and how they behave.

    ``ordered=False`` means the arguments are interchangeable — ``sibling(a, b)`` and
    ``sibling(b, a)`` are one proposition — so minting sorts them and both spellings
    reach the same id. ``ordered=True`` is the default because most predicates read in
    one direction: ``reports_to(a, b)`` is not ``reports_to(b, a)``.

    ``values`` closes a functional predicate's answers to a declared set of words. The
    id was already schema and the value was not, so ``hold firm`` and ``hold_firm`` were
    two beliefs that no divergence query could compare and nothing would notice.
    ``None``, the default, leaves the value open — right for ``location`` or a name,
    where the answers cannot be listed. A relational predicate cannot declare values:
    its value only says whether the proposition holds, and that is already closed to a
    bool.
    """

    name: str
    arity: int
    kind: PredicateKind
    ordered: bool = True
    values: frozenset[str] | None = None

    def __post_init__(self) -> None:
        if not _NAME.match(self.name):
            raise ValueError(f"predicate name {self.name!r} is not an identifier")
        if self.arity < 1:
            raise ValueError(f"predicate {self.name!r} needs at least one entity, got {self.arity}")
        if self.values is not None:
            if self.kind is PredicateKind.RELATIONAL:
                raise ValueError(
                    f"predicate {self.name!r} is relational, so its value is a bool and "
                    f"cannot declare a vocabulary"
                )
            if not self.values:
                raise ValueError(f"predicate {self.name!r} declares an empty vocabulary")

    def check_value(self, value: object) -> None:
        """
        Refuse a value this predicate cannot hold.

        Two rules, both the predicate's own so that every path writing a belief holds to
        them — the belief fold, the helpers that build belief events, and a derived read
        that never touches a store:

        - **A relational value is a bool.** The arity check already stops
          ``knows(alice)`` from being minted; this catches the same mistake arriving the
          other way, a third party smuggled into the answer of ``knows(alice, bob)``,
          which revising would retract. A ``Stance`` is refused too: how firmly a belief
          is held is doxastica's own ``stance`` argument, one axis over, and taking it as
          the value would merge ranking with epistemic state (R21). ``TypeError``.
        - **A declared vocabulary is closed.** :class:`OutsideVocabulary`. A predicate
          with no vocabulary accepts anything.
        """
        if self.kind is PredicateKind.RELATIONAL:
            if isinstance(value, bool):
                return
            hint = (
                " (how firmly it is held is the `stance` argument, not the value)"
                if isinstance(value, Stance)
                else ""
            )
            raise TypeError(
                f"predicate {self.name!r} is relational, so its value asserts the "
                f"proposition rather than answering it: pass a bool, not "
                f"{type(value).__name__}{hint}"
            )
        if self.values is None or (isinstance(value, str) and value in self.values):
            return
        raise OutsideVocabulary(
            f"predicate {self.name!r} answers with one of {sorted(self.values)}, not {value!r}"
        )


@dataclass(frozen=True, slots=True)
class MintedId:
    """A belief id taken apart: the scheme that made it and what went into it."""

    scheme: int
    namespace: str
    name: str
    entity_ids: tuple[UUID, ...]


class PredicateRegistry:
    """
    The declared predicates, and the only way to mint a belief id from them.

    ``namespace`` is required rather than defaulted, because doc 00 §6a's warning has
    no safe default. doxastica keys revision history on the belief id **alone**, and
    that history is cross-scope: for a divergence query between two holders the
    collision *is* the mechanism, and for two tenants it is a leak. Which one you are
    building is not something this library can guess, so it makes you say.
    """

    def __init__(self, predicates: Iterable[Predicate], *, namespace: str) -> None:
        if not _NAMESPACE.match(namespace):
            raise ValueError(
                f"namespace {namespace!r} must be non-empty and may contain only "
                f"letters, digits, '_', '.' and '-'"
            )
        self._namespace = namespace
        self._predicates: dict[str, Predicate] = {}
        for predicate in predicates:
            if predicate.name in self._predicates:
                raise ValueError(f"predicate {predicate.name!r} declared twice")
            self._predicates[predicate.name] = predicate

    @property
    def namespace(self) -> str:
        """The namespace every id this registry mints carries."""
        return self._namespace

    @property
    def names(self) -> tuple[str, ...]:
        """Every declared predicate name, in declaration order."""
        return tuple(self._predicates)

    def predicate(self, name: str) -> Predicate:
        """The declared predicate ``name``, or raise :class:`UnknownPredicate`."""
        try:
            return self._predicates[name]
        except KeyError:
            raise UnknownPredicate(
                f"predicate {name!r} is not declared; declared: {sorted(self._predicates)}"
            ) from None

    def belief_id(self, name: str, *entity_ids: UUID) -> str:
        """
        Mint the belief id for ``name`` applied to ``entity_ids``.

        Exactly ``arity`` entities, or :class:`WrongArity`. That check is the whole of
        §6a's functional/relational discipline made mechanical: a predicate declared
        ``knows/2`` cannot be minted at arity 1, so the silent retraction that shape
        causes is not something a caller can reach by accident.

        Entities are ids, never prose — the same discipline as the pin key, and for the
        same reason. If a model produced the claim, have it emit
        ``(predicate, entity_ids)`` rather than a sentence for something to parse.
        """
        return self.render(self.mint(name, *entity_ids))

    def mint(self, name: str, *entity_ids: UUID) -> MintedId:
        """Mint as structure rather than as a string — :meth:`belief_id` renders it."""
        predicate = self.predicate(name)
        if len(entity_ids) != predicate.arity:
            raise WrongArity(
                f"predicate {name!r} takes {predicate.arity} "
                f"{'entity' if predicate.arity == 1 else 'entities'}, got {len(entity_ids)}"
            )
        return MintedId(
            scheme=CURRENT_SCHEME,
            namespace=self._namespace,
            name=name,
            entity_ids=self._arrange(predicate, entity_ids),
        )

    def render(self, minted: MintedId) -> str:
        """
        Render a :class:`MintedId` back to its string form, in **its own** scheme.

        A re-key re-mints an id that already exists, and must reproduce it under the
        rule that made it rather than under today's — so the scheme travels with the
        structure. An unrecognised scheme raises: this code cannot reproduce an id it
        does not know the rule for, and guessing would orphan the history keyed on it.
        """
        if minted.scheme != CURRENT_SCHEME:
            raise UnknownScheme(
                f"belief id scheme v{minted.scheme} is not one this code can render "
                f"(it writes v{CURRENT_SCHEME})"
            )
        predicate = self.predicate(minted.name)
        if len(minted.entity_ids) != predicate.arity:
            raise WrongArity(
                f"predicate {minted.name!r} takes {predicate.arity}, got {len(minted.entity_ids)}"
            )
        entities = ",".join(str(e) for e in self._arrange(predicate, minted.entity_ids))
        return f"v{minted.scheme}:{minted.namespace}:{minted.name}({entities})"

    def parse(self, belief_id: str) -> MintedId:
        """
        Take a belief id apart into the components that made it.

        The inverse of :meth:`belief_id`, and the reason ids are legible rather than
        digests: re-keying a belief after an identity merge has to substitute one
        entity for another and re-mint, and a digest could only do that by consulting
        an index — which would make the index a correctness mechanism rather than the
        disposable convenience doc 00 §6b says it is.

        The namespace is read from the id rather than assumed, so a registry can parse
        an id another namespace minted. Whether it should *act* on it is the caller's
        call.
        """
        match = _BELIEF_ID.match(belief_id)
        if match is None:
            raise MalformedBeliefId(f"{belief_id!r} is not a belief id this registry mints")
        raw = match["args"]
        if not raw:
            raise MalformedBeliefId(f"{belief_id!r} names no entities")
        try:
            entity_ids = tuple(UUID(part) for part in raw.split(","))
        except ValueError as exc:
            raise MalformedBeliefId(f"{belief_id!r} carries a non-uuid entity: {exc}") from exc
        return MintedId(
            scheme=int(match["scheme"]),
            namespace=match["ns"],
            name=match["name"],
            entity_ids=entity_ids,
        )

    @staticmethod
    def _arrange(predicate: Predicate, entity_ids: Sequence[UUID]) -> tuple[UUID, ...]:
        # Sorting is what makes an unordered predicate's two spellings one id. It runs
        # on every render rather than only at mint, because substituting an entity --
        # which is what a re-key does -- can move it in the order.
        if predicate.ordered:
            return tuple(entity_ids)
        return tuple(sorted(entity_ids, key=str))

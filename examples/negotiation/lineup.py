"""
Which model fills which part of the harness — the unit you swap when you evaluate.

The negotiation makes three kinds of model call, and they are different jobs:

- **propose** — read the brief and make an offer, possibly reaching for the archive.
- **update** — read what happened and conclude one thing about the other side.
- **summarize** — consolidate a closed round into a chronicle section.

Until this module every one of them ran on the single ``model`` handed to
:func:`~examples.negotiation.story.negotiate`, for both parties. That made the most
useful question about the harness unaskable: *is this model better at this job?*

**A lineup is the application's, not the library's.** Roles are vocabulary, and doc 00
§8's rule for vocabulary is that it belongs to whoever owns the domain — the same reason
event types live in ``domain.py`` rather than ``anagrapheus``. The library already has
everything a lineup needs: :class:`~anagrapheus.provider.model.PinnedModel` wraps any
pydantic-ai ``Model``, and pydantic-ai's ``infer_model`` turns ``"anthropic:claude-opus-5"``
or ``"openrouter:openai/gpt-5"`` into one. Nothing in ``src/`` changes.

**Evaluating one role with everything else held fixed comes free, from the pin key.**
A key includes the model's id, so replaying a recorded run with
:class:`~anagrapheus.timeline.ReplayPins` in ``"record"`` mode and *one* role's model
changed makes exactly that role's calls miss and draw fresh. Every call upstream replays
bit for bit; calls downstream re-draw only where their prompt actually changed; and the
replay's ``divergence`` says where it stopped being a replay. That is a controlled
comparison rather than two independent runs that differ in everything at once.

**Two models with the same id share pins**, and that is the trap in the last paragraph.
For a real provider the id is ``provider:model`` and two different models cannot
collide. Every ``FunctionModel`` built from the same function reports the same id, so a
"swapped" role using a second copy of the scripted model would hit the baseline's pins
and never draw — a comparison that silently compared nothing.
:func:`~examples.negotiation.scripted.scripted` takes a ``name`` for exactly this.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal, cast, get_args

from pydantic_ai.models import infer_model

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping, Sequence

    from pydantic_ai.models import Model

__all__ = ["ROLES", "Lineup", "LineupError", "Role"]

#: The three jobs a model does in this harness.
type Role = Literal["propose", "update", "summarize"]

#: The roles, in the order a round performs them.
ROLES: tuple[Role, ...] = get_args(Role.__value__)

#: Roles a party performs. The summarizer is not one: there is one chronicle, every
#: party reads it, and note 06 §9a is why it may only ever carry what was said in the
#: room. A per-party summarizer would be a per-party chronicle, which is a different
#: design rather than a different model.
_PARTY_ROLES: frozenset[Role] = frozenset({"propose", "update"})


class LineupError(ValueError):
    """A lineup that would run, but not the way it reads."""


@dataclass(frozen=True, slots=True)
class Lineup:
    """
    A model for each role, optionally different per party.

    Most specific wins: a model named for ``("eu", "propose")`` beats one named for
    ``"propose"``, which beats the default.
    """

    #: What fills any slot nothing more specific names. ``None`` only when every role
    #: is named explicitly.
    default: Model | None = None
    roles: Mapping[Role, Model] = field(default_factory=lambda: _none_by_role())
    parties: Mapping[tuple[str, Role], Model] = field(default_factory=lambda: _none_by_party())

    def __post_init__(self) -> None:
        uncovered = [role for role in ROLES if self.default is None and role not in self.roles]
        if uncovered:
            raise LineupError(
                f"nothing fills {', '.join(uncovered)}: give a default model, or name "
                f"one for every role ({', '.join(ROLES)})."
            )
        for party, role in self.parties:
            if role not in _PARTY_ROLES:
                raise LineupError(
                    f"{party}.{role}: only {' and '.join(sorted(_PARTY_ROLES))} can differ "
                    f"by party. There is one chronicle and every party reads it."
                )

    @classmethod
    def of(cls, model: Model) -> Lineup:
        """One model in every role, for every party — what the demo did before this."""
        return cls(default=model)

    @classmethod
    def parse(
        cls,
        specs: Iterable[str],
        *,
        resolve: Callable[[str], Model] = infer_model,
    ) -> Lineup:
        """
        Build a lineup from ``[party.]role=provider:model`` specs, or a bare default.

        ``resolve`` turns a model string into a model and defaults to pydantic-ai's
        ``infer_model``, which already fails loudly — at this call, not at the first
        draw — on a missing API key, an unknown provider, or a missing prefix. A test
        passes its own so it needs no key.

        Naming the same slot twice is refused rather than resolved last-wins: on a
        command line that is almost always a typo, and the lineup that silently runs is
        not the one that was asked for.
        """
        default: Model | None = None
        roles: dict[Role, Model] = {}
        parties: dict[tuple[str, Role], Model] = {}
        seen: set[str] = set()
        for spec in specs:
            slot, has_slot, model = spec.partition("=")
            if not has_slot:
                slot, model = "", spec
            if not model:
                raise LineupError(f"{spec!r} names no model.")
            if slot in seen:
                raise LineupError(f"{slot or 'the default'} is named twice.")
            seen.add(slot)
            if not slot:
                default = resolve(model)
                continue
            party, has_party, role = slot.rpartition(".")
            if role not in ROLES:
                raise LineupError(f"{spec!r}: unknown role {role!r}; expected one of {ROLES}.")
            if has_party:
                parties[(party, role)] = resolve(model)
            else:
                roles[role] = resolve(model)
        return cls(default=default, roles=roles, parties=parties)

    def model_for(self, role: Role, party: str | None = None) -> Model:
        """The model that fills ``role`` — for ``party``, if it has its own."""
        if party is not None and (party, role) in self.parties:
            return self.parties[(party, role)]
        if role in self.roles:
            return self.roles[role]
        # `__post_init__` guarantees a default whenever a role is unnamed.
        return cast("Model", self.default)

    def check_parties(self, seated: Sequence[str]) -> None:
        """
        Refuse a lineup naming a party that is not at the table.

        Without this, ``ue.propose=...`` is a typo that runs: the misspelt slot is never
        consulted, the default fills the EU's role, and an evaluation reports a result
        for a model that never took part.
        """
        unknown = sorted({party for party, _ in self.parties} - set(seated))
        if unknown:
            raise LineupError(
                f"the lineup names {', '.join(unknown)}, not at the table. "
                f"Seated: {', '.join(seated)}."
            )

    def describe(self, seated: Sequence[str]) -> str:
        """Every slot and the model id filling it — what actually ran, not what was typed."""
        slots = [
            f"{party}.{role}={self.model_for(role, party).model_id}"
            for party in seated
            for role in ROLES
            if role in _PARTY_ROLES
        ]
        slots.append(f"summarize={self.model_for('summarize').model_id}")
        return ", ".join(slots)


def _none_by_role() -> dict[Role, Model]:
    return {}


def _none_by_party() -> dict[tuple[str, Role], Model]:
    return {}

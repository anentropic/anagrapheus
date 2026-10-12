"""
The table, read into something a person can look at.

Everything here is a *read*, taken from the table's omniscient position: both parties'
private scopes, and every derived model of one mind by another. That is the analyst's
seat, the same one :meth:`~examples.negotiation.table.Table.misreading` sits in, and it
is fine for a UI to sit there too. What must not happen is any of it reaching an
agent: these snapshots go to the browser and nowhere else.

**Identifiers are rendered, not shown.** A belief id is
``v1:negotiation:position(<uuid>,<uuid>)`` and an event is a UUID, which is right for a
store and useless on a screen. :class:`Labeler` maps entity ids to the names they were
seated or tabled under and event ids to ``#seq Type``, so a belief reads as
``position(uk, fisheries)`` and its evidence as ``#14 PositionStated``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import UUID

from doxastica import BeliefFilter
from examples.negotiation.nesting import DEFAULT_DEPTH, reconstruct
from examples.negotiation.scopes import held, stated
from pydantic_core import to_jsonable_python

if TYPE_CHECKING:
    from collections.abc import Mapping

    from doxastica import BeliefState
    from examples.negotiation.nesting import Derived
    from examples.negotiation.table import Table

    from anagrapheus.timeline import PendingEvent, StoredEvent

__all__ = ["Labeler", "render_event", "snapshot"]

type Json = Any


class Labeler:
    """
    Readable names for the ids a table is full of.

    Built per table and kept current by :meth:`saw`, which the watcher calls with every
    appended event: an event's label needs its ``seq``, which only the append knows.
    Entity names are read from the table's seats and clauses each time, since parties
    can be seated at any point.
    """

    def __init__(self, table: Table) -> None:
        self._table = table
        self._events: dict[str, str] = {}

    def saw(self, seq: int, event_id: UUID | str, type_name: str) -> None:
        """Remember an appended event under its ``#seq Type`` label."""
        self._events[str(event_id)] = f"#{seq} {type_name}"

    def entity(self, entity_id: UUID | str) -> str:
        """An entity or event id as a name, or a short form of the id if unknown."""
        key = str(entity_id)
        for names in (self._table.seats, self._table.clauses):
            for name, known in names.items():
                if str(known) == key or str(self._table.canonical(known)) == key:
                    return name
        return self._events.get(key, f"{key[:8]}…")

    def event(self, event_id: UUID | str) -> str:
        """An event id as ``#seq Type``."""
        key = str(event_id)
        return self._events.get(key, f"{key[:8]}…")

    def belief(self, belief_id: str) -> str:
        """A belief id as ``predicate(name, name)``, falling back to the raw id."""
        try:
            minted = self._table.predicates.parse(belief_id)
        except Exception:  # noqa: BLE001 -- a label must never break a snapshot
            return belief_id
        return f"{minted.name}({', '.join(self.entity(e) for e in minted.entity_ids)})"

    def payload(self, value: Json) -> Json:
        """A JSON payload with every UUID-shaped string replaced by its label."""
        if isinstance(value, dict):
            return {k: self.payload(v) for k, v in value.items()}
        if isinstance(value, list):
            return [self.payload(v) for v in value]
        if isinstance(value, str) and _is_uuid(value):
            return self.entity(value)
        return value


def snapshot(table: Table, labels: Labeler) -> dict[str, Json]:
    """
    Everything worth showing about the table right now, as JSON.

    Each section is computed on its own and an error in one is reported in its place,
    because a snapshot can be taken between any two appends -- including before the
    clause is tabled -- and a half-built table should show what it has rather than
    nothing.
    """
    return {
        "scopes": _guard(lambda: _scopes(table, labels)),
        "models": _guard(lambda: _models(table, labels)),
        "headline": _guard(lambda: _headline(table, labels)),
        "chronicle": _guard(lambda: [s.prose for s in table.brief().depth()]),
    }


def render_event(event: PendingEvent | StoredEvent, seq: int, labels: Labeler) -> dict[str, Json]:
    """One event as a row: its ``seq``, type, a one-line summary, and its payload."""
    payload = labels.payload(to_jsonable_python(event.payload))
    return {
        "seq": seq,
        "id": str(event.envelope.event_id),
        "type": event.envelope.type,
        "namespace": event.envelope.namespace,
        "summary": _summary(event.envelope.type, payload, labels),
        "payload": payload,
    }


# ---- sections ---------------------------------------------------------------------------


def _scopes(table: Table, labels: Labeler) -> list[dict[str, Json]]:
    """Each party's two stored scopes: what it wants, and what it has said."""
    return [
        {
            "party": party,
            "kind": kind,
            "scope": scope,
            "beliefs": [
                _belief(state, labels) for state in table.core.query_scope(scope, BeliefFilter())
            ],
        }
        for party in table.parties
        for kind, scope in (("held", held(party)), ("stated", stated(party)))
    ]


def _models(table: Table, labels: Labeler) -> list[dict[str, Json]]:
    """
    Every mind as another imagines it, to depth two. Derived here and dropped (R28).

    For each viewer and each other party: the viewer's model of the other, and the
    viewer's model of the other's model of the viewer. The second is where the demo's
    false access-belief shows up, because it is gated on what the viewer *thinks* the
    other witnessed.
    """
    interpret = table.interpret()
    out: list[dict[str, Json]] = []
    for viewer in table.parties:
        view = table.view(viewer)
        for other in (p for p in table.parties if p != viewer):
            for chain in ((other,), (other, viewer)):
                derived = reconstruct(view, chain, interpret=interpret, depth=DEFAULT_DEPTH)
                out.append(_derived(viewer, derived, labels))
    return out


def _headline(table: Table, labels: Labeler) -> dict[str, Json] | None:
    """
    The story's measured claim, after every event rather than once a round.

    What the EU reads of the UK's position, against what the UK imagines the EU reads:
    the pair :class:`~examples.negotiation.story.Mismatch` records at each round's end.
    """
    if not {"eu", "uk"} <= set(table.seats) or not table.clauses:
        return None
    clause = next(iter(table.clauses))
    wanted = table.predicates.belief_id(
        "position", table.canonical(table.seats["uk"]), table.canonical(table.clauses[clause])
    )
    interpret = table.interpret()
    truly = reconstruct(table.view("eu"), ("uk",), interpret=interpret).get(wanted)
    imagined = reconstruct(table.view("uk"), ("eu", "uk"), interpret=interpret).get(wanted)
    return {
        "belief": labels.belief(wanted),
        "truly": to_jsonable_python(truly),
        "imagined": to_jsonable_python(imagined),
        "wrong": truly != imagined,
    }


# ---- pieces -------------------------------------------------------------------------------


def _belief(state: BeliefState, labels: Labeler) -> dict[str, Json]:
    return {
        "id": state.belief_id,
        "label": labels.belief(state.belief_id),
        "value": to_jsonable_python(state.value, fallback=repr),
        "status": _enum(state.status),
        "stance": _enum(state.stance),
        "source": labels.event(state.source_event_id),
    }


def _derived(viewer: str, derived: Derived, labels: Labeler) -> dict[str, Json]:
    path = " → ".join((viewer, *derived.chain))
    return {
        "viewer": viewer,
        "chain": list(derived.chain),
        "title": path,
        "beliefs": [
            {
                "id": belief_id,
                "label": labels.belief(belief_id),
                "value": to_jsonable_python(value, fallback=repr),
            }
            for belief_id, value in sorted(derived.beliefs.items())
        ],
        "over": [labels.event(e) for e in derived.over],
        "truncated": derived.truncated,
    }


def _summary(type_name: str, payload: Mapping[str, Json], labels: Labeler) -> str:
    """A one-line reading of the domain's events; ``key=value`` pairs for the rest."""
    match type_name:
        case "established":
            return f"{payload['first_surface_form']!r} established ({payload['provenance']})"
        case "ClauseTabled":
            return f"{payload['title']!r} is tabled"
        case "PositionStated":
            heard = ", ".join(payload.get("heard_by") or ()) or "nobody"
            return f"{payload['party']} states {payload['position']} (heard by {heard})"
        case "SideRemarkOverheard":
            return (
                f"{payload['speaker']} lets slip {payload['suggests']!r}, "
                f"overheard by {payload['overheard_by']}"
            )
        case "RoundClosed":
            return f"round {payload['round_number']} closed"
        case "BeliefMinted":
            return f"{labels.belief(str(payload['belief_id']))} minted"
        case "PinPromoted":
            return f"{payload['kind']} pin promoted into the log"
        case _:
            return ", ".join(
                f"{k}={v}" for k, v in payload.items() if isinstance(v, str | int | float | bool)
            )


def _guard(compute: Any) -> Json:  # noqa: ANN401
    try:
        return compute()
    except Exception as error:  # noqa: BLE001 -- shown in place of the section
        return {"error": f"{type(error).__name__}: {error}"}


def _enum(value: object) -> str:
    return str(getattr(value, "name", value))


def _is_uuid(text: str) -> bool:
    if len(text) != 36:
        return False
    try:
        UUID(text)
    except ValueError:
        return False
    return True

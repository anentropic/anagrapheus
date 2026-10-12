"""
What a context is made of: parts, each with a register, a rank and its sources.

A part is **structured, never pre-rendered**. The application's renderer turns parts
into messages, and nothing here decides wording: NVM's position is "structured at
source, legible at surface", and an experiment that varies only the serialization needs
the structure held still while the bytes change.

The register is structure as well, which the demo learned by hand: a chronicle section
read beside a belief looks like one unless something says otherwise, and the something
was a sentence in a prompt (note 07 §1).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

from anagrapheus.beliefs.ops import StateRef

if TYPE_CHECKING:
    from uuid import UUID

    from pydantic import JsonValue

    from anagrapheus.chronicle.sections import SectionId

__all__ = ["Context", "EventRef", "GivenRef", "Part", "Register", "SectionRef", "Source"]


class Register(StrEnum):
    """
    What kind of thing a part is, which is what decides whether it may be shed.

    NVM's three labelled registers plus R61's fourth: prose a model wrote is advisory
    however it is worded, and is labelled as such by where it was read from.
    """

    #: Must hold. Never shed: a call that cannot carry its invariants is not made.
    binding = "binding"
    #: Advisory and ranked; shed lowest rank first.
    considered = "considered"
    #: Technique, loaded on relevance.
    craft = "craft"
    #: Model-written prose. Advisory only (R61 clause 4).
    generated = "generated"


@dataclass(frozen=True, slots=True)
class EventRef:
    """An event a slice handed out, by position and id."""

    seq: int
    event_id: UUID


@dataclass(frozen=True, slots=True)
class SectionRef:
    """A chronicle section a slice handed out, and the range of the log it covers."""

    section_id: SectionId
    from_seq: int
    to_seq: int


@dataclass(frozen=True, slots=True)
class GivenRef:
    """
    A value the application handed in, rather than one a projection produced.

    ``event_id`` names the event it came from when there is one — NVM's
    ``RenderContract`` is the Thinker's pinned output — and is ``None`` when the value
    was computed in memory by the caller, which a context should then say plainly.
    """

    name: str
    event_id: UUID | None = None


#: Where something in a context came from.
type Source = EventRef | StateRef | SectionRef | GivenRef


@dataclass(frozen=True, slots=True)
class Part:
    """
    One piece of what a model is shown.

    ``slot`` is the application's name for it (NVM's ``binding``, ``technique``,
    ``context``…). ``rank`` orders parts within a register for the budget: higher
    survives longer. ``sources`` cites what this part was built from; it is checked
    against what the grant actually handed out, so a part cannot cite what it never
    read.
    """

    slot: str
    register: Register
    content: JsonValue
    rank: int = 0
    sources: tuple[Source, ...] = ()


@dataclass(frozen=True, slots=True)
class Context:
    """
    What one model call is shown, and everything needed to say why.

    ``read`` is every source the grant's slices handed out while the assembler ran,
    whether or not a part cites it. That is the field a firewall test asserts on:
    it is filled by the slices, so it does not depend on the assembler being honest.
    ``shed`` is what the budget dropped — what the model was *not* shown.
    """

    role: str
    upto_seq: int | None
    parts: tuple[Part, ...]
    shed: tuple[Part, ...] = ()
    read: tuple[Source, ...] = ()

    def describe(self) -> JsonValue:
        """The context as plain data, for a golden test or a log line."""
        return {
            "role": self.role,
            "upto_seq": self.upto_seq,
            "parts": [_part(p) for p in self.parts],
            "shed": [_part(p) for p in self.shed],
            "read": [_source(s) for s in self.read],
        }


def _part(part: Part) -> JsonValue:
    return {
        "slot": part.slot,
        "register": part.register.value,
        "rank": part.rank,
        "content": part.content,
        "sources": [_source(s) for s in part.sources],
    }


def _source(source: Source) -> JsonValue:
    match source:
        case EventRef():
            return {"event": str(source.event_id), "seq": source.seq}
        case StateRef():
            return {
                "state": source.belief_id,
                "scope": source.scope_id,
                "event": str(source.event_id),
            }
        case SectionRef():
            return {"section": source.section_id, "from": source.from_seq, "to": source.to_seq}
        case GivenRef():
            return {
                "given": source.name,
                "event": None if source.event_id is None else str(source.event_id),
            }

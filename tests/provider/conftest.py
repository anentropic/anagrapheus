"""
Wiring shared by the provider suite.

Every test here counts model calls, because that is the assertion the whole layer is
about: a pinned call is one the model never sees a second time. `FunctionModel` makes
the count observable, which no real model would.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import pytest
from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.models.function import FunctionModel

from anagrapheus.timeline import (
    EventTypeRegistry,
    PinLedger,
    SQLiteEventStore,
    register_builtin_events,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from pathlib import Path

    from pydantic_ai.messages import ModelMessage
    from pydantic_ai.models.function import AgentInfo


@dataclass
class Counted:
    """A `FunctionModel` that records how many times it was actually asked."""

    reply: Callable[[list[ModelMessage], AgentInfo], ModelResponse]
    calls: list[list[ModelMessage]] = field(default_factory=list)

    def __call__(self, messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        self.calls.append(messages)
        return self.reply(messages, info)

    @property
    def count(self) -> int:
        return len(self.calls)

    def model(self) -> FunctionModel:
        return FunctionModel(self)


def says(text: str) -> Callable[[list[ModelMessage], AgentInfo], ModelResponse]:
    """A reply function that always answers with the same text."""

    def reply(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        return ModelResponse(parts=[TextPart(text)])

    return reply


@dataclass
class Rig:
    """A store, a registry and a live ledger over one file."""

    store: SQLiteEventStore
    types: EventTypeRegistry
    ledger: PinLedger


@pytest.fixture
def rig(tmp_path: Path) -> Iterator[Rig]:
    types = EventTypeRegistry()
    register_builtin_events(types)
    store = SQLiteEventStore(tmp_path / "agent.db")
    try:
        yield Rig(store=store, types=types, ledger=PinLedger(store, types))
    finally:
        store.close()

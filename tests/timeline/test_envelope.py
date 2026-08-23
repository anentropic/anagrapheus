"""Envelope defaults and immutability."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from anagrapheus.timeline.envelope import Envelope, PendingEvent, StoredEvent


def _envelope(**overrides: object) -> Envelope:
    defaults: dict[str, object] = {"namespace": "demo", "type": "X", "schema_version": 1}
    defaults.update(overrides)
    return Envelope.model_validate(defaults)


def test_event_id_is_uuid7():
    assert _envelope().event_id.version == 7


def test_created_at_is_aware_utc():
    created = _envelope().created_at
    assert created.tzinfo is not None
    assert created.utcoffset() == datetime.now(tz=UTC).utcoffset()


def test_schema_version_must_be_positive():
    with pytest.raises(ValidationError):
        _envelope(schema_version=0)


def test_envelope_is_frozen():
    with pytest.raises(ValidationError):
        _envelope().type = "Y"  # type: ignore[misc]


def test_stored_event_requires_positive_seq():
    envelope = _envelope()
    with pytest.raises(ValidationError):
        StoredEvent(seq=0, envelope=envelope, payload={})
    assert StoredEvent(seq=1, envelope=envelope, payload={}).seq == 1


def test_pending_event_round_trips_payload():
    pending = PendingEvent(envelope=_envelope(), payload={"n": 3})
    assert pending.payload == {"n": 3}

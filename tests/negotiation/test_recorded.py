"""
The negotiation, replayed from a recording committed to the repository.

Every other replay in this suite records and replays inside one test. This one reads a
file made earlier, by another process, and shares nothing with it but the bytes — which
is the form of the replay claim a consumer actually relies on.

**It is also a regression test on what the model is asked.** Change a prompt and the
pins for that prompt miss, so this fails. That is wanted: change the prompt assembly and
the keys miss, made visible in review rather than discovered in a
deployed agent.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from examples.negotiation.scripted import scripted
from examples.negotiation.story import negotiate

from anagrapheus.timeline import PinMiss, ReplayPins, UpcastRegistry, read_recording
from tests.fixtures.record import NEGOTIATION, NEGOTIATION_LIVE, replay_lineup

if TYPE_CHECKING:
    from pathlib import Path


def test_the_committed_negotiation_replays_from_disk(tmp_path: Path):
    recording = read_recording(NEGOTIATION)
    reached: list[str] = []

    try:
        run = negotiate(
            tmp_path / "replay.db",
            models=scripted(reached.append),
            resolver=lambda table: ReplayPins.from_events(
                # Upcast on the way in, as anything read from a log is: the recording
                # holds events exactly as they were stored, at the version they were.
                recording.events,
                table.types,
                upcasts=UpcastRegistry(),
                on_miss="strict",
            ),
        )
    except PinMiss as miss:
        pytest.fail(
            f"{NEGOTIATION.name} no longer replays: a request changed since it was "
            f"recorded through {recording.header['lineup']}. If the change to what the "
            f"model is asked was deliberate, run `{recording.header['rerecord']}` and "
            f"commit the diff with it. First miss: {miss}"
        )

    try:
        assert reached == [], "every draw answered from the file"
        assert run.lineup.describe(run.table.parties) == recording.header["lineup"], (
            "replayed through the route it was recorded through"
        )
        final = run.rounds[-1]
        assert final.turn("uk").offer.concede and not final.turn("eu").offer.concede, (
            "and it is the same negotiation: the UK concedes in round four"
        )
    finally:
        run.table.close()


def test_a_real_route_recording_replays_offline_without_its_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """
    Real response shapes, in the default suite, on every commit, with no secret.

    Every other pin in tiers 0–2 came from a ``FunctionModel``. This corpus was recorded
    through OpenRouter on a real model, so replaying it runs the pin round trip over
    shapes nothing synthetic produces — tier 3's question, asked continuously instead of
    at release.

    The keys are removed first, so "needs no key" is tested rather than assumed: the
    recorded models are rebuilt with a placeholder credential that a strict replay never
    sends, because it raises at the first miss before any request is made.
    """
    for key in ("OPENROUTER_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    recording = read_recording(NEGOTIATION_LIVE)
    responses = [
        stored.payload["result"]
        for stored in recording.events
        if stored.payload.get("kind") == "model_call"
    ]
    assert responses and all(
        isinstance(r, dict) and r.get("provider_name") == "openrouter" for r in responses
    ), "every model call in this corpus came from a real provider, not a scripted one"

    try:
        run = negotiate(
            tmp_path / "replay.db",
            models=replay_lineup(recording.header),
            resolver=lambda table: ReplayPins.from_events(
                recording.events, table.types, upcasts=UpcastRegistry(), on_miss="strict"
            ),
        )
    except PinMiss as miss:
        pytest.fail(
            f"{NEGOTIATION_LIVE.name} no longer replays: a request changed since it was "
            f"recorded through {recording.header['lineup']}. If the change was deliberate, "
            f"re-record with `{recording.header['rerecord']}` and commit the diff. "
            f"First miss: {miss}"
        )

    try:
        assert run.lineup.describe(run.table.parties) == recording.header["lineup"]
        assert len(run.rounds) == 4
        assert all(t.reading.predicate == "red_line" for r in run.rounds for t in r.turns)
    finally:
        run.table.close()

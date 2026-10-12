"""
The browser UI's backend, run headless: what it streams is what the story did.

The page itself is not tested here; what is, is the claim it renders -- that every
frame is the table as it stood after one append, and that the log lines arrive stamped
with where in the story they happened.
"""

from __future__ import annotations

import json
import logging
import threading
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any

import pytest
from examples.negotiation.scripted import scripted
from examples.negotiation.story import negotiate
from examples.negotiation.ui.server import serve
from examples.negotiation.ui.session import WATCHED_LOGGERS, Hub, Runner

if TYPE_CHECKING:
    from collections.abc import Generator, Iterator
    from pathlib import Path

    from examples.negotiation.table import Table

    from anagrapheus.timeline import PendingEvent

type Message = dict[str, Any]


@contextmanager
def _wired() -> Generator[tuple[Hub, Runner]]:
    """A runner wired to the loggers as ``__main__`` wires it, and unwired after."""
    hub = Hub()
    runner = Runner(hub)
    saved = {name: logging.getLogger(name).level for name in WATCHED_LOGGERS}
    for name in WATCHED_LOGGERS:
        logging.getLogger(name).setLevel(logging.DEBUG)
        logging.getLogger(name).addHandler(runner)
    try:
        yield hub, runner
    finally:
        for name in WATCHED_LOGGERS:
            logging.getLogger(name).removeHandler(runner)
            logging.getLogger(name).setLevel(saved[name])


@pytest.fixture
def runner() -> Iterator[tuple[Hub, Runner]]:
    with _wired() as wired:
        yield wired


def _play(hub: Hub, runner: Runner) -> list[Message]:
    assert runner.start([]) is None
    messages: list[Message] = []
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        fresh = hub.since(len(messages), timeout=1)
        messages += fresh
        if any(m["type"] in ("done", "failed") for m in fresh):
            return messages
    pytest.fail("the run did not finish within 30s")


@pytest.fixture(scope="module")
def played() -> Iterator[list[Message]]:
    """One run, read by every test that only looks at what it streamed."""
    with _wired() as wired:
        yield _play(*wired)


def _frames(messages: list[Message], stage: str) -> list[Message]:
    return [m for m in messages if m["type"] == "frame" and m["stage"] == stage]


def _calls(messages: list[Message], stage: str) -> list[str]:
    return [
        m["message"]
        for m in messages
        if m["type"] == "log"
        and m["stage"] == stage
        and m["logger"] == "anagrapheus.provider.model"
    ]


def test_a_run_plays_live_then_replays(played: list[Message]):
    assert played[0]["type"] == "run"
    assert played[-1]["type"] == "done", played[-1]
    assert "none from a model" in played[-1]["verdict"]
    assert _frames(played, "live") and _frames(played, "replay")


def test_every_event_appears_in_exactly_one_frame(played: list[Message]):
    """A frame per append, plus the closing one that carries the promoted pins."""
    seqs = [e["seq"] for f in _frames(played, "live") for e in f["events"]]
    assert seqs == list(range(1, len(seqs) + 1))
    closing = _frames(played, "live")[-1]
    assert {e["type"] for e in closing["events"]} == {"PinPromoted"}


def test_the_replay_reaches_no_model(played: list[Message]):
    live, replayed = _calls(played, "live"), _calls(played, "replay")
    assert any(" drawn " in line for line in live)
    assert replayed, "the replay makes the same calls -- answered from the log"
    assert all("reused from pin" in line for line in replayed)


def test_the_second_order_check_matches_the_story_at_each_round_end(
    played: list[Message], tmp_path: Path
):
    """
    The page's second-order check is the story's :class:`Mismatch`, measured again.

    Taken at each ``RoundClosed`` frame and compared with what the story itself records
    once a round -- so the red banner the page shows after round one is the same claim
    the acceptance test makes, not a second opinion that could drift from it.
    """
    story = negotiate(tmp_path / "story.db", models=scripted())
    try:
        expected = [m.wrong for m in story.second_order]
    finally:
        story.table.close()
    closes = [
        f for f in _frames(played, "live") if any(e["type"] == "RoundClosed" for e in f["events"])
    ]
    eu_reads_uk = [
        next(
            e for e in f["snapshot"]["second_order"] if (e["reader"], e["subject"]) == ("eu", "uk")
        )
        for f in closes
    ]
    assert [e["wrong"] for e in eu_reads_uk] == expected
    assert expected[0], "the story's point: the UK is wrong after round one"


def test_beliefs_are_shown_by_name(played: list[Message]):
    scopes = _frames(played, "live")[-1]["snapshot"]["scopes"]
    uk_held = next(s for s in scopes if s["scope"] == "party:uk:held")
    position = next(b for b in uk_held["beliefs"] if b["label"] == "position(uk, fisheries)")
    assert position["value"] == "concede"
    assert position["source"].endswith("ClauseTabled")


def test_round_steps_label_the_frames(played: list[Message]):
    phases = {f["phase"] for f in _frames(played, "live")}
    assert {"round 1: say", "round 1: leak", "round 1: read", "round 1: close"} <= phases


def test_log_lines_know_which_frame_they_followed(played: list[Message]):
    frames = len(_frames(played, "live"))
    logs = [m for m in played if m["type"] == "log" and m["stage"] == "live"]
    assert all(-1 <= m["frame"] < frames for m in logs)
    assert [m["frame"] for m in logs] == sorted(m["frame"] for m in logs)


def test_a_bad_lineup_is_refused_without_starting(runner: tuple[Hub, Runner]):
    hub, run = runner
    error = run.start(["updaet=x"])
    assert error is not None and "unknown role 'updaet'" in error
    assert hub.since(0, timeout=0) == []


def test_only_one_run_at_a_time(runner: tuple[Hub, Runner]):
    hub, run = runner
    assert run.start([]) is None
    assert run.start([]) == "a negotiation is already running; wait for it to finish"
    _drain(hub)


def _drain(hub: Hub) -> None:
    n, deadline = 0, time.monotonic() + 30
    while time.monotonic() < deadline:
        fresh = hub.since(n, timeout=1)
        n += len(fresh)
        if any(m["type"] in ("done", "failed") for m in fresh):
            return
    # Falling through would pass the test and leave a hung run behind it.
    pytest.fail("the run did not finish within 30s")


def test_the_table_reports_every_append(table: Table):
    seen: list[tuple[int, int]] = []

    def listener(events: tuple[PendingEvent, ...], last: int) -> None:
        seen.append((len(events), last))

    table.on_append.append(listener)
    table.table_clause("fisheries")
    # `table_clause` appends twice: the clause as an entity, then `ClauseTabled`.
    assert seen == [(1, 3), (1, 4)]


# ---- the server ------------------------------------------------------------------------


@pytest.fixture
def server(runner: tuple[Hub, Runner]) -> Iterator[str]:
    hub, run = runner
    httpd = serve(hub, run, ["anthropic:claude-haiku-5-5"], host="127.0.0.1", port=0)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}"
    finally:
        httpd.shutdown()
        httpd.server_close()


def _get(url: str) -> tuple[int, bytes]:
    try:
        with urllib.request.urlopen(url, timeout=5) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, b""


def test_the_page_and_its_config_are_served(server: str):
    status, body = _get(server + "/")
    assert status == 200 and b"<title>" in body
    config = json.loads(_get(server + "/config")[1])
    assert config == {"models": ["anthropic:claude-haiku-5-5"], "history": 0}


def test_nothing_outside_static_is_served(server: str):
    assert _get(server + "/static/../server.py")[0] == 404
    assert _get(server + "/static/%2e%2e/server.py")[0] == 404


def test_a_bad_run_request_is_refused_with_the_reason(server: str):
    request = urllib.request.Request(
        server + "/run",
        data=json.dumps({"models": ["updaet=x"]}).encode(),
        headers={"Content-Type": "application/json"},
    )
    with pytest.raises(urllib.error.HTTPError) as refused:
        urllib.request.urlopen(request, timeout=5)
    assert refused.value.code == 400
    assert "unknown role" in json.loads(refused.value.read())["error"]


def test_the_second_order_check_runs_both_ways(played: list[Message]):
    """
    Each side's read of the other, checked in both directions, not only the staged one.

    The scripted story fools the UK, but a real model may misjudge either side or both,
    and a page that only asked about the UK would report nothing when the EU is fooled.
    """
    final = _frames(played, "live")[-1]["snapshot"]["second_order"]
    pairs = {(e["reader"], e["subject"]) for e in final}
    assert pairs == {("eu", "uk"), ("uk", "eu")}
    after_round_one = next(
        f for f in _frames(played, "live") if any(e["type"] == "RoundClosed" for e in f["events"])
    )["snapshot"]["second_order"]
    wrong = {(e["reader"], e["subject"]) for e in after_round_one if e["wrong"]}
    assert wrong == {("eu", "uk")}, "the story's mistake, and only that one, after round one"


def _post(url: str, body: bytes, headers: dict[str, str]) -> int:
    request = urllib.request.Request(url, data=body, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status
    except urllib.error.HTTPError as error:
        return error.code


def test_a_cross_site_form_post_cannot_start_a_run(server: str, runner: tuple[Hub, Runner]):
    """
    Review finding: ``/run`` parsed JSON whatever the content type said.

    A ``text/plain`` POST is one any website can send to a loopback server without a
    CORS preflight -- and it would have started a run on this process's provider keys.
    """
    hub, _ = runner
    body = json.dumps({"models": []}).encode()
    assert _post(server + "/run", body, {"Content-Type": "text/plain"}) == 415
    assert hub.since(0, timeout=0) == [], "no run started"


def test_a_page_from_elsewhere_is_refused(server: str, runner: tuple[Hub, Runner]):
    """Requests must name this server as ``Host`` and, if they carry one, ``Origin``."""
    hub, _ = runner
    body = json.dumps({"models": []}).encode()
    json_type = {"Content-Type": "application/json"}
    assert _post(server + "/run", body, {**json_type, "Origin": "https://evil.test"}) == 403
    # A DNS-rebinding page reaches 127.0.0.1 under its own name, which `Host` carries.
    rebound = urllib.request.Request(server + "/events", headers={"Host": "evil.test"})
    with pytest.raises(urllib.error.HTTPError) as refused:
        urllib.request.urlopen(rebound, timeout=5)
    assert refused.value.code == 403
    assert hub.since(0, timeout=0) == []


def test_the_server_will_not_listen_beyond_loopback(runner: tuple[Hub, Runner]):
    hub, run = runner
    with pytest.raises(ValueError, match="loopback only"):
        serve(hub, run, [], host="0.0.0.0", port=0)

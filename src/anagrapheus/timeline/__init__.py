"""
The event-sourced timeline: the agent's authoritative record.

One append-only stream is what happened. Everything else — memory, state, indexes — is
a projection that can be dropped and rebuilt. Nondeterminism is pinned by key, so a run
replays or says exactly where it diverged.
"""

__all__: list[str] = []

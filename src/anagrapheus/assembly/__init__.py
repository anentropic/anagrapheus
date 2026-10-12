"""
Prompt assembly: what a model is shown, built only from what its role was granted.

Doc 00 §2 draws "transcript / prompt assembly" as a projection of the log and says no
more. This package is the shape note 07 settled for it:

- a **grant** of :mod:`~anagrapheus.assembly.slices`, each reading at one position and
  remembering what it handed out, so a role can read nothing it was not granted and a
  context always knows its sources;
- **parts** carrying a register, a rank and their sources, structured rather than
  rendered (:mod:`~anagrapheus.assembly.parts`);
- :func:`~anagrapheus.assembly.assemble.assemble`, which checks citations and labels
  and fits a :class:`~anagrapheus.assembly.assemble.Budget` that never sheds a binding
  part.

Rendering, slot names, budgets and every schema are the application's. Pinning does
not change: the rendered messages go through
:class:`~anagrapheus.provider.PinnedModel` as before, and its content-addressed key
makes any assembly change a miss.
"""

from __future__ import annotations

from anagrapheus.assembly.assemble import DEFAULT_SHED_ORDER, Budget, assemble, slices_of
from anagrapheus.assembly.errors import (
    AssemblyError,
    BudgetExceeded,
    MislabelledPart,
    NotGranted,
    StaleRead,
    UncitedSource,
)
from anagrapheus.assembly.parts import (
    Context,
    EventRef,
    GivenRef,
    Part,
    Register,
    SectionRef,
    Source,
)
from anagrapheus.assembly.render import render_labelled
from anagrapheus.assembly.slices import (
    BeliefSlice,
    ChronicleSlice,
    EventsSlice,
    Given,
    LogSlice,
    Slice,
    handed_out,
)

__all__ = [
    "DEFAULT_SHED_ORDER",
    "AssemblyError",
    "BeliefSlice",
    "Budget",
    "BudgetExceeded",
    "ChronicleSlice",
    "Context",
    "EventRef",
    "EventsSlice",
    "Given",
    "GivenRef",
    "LogSlice",
    "MislabelledPart",
    "NotGranted",
    "Part",
    "Register",
    "SectionRef",
    "Slice",
    "Source",
    "StaleRead",
    "UncitedSource",
    "assemble",
    "handed_out",
    "render_labelled",
    "slices_of",
]

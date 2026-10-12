"""
Putting a context together: run the assembler, check what it built, fit the budget.

``assemble`` is small on purpose. The assembler is the application's and is required
to be pure; what the library adds is the four checks that make a context trustworthy
whatever the assembler does:

- **The grant holds slices and nothing else.** A grant is a dataclass, and a field
  that is not a :class:`~anagrapheus.assembly.slices.Slice` is refused, because a
  store or a table in a grant is a way round every other check here.
- **The slices agree on a position.** Two slices at different positions would build a
  context no single moment of the log describes.
- **A part cites only what was handed out**, and **prose is labelled as prose**: a
  part citing a chronicle section must be ``generated``, and every section read must
  be cited by one. That catches prose that loses its citation; it cannot stop an
  assembler that cites a section *and* copies its words elsewhere, which is the limit
  of checking structure rather than text.
- **A grant is read for one assembly.** Its slices must be unread when it arrives, so
  the context's ``read`` is exactly this call's.
- **The budget never sheds a binding part**, and says what it shed.

**Determinism is the contract this rests on** (note 07 §4). Same log, same position,
same grant: same parts, so the same rendered bytes, so a replay hits its pins. Nothing
here can check that an assembler is pure, so the golden test does instead.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from typing import TYPE_CHECKING

from anagrapheus.assembly.errors import (
    AssemblyError,
    BudgetExceeded,
    MislabelledPart,
    UncitedSource,
)
from anagrapheus.assembly.parts import Context, Register, SectionRef
from anagrapheus.assembly.slices import Slice, handed_out

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Sequence

    from anagrapheus.assembly.parts import Part

__all__ = ["DEFAULT_SHED_ORDER", "Budget", "assemble", "slices_of"]

#: Which registers a budget sheds first. ``binding`` is never in it.
DEFAULT_SHED_ORDER: tuple[Register, ...] = (
    Register.considered,
    Register.generated,
    Register.craft,
)


@dataclass(frozen=True, slots=True)
class Budget:
    """
    How much a context may hold, and what goes first when it holds too much.

    ``count`` is the application's token counter — the library does not guess at one.
    Parts are shed register by register in ``order``, lowest ``rank`` first and, among
    equals, the later part first. ``binding`` may not appear in ``order``: "the binding
    layer never sheds" (U42).
    """

    total: int
    count: Callable[[Part], int]
    order: tuple[Register, ...] = DEFAULT_SHED_ORDER

    def __post_init__(self) -> None:
        if Register.binding in self.order:
            raise ValueError("a budget may never shed binding parts")

    def fit(self, parts: Sequence[Part]) -> tuple[tuple[Part, ...], tuple[Part, ...]]:
        """``(kept, shed)``, with ``kept`` in assembly order."""
        sizes = [self.count(p) for p in parts]
        binding = sum(
            n for p, n in zip(parts, sizes, strict=True) if p.register is Register.binding
        )
        if binding > self.total:
            raise BudgetExceeded(
                f"the binding parts alone come to {binding} against a total of {self.total}; "
                f"a call that cannot carry its invariants is not made"
            )
        used = sum(sizes)
        dropped: set[int] = set()
        for register in self.order:
            candidates = sorted(
                (i for i, p in enumerate(parts) if p.register is register),
                key=lambda i: (parts[i].rank, -i),
            )
            for i in candidates:
                if used <= self.total:
                    break
                dropped.add(i)
                used -= sizes[i]
        if used > self.total:
            raise BudgetExceeded(
                f"{used} remain against a total of {self.total} after shedding every "
                f"register this budget may shed"
            )
        kept = tuple(p for i, p in enumerate(parts) if i not in dropped)
        shed = tuple(p for i, p in enumerate(parts) if i in dropped)
        return kept, shed


def slices_of(grant: object) -> tuple[Slice, ...]:
    """
    A grant's slices, refusing a grant that holds anything else.

    Frozen, so the assembler cannot swap a field for a store after the check has run.
    """
    if not dataclasses.is_dataclass(grant) or isinstance(grant, type):
        raise TypeError(f"a grant is a dataclass instance of slices, not {type(grant).__name__}")
    params: object = getattr(type(grant), "__dataclass_params__", None)
    if not getattr(params, "frozen", False):
        raise TypeError(
            f"grant {type(grant).__name__} is not frozen; an assembler could replace a "
            f"slice with a store after the grant was checked"
        )
    found: list[Slice] = []
    for field in dataclasses.fields(grant):
        value: object = getattr(grant, field.name)
        if not isinstance(value, Slice):
            raise TypeError(
                f"grant field {field.name!r} holds a {type(value).__name__}, which is not a "
                f"slice; a grant that carries a store is a way round the firewall"
            )
        found.append(value)
    return tuple(found)


def assemble[G](
    role: str,
    assembler: Callable[[G], Iterable[Part]],
    grant: G,
    *,
    budget: Budget | None = None,
) -> Context:
    """
    Run ``assembler`` over ``grant`` and return what ``role`` will be shown.

    Raises :class:`~anagrapheus.assembly.errors.UncitedSource` for a part citing what
    the grant never handed out, :class:`~anagrapheus.assembly.errors.MislabelledPart`
    for prose presented as anything but ``generated``, and
    :class:`~anagrapheus.assembly.errors.BudgetExceeded` when the binding parts alone
    do not fit.
    """
    slices = slices_of(grant)
    positions = {s.upto_seq for s in slices if s.upto_seq is not None}
    if len(positions) > 1:
        raise AssemblyError(
            f"the slices in this grant read at different positions {sorted(positions)}; a "
            f"context must describe one moment of the log"
        )
    if handed_out(slices):
        # Ledgers live on the slices, so a reused grant would carry the last call's
        # reads into this one's context, and let a part cite what this call never read.
        raise AssemblyError(
            "this grant was already read from; a grant is opened for one assembly, so "
            "its context describes that call and no other"
        )
    parts = tuple(assembler(grant))
    read = handed_out(slices)
    known = set(read)
    labelled: set[SectionRef] = set()
    for part in parts:
        for source in part.sources:
            if source not in known:
                raise UncitedSource(
                    f"part {part.slot!r} cites {source}, which this grant never handed out"
                )
            if isinstance(source, SectionRef):
                if part.register is not Register.generated:
                    raise MislabelledPart(
                        f"part {part.slot!r} cites chronicle prose as {part.register.value}; "
                        f"model-written prose is advisory and must be labelled generated"
                    )
                labelled.add(source)
    unlabelled = [s for s in read if isinstance(s, SectionRef) and s not in labelled]
    if unlabelled:
        # Checking only what parts cite would let prose in under another register by
        # leaving the citation off. Every section read must be accounted for by a
        # generated part, which makes an omitted citation an error rather than a bypass.
        raise MislabelledPart(
            f"{len(unlabelled)} chronicle section(s) were read and no generated part cites "
            f"them ({unlabelled[0].section_id!r} first); read prose only to show it as prose"
        )
    kept, shed = (parts, ()) if budget is None else budget.fit(parts)
    return Context(
        role=role,
        upto_seq=next(iter(positions), None),
        parts=kept,
        shed=shed,
        read=read,
    )

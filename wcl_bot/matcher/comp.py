"""Healer composition slot definitions + matching logic."""
from __future__ import annotations

import itertools
from dataclasses import dataclass
from typing import Sequence

# Retail healer specs as of TWW. Used to normalize user spec input and to
# validate that an "any spec of this class" slot is actually a healer class.
HEALER_SPECS: dict[str, frozenset[str]] = {
    "Priest": frozenset({"Holy", "Discipline"}),
    "Paladin": frozenset({"Holy"}),
    "Druid": frozenset({"Restoration"}),
    "Shaman": frozenset({"Restoration"}),
    "Monk": frozenset({"Mistweaver"}),
    "Evoker": frozenset({"Preservation"}),
}

# Common short forms users type. Maps to canonical (Class, Spec).
_SPEC_ALIASES: dict[str, tuple[str, str]] = {
    "hpriest": ("Priest", "Holy"),
    "holypriest": ("Priest", "Holy"),
    "disc": ("Priest", "Discipline"),
    "discpriest": ("Priest", "Discipline"),
    "hpal": ("Paladin", "Holy"),
    "holypal": ("Paladin", "Holy"),
    "holypaladin": ("Paladin", "Holy"),
    "rdruid": ("Druid", "Restoration"),
    "resto": ("Druid", "Restoration"),  # ambiguous, defaults to druid
    "restodruid": ("Druid", "Restoration"),
    "rsham": ("Shaman", "Restoration"),
    "rshaman": ("Shaman", "Restoration"),
    "restoshaman": ("Shaman", "Restoration"),
    "mw": ("Monk", "Mistweaver"),
    "mistweaver": ("Monk", "Mistweaver"),
    "preg": ("Evoker", "Preservation"),
    "presevoker": ("Evoker", "Preservation"),
    "preservation": ("Evoker", "Preservation"),
}


def _normalize_class(name: str) -> str:
    name = name.strip().title()
    if name not in HEALER_SPECS:
        raise ValueError(
            f"Unknown / non-healer class: {name!r}. "
            f"Expected one of: {sorted(HEALER_SPECS)}"
        )
    return name


def _normalize_spec(wow_class: str, spec: str) -> str:
    s = spec.strip().title()
    valid = HEALER_SPECS[wow_class]
    if s not in valid:
        raise ValueError(
            f"{s!r} is not a healer spec for {wow_class}. "
            f"Valid: {sorted(valid)}"
        )
    return s


@dataclass(frozen=True)
class HealerSlot:
    wow_class: str
    spec: str | None = None  # None = any healer spec of this class

    @classmethod
    def create(cls, wow_class: str, spec: str | None = None) -> HealerSlot:
        wc = _normalize_class(wow_class)
        sp = _normalize_spec(wc, spec) if spec else None
        return cls(wow_class=wc, spec=sp)

    def matches(self, player_class: str, player_spec: str) -> bool:
        if player_class.title() != self.wow_class:
            return False
        if self.spec is None:
            return player_spec.title() in HEALER_SPECS[self.wow_class]
        return player_spec.title() == self.spec


@dataclass(frozen=True)
class TargetComp:
    slots: tuple[HealerSlot, ...]

    def matches(self, healers: Sequence[tuple[str, str]]) -> bool:
        """True iff healers can be assigned 1:1 to slots, every slot satisfied.

        Requires exact count: len(healers) must equal len(slots)."""
        if len(healers) != len(self.slots):
            return False
        return self._can_assign(healers)

    def is_subset_of(self, healers: Sequence[tuple[str, str]]) -> bool:
        """True iff the comp's slots can be filled by some subset of `healers`.

        Used for the 'include fights with more healers' mode — a 4-healer comp
        matches a 6-healer fight if 4 of those 6 healers satisfy the slots."""
        if len(healers) < len(self.slots):
            return False
        return self._can_assign(healers)

    def _can_assign(self, healers: Sequence[tuple[str, str]]) -> bool:
        # Brute-force permutations — sized for slot counts <= 8.
        for perm in itertools.permutations(healers, len(self.slots)):
            if all(slot.matches(c, s) for slot, (c, s) in zip(self.slots, perm)):
                return True
        return False


def parse_comp_spec(spec: str) -> TargetComp:
    """Parse a CLI comp string like 'Priest/Holy,Paladin,rdruid,preg'.

    Each token is one of:
      - 'Class'           — any healer spec of this class
      - 'Class/Spec'      — exact spec
      - shortcut alias    — e.g. 'hpriest', 'rdruid', 'preg' (see _SPEC_ALIASES)
    """
    slots: list[HealerSlot] = []
    for raw in spec.split(","):
        token = raw.strip()
        if not token:
            continue
        alias_key = token.lower().replace(" ", "").replace("-", "")
        if alias_key in _SPEC_ALIASES:
            cls_name, spec_name = _SPEC_ALIASES[alias_key]
            slots.append(HealerSlot.create(cls_name, spec_name))
        elif "/" in token:
            cls_part, spec_part = token.split("/", 1)
            slots.append(HealerSlot.create(cls_part, spec_part))
        else:
            slots.append(HealerSlot.create(token))
    if not slots:
        raise ValueError(f"No healer slots parsed from: {spec!r}")
    return TargetComp(slots=tuple(slots))

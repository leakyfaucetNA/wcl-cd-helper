"""Tracked healer raid cooldowns by (class, spec).

Curated by the user. To add a spell: append a CooldownSpell to the relevant
spec's tuple — no other code changes required. Each spell has a primary
`spell_id`; `aliases` lets one slot cover talent-replacement variants
(e.g. Avenging Wrath ↔ Avenging Crusader, Revival ↔ Restoral) so the
extraction layer counts them as the same coordinated cooldown.

All current IDs were verified against gameData.ability on 2026-05-23.
Re-run `python -m examples.verify_spell_ids` after major patches.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class CooldownSpell:
    spell_id: int
    name: str
    category: str  # "raid" or "external"
    aliases: tuple[int, ...] = field(default_factory=tuple)
    display_name: str | None = None  # falls back to `name`

    @property
    def all_ids(self) -> tuple[int, ...]:
        return (self.spell_id, *self.aliases)

    @property
    def label(self) -> str:
        return self.display_name or self.name


HEALER_COOLDOWNS: dict[tuple[str, str], tuple[CooldownSpell, ...]] = {
    ("Priest", "Holy"): (
        CooldownSpell(64843, "Divine Hymn", "raid"),
        CooldownSpell(200183, "Apotheosis", "raid"),
        CooldownSpell(47788, "Guardian Spirit", "external"),
        CooldownSpell(32375, "Mass Dispel", "raid"),
    ),
    ("Priest", "Discipline"): (
        CooldownSpell(246287, "Evangelism", "raid"),
        CooldownSpell(421453, "Ultimate Penitence", "raid"),
        CooldownSpell(33206, "Pain Suppression", "external"),
        CooldownSpell(32375, "Mass Dispel", "raid"),
    ),
    ("Paladin", "Holy"): (
        CooldownSpell(31821, "Aura Mastery", "raid"),
        # Avenging Crusader (216331) is a talent that replaces Avenging Wrath (31884).
        CooldownSpell(
            31884,
            "Avenging Wrath",
            "raid",
            aliases=(216331,),
            display_name="Avenging Wrath / Crusader",
        ),
        CooldownSpell(6940, "Blessing of Sacrifice", "external"),
    ),
    ("Druid", "Restoration"): (
        CooldownSpell(740, "Tranquility", "raid"),
        # UNCERTAIN: post-DF Convoke is 391528 for Resto; verify.
        CooldownSpell(391528, "Convoke the Spirits", "raid"),
        CooldownSpell(102342, "Ironbark", "external"),
    ),
    ("Shaman", "Restoration"): (
        CooldownSpell(98008, "Spirit Link Totem", "raid"),
        CooldownSpell(108280, "Healing Tide Totem", "raid"),
        CooldownSpell(114052, "Ascendance", "raid"),
    ),
    ("Monk", "Mistweaver"): (
        # Restoral (388615) is a talent that replaces Revival (115310).
        CooldownSpell(
            115310,
            "Revival",
            "raid",
            aliases=(388615,),
            display_name="Revival / Restoral",
        ),
        # Yu'lon and Chi-Ji are two talent choices for the celestial CD slot.
        CooldownSpell(
            322118,
            "Invoke Yu'lon, the Jade Serpent",
            "raid",
            aliases=(325197,),
            display_name="Yu'lon / Chi-Ji",
        ),
        CooldownSpell(443028, "Celestial Conduit", "raid"),
        CooldownSpell(116849, "Life Cocoon", "external"),
    ),
    ("Evoker", "Preservation"): (
        CooldownSpell(363534, "Rewind", "raid"),
        # Stasis fires two cast events: Store begins the buff (cooldown press),
        # Release consumes the stored spells (when the burst healing lands).
        # Tracked separately so notes can show both timings.
        CooldownSpell(370537, "Stasis (Store)", "raid", display_name="Stasis Store"),
        CooldownSpell(370564, "Stasis (Release)", "raid", display_name="Stasis Release"),
        CooldownSpell(359816, "Dream Flight", "raid"),
        CooldownSpell(357170, "Time Dilation", "external"),
        CooldownSpell(374227, "Zephyr", "external"),
    ),
}


# Class-wide raid CDs available to ANY spec of the class. Off by default —
# only included when the user toggles "Include DPS raid CDs" in Settings.
# Spec-specific healing CDs stay in HEALER_COOLDOWNS so they're always on.
CLASS_RAID_COOLDOWNS: dict[str, tuple[CooldownSpell, ...]] = {
    "Warrior": (
        CooldownSpell(97462, "Rallying Cry", "raid"),
    ),
    "Death Knight": (
        CooldownSpell(51052, "Anti-Magic Zone", "raid"),
    ),
    "Demon Hunter": (
        CooldownSpell(196718, "Darkness", "raid"),
    ),
    "Druid": (
        CooldownSpell(106898, "Stampeding Roar", "raid"),
    ),
    "Hunter": (
        CooldownSpell(53480, "Roar of Sacrifice", "external"),
    ),
    "Mage": (
        CooldownSpell(80353, "Time Warp", "raid"),
    ),
    "Rogue": (
        CooldownSpell(76577, "Smoke Bomb", "raid"),
    ),
    "Shaman": (
        # Heroism (32182) is the Horde variant; alias so either cast counts.
        CooldownSpell(
            2825, "Bloodlust", "raid",
            aliases=(32182,), display_name="Bloodlust / Heroism",
        ),
    ),
    "Evoker": (
        CooldownSpell(390386, "Fury of the Aspects", "raid"),
    ),
}


def spells_for(
    wow_class: str,
    spec: str,
    *,
    include_class_raid: bool = False,
) -> tuple[CooldownSpell, ...]:
    """Return tracked cooldowns for the given (class, spec).

    With `include_class_raid=True`, also folds in CLASS_RAID_COOLDOWNS for
    that class — used when the "Include DPS raid CDs" toggle is on, so any
    player of that class (not just healers) gets their raid CDs tracked.
    """
    out = HEALER_COOLDOWNS.get((wow_class.title(), spec.title()), ())
    if include_class_raid:
        out = out + CLASS_RAID_COOLDOWNS.get(wow_class.title(), ())
    return out


def all_tracked_spell_ids() -> set[int]:
    """Every spell ID we care about, across all specs (primary + aliases),
    including class-wide raid CDs."""
    ids = {
        sid
        for spells in HEALER_COOLDOWNS.values()
        for spell in spells
        for sid in spell.all_ids
    }
    for spells in CLASS_RAID_COOLDOWNS.values():
        for spell in spells:
            ids.update(spell.all_ids)
    return ids

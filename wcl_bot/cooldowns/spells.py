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
    ),
    ("Priest", "Discipline"): (
        CooldownSpell(246287, "Evangelism", "raid"),
        CooldownSpell(421453, "Ultimate Penitence", "raid"),
        CooldownSpell(33206, "Pain Suppression", "external"),
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


def spells_for(wow_class: str, spec: str) -> tuple[CooldownSpell, ...]:
    """Return tracked cooldowns for the given (class, spec), or () if untracked."""
    return HEALER_COOLDOWNS.get((wow_class.title(), spec.title()), ())


def all_tracked_spell_ids() -> set[int]:
    """Every spell ID we care about, across all specs (primary + aliases)."""
    return {
        sid
        for spells in HEALER_COOLDOWNS.values()
        for spell in spells
        for sid in spell.all_ids
    }

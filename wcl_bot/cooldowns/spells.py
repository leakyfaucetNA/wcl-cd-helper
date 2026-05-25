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


# Coarse functional classification used by the class-override remap.
# Map a spell to a cross-class equivalent by matching purpose first.
# Keep this set small — too many buckets makes substitution sparse.
PURPOSE_RAID_BURST_HEAL    = "raid_burst_heal"     # Tranq, Divine Hymn, Healing Tide, Rewind, Revival, ...
PURPOSE_RAID_TURBO_HEAL    = "raid_turbo_heal"     # Apotheosis, Avenging Wrath, Ascendance, celestials
PURPOSE_RAID_DR            = "raid_dr"             # Spirit Link, Aura Mastery, Anti-Magic Zone, Darkness
PURPOSE_SINGLE_EXT_HEAL    = "single_ext_heal"     # Guardian Spirit, Life Cocoon
PURPOSE_SINGLE_EXT_DR      = "single_ext_dr"       # Pain Suppression, Ironbark, BoSac, Time Dilation
PURPOSE_UTILITY            = "utility"             # Mass Dispel, Lust, Rallying Cry, Stampeding Roar


@dataclass(frozen=True)
class CooldownSpell:
    spell_id: int
    name: str
    category: str  # "raid" or "external"
    purpose: str = PURPOSE_UTILITY   # see PURPOSE_* constants; drives cross-class remap
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
        CooldownSpell(64843, "Divine Hymn", "raid", purpose=PURPOSE_RAID_BURST_HEAL),
        CooldownSpell(200183, "Apotheosis", "raid", purpose=PURPOSE_RAID_TURBO_HEAL),
        CooldownSpell(47788, "Guardian Spirit", "external", purpose=PURPOSE_SINGLE_EXT_HEAL),
        CooldownSpell(32375, "Mass Dispel", "raid", purpose=PURPOSE_UTILITY),
    ),
    ("Priest", "Discipline"): (
        CooldownSpell(246287, "Evangelism", "raid", purpose=PURPOSE_RAID_TURBO_HEAL),
        CooldownSpell(421453, "Ultimate Penitence", "raid", purpose=PURPOSE_RAID_BURST_HEAL),
        CooldownSpell(33206, "Pain Suppression", "external", purpose=PURPOSE_SINGLE_EXT_DR),
        CooldownSpell(32375, "Mass Dispel", "raid", purpose=PURPOSE_UTILITY),
    ),
    ("Paladin", "Holy"): (
        CooldownSpell(31821, "Aura Mastery", "raid", purpose=PURPOSE_RAID_DR),
        # Avenging Crusader (216331) is a talent that replaces Avenging Wrath (31884).
        CooldownSpell(
            31884, "Avenging Wrath", "raid",
            purpose=PURPOSE_RAID_TURBO_HEAL,
            aliases=(216331,),
            display_name="Avenging Wrath / Crusader",
        ),
        CooldownSpell(6940, "Blessing of Sacrifice", "external", purpose=PURPOSE_SINGLE_EXT_DR),
    ),
    ("Druid", "Restoration"): (
        CooldownSpell(740, "Tranquility", "raid", purpose=PURPOSE_RAID_BURST_HEAL),
        CooldownSpell(391528, "Convoke the Spirits", "raid", purpose=PURPOSE_RAID_BURST_HEAL),
        CooldownSpell(102342, "Ironbark", "external", purpose=PURPOSE_SINGLE_EXT_DR),
    ),
    ("Shaman", "Restoration"): (
        CooldownSpell(98008, "Spirit Link Totem", "raid", purpose=PURPOSE_RAID_DR),
        CooldownSpell(108280, "Healing Tide Totem", "raid", purpose=PURPOSE_RAID_BURST_HEAL),
        CooldownSpell(114052, "Ascendance", "raid", purpose=PURPOSE_RAID_TURBO_HEAL),
    ),
    ("Monk", "Mistweaver"): (
        # Restoral (388615) is a talent that replaces Revival (115310).
        CooldownSpell(
            115310, "Revival", "raid",
            purpose=PURPOSE_RAID_BURST_HEAL,
            aliases=(388615,),
            display_name="Revival / Restoral",
        ),
        # Yu'lon and Chi-Ji are two talent choices for the celestial CD slot.
        CooldownSpell(
            322118, "Invoke Yu'lon, the Jade Serpent", "raid",
            purpose=PURPOSE_RAID_TURBO_HEAL,
            aliases=(325197,),
            display_name="Yu'lon / Chi-Ji",
        ),
        CooldownSpell(443028, "Celestial Conduit", "raid", purpose=PURPOSE_RAID_BURST_HEAL),
        CooldownSpell(116849, "Life Cocoon", "external", purpose=PURPOSE_SINGLE_EXT_HEAL),
    ),
    ("Evoker", "Preservation"): (
        CooldownSpell(363534, "Rewind", "raid", purpose=PURPOSE_RAID_BURST_HEAL),
        # Stasis Store sets up a burst, Release lands it.
        CooldownSpell(370537, "Stasis (Store)", "raid", purpose=PURPOSE_RAID_TURBO_HEAL, display_name="Stasis Store"),
        CooldownSpell(370564, "Stasis (Release)", "raid", purpose=PURPOSE_RAID_BURST_HEAL, display_name="Stasis Release"),
        CooldownSpell(359816, "Dream Flight", "raid", purpose=PURPOSE_RAID_BURST_HEAL),
        CooldownSpell(357170, "Time Dilation", "external", purpose=PURPOSE_SINGLE_EXT_DR),
        CooldownSpell(374227, "Zephyr", "external", purpose=PURPOSE_RAID_DR),
    ),
}


# Class-wide raid CDs available to ANY spec of the class. Off by default —
# only included when the user toggles "Include DPS raid CDs" in Settings.
# Spec-specific healing CDs stay in HEALER_COOLDOWNS so they're always on.
CLASS_RAID_COOLDOWNS: dict[str, tuple[CooldownSpell, ...]] = {
    "Warrior": (
        CooldownSpell(97462, "Rallying Cry", "raid", purpose=PURPOSE_RAID_DR),
    ),
    "Death Knight": (
        CooldownSpell(51052, "Anti-Magic Zone", "raid", purpose=PURPOSE_RAID_DR),
    ),
    "Demon Hunter": (
        CooldownSpell(196718, "Darkness", "raid", purpose=PURPOSE_RAID_DR),
    ),
    "Druid": (
        CooldownSpell(106898, "Stampeding Roar", "raid", purpose=PURPOSE_UTILITY),
    ),
    "Hunter": (
        CooldownSpell(53480, "Roar of Sacrifice", "external", purpose=PURPOSE_SINGLE_EXT_DR),
    ),
    "Mage": (
        CooldownSpell(80353, "Time Warp", "raid", purpose=PURPOSE_UTILITY),
    ),
    "Rogue": (
        CooldownSpell(76577, "Smoke Bomb", "raid", purpose=PURPOSE_RAID_DR),
    ),
    "Shaman": (
        # Heroism (32182) is the Horde variant; alias so either cast counts.
        CooldownSpell(
            2825, "Bloodlust", "raid",
            purpose=PURPOSE_UTILITY,
            aliases=(32182,), display_name="Bloodlust / Heroism",
        ),
    ),
    "Evoker": (
        CooldownSpell(390386, "Fury of the Aspects", "raid", purpose=PURPOSE_UTILITY),
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


def substitute_for(
    original: CooldownSpell, target_class: str
) -> CooldownSpell | None:
    """Pick the target class's spell that best matches `original`'s purpose.

    Used by the per-healer class-override remap so a Resto Shaman's Spirit
    Link Totem (raid DR) can be rewritten as a Holy Paladin's Aura Mastery
    in the generated note. Preference order:
      1. Same purpose AND same category (raid vs external)
      2. Same purpose, any category
    Returns None if the target class has no candidate — caller decides
    whether to drop the line or fall back.
    """
    target_class = target_class.title() if target_class else ""
    if not target_class:
        return None
    # Collect every tracked spell for this class across all its healer specs
    # plus the class-wide raid CD list.
    candidates: list[CooldownSpell] = []
    for (cls, _spec), spells in HEALER_COOLDOWNS.items():
        if cls == target_class:
            candidates.extend(spells)
    candidates.extend(CLASS_RAID_COOLDOWNS.get(target_class, ()))
    if not candidates:
        return None
    # Prefer purpose+category match, then purpose-only.
    same_purpose = [s for s in candidates if s.purpose == original.purpose]
    if not same_purpose:
        return None
    exact = [s for s in same_purpose if s.category == original.category]
    return exact[0] if exact else same_purpose[0]


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

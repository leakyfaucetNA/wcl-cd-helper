"""WoW raid note formatters: NSRT (Northern Sky Raid Tools) and MRT
(Method Raid Tools) styles.

Both styles share most of the syntax:
    {time:MM:SS[,Label]} |cffXXXXXXName|r {spell:ID}

Differences worth noting:
  - **Color table**: NSRT swaps Priest's pure-white #FFFFFF for #F0EBE0 so
    names are readable on light note backgrounds. MRT uses the canonical
    Blizzard class colors.
  - **Time label**: MRT supports an optional comma-separated label in the
    `{time:...}` token (used by other notes as a cross-reference anchor).
    We emit the spell's display name as the label. NSRT omits the label.
"""
from __future__ import annotations

from enum import Enum

from wcl_bot.cooldowns.extract import FightCooldowns
from wcl_bot.cooldowns.spells import substitute_for


# Canonical Blizzard class colors (ARGB hex, no leading #).
CLASS_COLORS_MRT: dict[str, str] = {
    "Death Knight": "C41E3A",
    "Demon Hunter": "A330C9",
    "Druid": "FF7C0A",
    "Evoker": "33937F",
    "Hunter": "AAD372",
    "Mage": "3FC7EB",
    "Monk": "00FF98",
    "Paladin": "F48CBA",
    "Priest": "FFFFFF",
    "Rogue": "FFF468",
    "Shaman": "0070DD",
    "Warlock": "8788EE",
    "Warrior": "C69B6D",
}

# NSRT uses the same colors except Priest (off-white for readability).
CLASS_COLORS_NSRT: dict[str, str] = {**CLASS_COLORS_MRT, "Priest": "F0EBE0"}


class NoteStyle(str, Enum):
    NSRT = "nsrt"
    MRT = "mrt"


def format_note(
    fc: FightCooldowns,
    style: NoteStyle = NoteStyle.NSRT,
    *,
    name_overrides: dict[str, str] | None = None,
    class_overrides: dict[str, str] | None = None,
) -> str:
    """Emit a paste-ready raid note for the given fight's cooldown timeline.

    `name_overrides` maps WCL player names to display names.
    `class_overrides` maps WCL player names to a *target class*; events for
    that player are rewritten with the target class's equivalent spell (by
    purpose) and their class color in the note. Events with no equivalent
    in the target class are dropped.
    """
    colors = CLASS_COLORS_NSRT if style is NoteStyle.NSRT else CLASS_COLORS_MRT
    n_over = name_overrides or {}
    c_over = class_overrides or {}
    lines: list[str] = []
    for ev in fc.events:
        target_class = c_over.get(ev.healer.name)
        if target_class:
            sub = substitute_for(ev.spell, target_class)
            if sub is None:
                continue  # no equivalent in target class — drop the line
            spell_id = sub.spell_id
            spell_label = sub.label
            display_class = target_class
        else:
            spell_id = ev.cast_spell_id
            spell_label = ev.spell.label
            display_class = ev.healer.wow_class

        mm, ss = divmod(ev.time_into_fight_ms // 1000, 60)
        color = colors.get(display_class, "FFFFFF").lower()
        time_token = _time_token(int(mm), int(ss), spell_label, style)
        name = n_over.get(ev.healer.name, ev.healer.name)
        lines.append(
            f"{time_token} |cff{color}{name}|r {{spell:{spell_id}}}"
        )
    return "\n".join(lines)


def _time_token(mm: int, ss: int, spell_label: str, style: NoteStyle) -> str:
    if style is NoteStyle.MRT:
        # MRT labels can't contain whitespace cleanly — collapse to camel-ish
        # underscore form so the anchor name is parseable.
        anchor = spell_label.replace(" / ", "_").replace(" ", "_")
        return f"{{time:{mm:02d}:{ss:02d},{anchor}}}"
    return f"{{time:{mm:02d}:{ss:02d}}}"

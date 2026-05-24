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
) -> str:
    """Emit a paste-ready raid note for the given fight's cooldown timeline.

    `name_overrides` maps WCL player names to display names (e.g. the user's
    own guild members). Names not in the map render as-is.
    """
    colors = CLASS_COLORS_NSRT if style is NoteStyle.NSRT else CLASS_COLORS_MRT
    overrides = name_overrides or {}
    lines: list[str] = []
    for ev in fc.events:
        mm, ss = divmod(ev.time_into_fight_ms // 1000, 60)
        color = colors.get(ev.healer.wow_class, "FFFFFF").lower()
        time_token = _time_token(int(mm), int(ss), ev.spell.label, style)
        name = overrides.get(ev.healer.name, ev.healer.name)
        lines.append(
            f"{time_token} |cff{color}{name}|r {{spell:{ev.cast_spell_id}}}"
        )
    return "\n".join(lines)


def _time_token(mm: int, ss: int, spell_label: str, style: NoteStyle) -> str:
    if style is NoteStyle.MRT:
        # MRT labels can't contain whitespace cleanly — collapse to camel-ish
        # underscore form so the anchor name is parseable.
        anchor = spell_label.replace(" / ", "_").replace(" ", "_")
        return f"{{time:{mm:02d}:{ss:02d},{anchor}}}"
    return f"{{time:{mm:02d}:{ss:02d}}}"

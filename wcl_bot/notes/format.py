"""WoW raid note formatters: NSRT (Northern Sky Raid Tools) and MRT
(Method Raid Tools) styles.

The two formats are *completely different syntaxes*, not just stylistic
variants. They share no per-line tokens.

  - **MRT** (Method Raid Tools): WeakAuras-ish `{time:MM:SS[,Label]} ...`
    with inline color codes and `{spell:ID}` references. One line per cast.
    Example:
        {time:01:23,Tranquility} |cffff7c0aMaver|r {spell:740}

  - **NSRT** (Northern Sky Raid Tools): semicolon-separated key=value
    pairs the addon parses with `line:match("tag:([^;]+)")` etc. Times
    are seconds (decimal allowed), spell is `spellid:<id>`, players are
    in `tag:<name1,name2>`. No color codes — NSRT colors per-class at
    runtime from the player's class. One line per cast.
    Example:
        tag:Maver; time:83; spellid:740
"""
from __future__ import annotations

from enum import Enum

from wcl_bot.cooldowns.extract import FightCooldowns
from wcl_bot.cooldowns.spells import substitute_for


# Canonical Blizzard class colors (ARGB hex, no leading #). MRT-only — NSRT
# doesn't embed colors in note text.
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
    purpose) in the note. Events with no equivalent in the target class
    are dropped.
    """
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
        name = n_over.get(ev.healer.name, ev.healer.name)
        time_s = ev.time_into_fight_ms // 1000
        if style is NoteStyle.NSRT:
            lines.append(_nsrt_line(name, int(time_s), spell_id))
        else:
            lines.append(_mrt_line(name, int(time_s), spell_id, spell_label, display_class))
    return "\n".join(lines)


def _nsrt_line(name: str, time_s: int, spell_id: int) -> str:
    """One reminder line in NSRT's `key:value;` format. Order matches what
    the addon's `BuildFirstLine` exporter produces so it parses cleanly."""
    return f"tag:{name}; time:{time_s}; spellid:{spell_id}"


def _mrt_line(name: str, time_s: int, spell_id: int, spell_label: str, wow_class: str) -> str:
    mm, ss = divmod(time_s, 60)
    color = CLASS_COLORS_MRT.get(wow_class, "FFFFFF").lower()
    # MRT label anchor can't contain whitespace cleanly — collapse to
    # underscore form so other notes can `{time:...}` to it.
    anchor = spell_label.replace(" / ", "_").replace(" ", "_")
    return (
        f"{{time:{mm:02d}:{ss:02d},{anchor}}} "
        f"|cff{color}{name}|r {{spell:{spell_id}}}"
    )

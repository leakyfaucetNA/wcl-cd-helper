"""WCL fightRankings server-side comp filter generator.

WCL's `fightRankings.filter` argument accepts a pipe-delimited expression
string that mirrors the website's Advanced Filter URL parameter:

    duration.MIN.MAX | tank.N | heal.N | melee.N | ranged.N | <comp tuples>

Where <comp tuples> is a comma-separated list of `<class_id>.<spec_id>.<count>`
entries. count = -1 means "at least one"; a positive integer means exactly
that many of that spec are required.

IDs decoded from the website filter UI on 2026-05-23. The mapping is
alphabetical for the original 11 classes; DH (12) and Evoker (13) were
appended later so existing IDs stayed stable. Spec IDs are alphabetical
within each class except Rogue (Outlaw=4, spec 2 retired) and Evoker/DH
which are in release order.
"""
from __future__ import annotations

from collections import Counter

from wcl_bot.matcher.comp import TargetComp

CLASS_IDS: dict[str, int] = {
    "Death Knight": 1,
    "Druid": 2,
    "Hunter": 3,
    "Mage": 4,
    "Monk": 5,
    "Paladin": 6,
    "Priest": 7,
    "Rogue": 8,
    "Shaman": 9,
    "Warlock": 10,
    "Warrior": 11,
    "Demon Hunter": 12,
    "Evoker": 13,
}

SPEC_IDS: dict[tuple[str, str], int] = {
    ("Death Knight", "Blood"): 1,
    ("Death Knight", "Frost"): 2,
    ("Death Knight", "Unholy"): 3,
    ("Druid", "Balance"): 1,
    ("Druid", "Feral"): 2,
    ("Druid", "Guardian"): 3,
    ("Druid", "Restoration"): 4,
    ("Hunter", "Beast Mastery"): 1,
    ("Hunter", "Marksmanship"): 2,
    ("Hunter", "Survival"): 3,
    ("Mage", "Arcane"): 1,
    ("Mage", "Fire"): 2,
    ("Mage", "Frost"): 3,
    ("Monk", "Brewmaster"): 1,
    ("Monk", "Mistweaver"): 2,
    ("Monk", "Windwalker"): 3,
    ("Paladin", "Holy"): 1,
    ("Paladin", "Protection"): 2,
    ("Paladin", "Retribution"): 3,
    ("Priest", "Discipline"): 1,
    ("Priest", "Holy"): 2,
    ("Priest", "Shadow"): 3,
    ("Rogue", "Assassination"): 1,
    ("Rogue", "Subtlety"): 3,
    ("Rogue", "Outlaw"): 4,
    ("Shaman", "Elemental"): 1,
    ("Shaman", "Enhancement"): 2,
    ("Shaman", "Restoration"): 3,
    ("Warlock", "Affliction"): 1,
    ("Warlock", "Demonology"): 2,
    ("Warlock", "Destruction"): 3,
    ("Warrior", "Arms"): 1,
    ("Warrior", "Fury"): 2,
    ("Warrior", "Protection"): 3,
    ("Demon Hunter", "Havoc"): 1,
    ("Demon Hunter", "Vengeance"): 2,
    ("Demon Hunter", "Devourer"): 3,
    ("Evoker", "Devastation"): 1,
    ("Evoker", "Preservation"): 2,
    ("Evoker", "Augmentation"): 3,
}


class CompFilterError(ValueError):
    pass


def build_comp_filter(
    target_comp: TargetComp,
    *,
    healer_count: int | None = None,
    omit_heal_constraint: bool = False,
    tanks: int | None = None,
    melee: int | None = None,
    ranged: int | None = None,
    duration_min_s: int | None = None,
    duration_max_s: int | None = None,
) -> str:
    """Build a fightRankings filter string for the given comp.

    `healer_count` controls the `heal.N` constraint. Default is the comp's
    own slot count (exact match). For fan-out queries that look for fights
    with the comp PLUS extras, pass `healer_count=N+k` — the filter then
    asks the API for fights of that healer count where each of the comp's
    specs is present (at-least-one). Per-spec minimum counts (e.g. "at
    least 2 R.Druid") are enforced client-side by subset matching.

    Raises CompFilterError if any healer slot has spec=None — server-side
    filtering can't express "any healer spec of class X".
    """
    flex = [s for s in target_comp.slots if s.spec is None]
    if flex:
        raise CompFilterError(
            f"Cannot build server-side filter: {len(flex)} slot(s) are "
            f"class-only (any spec). The filter requires exact (class, spec). "
            f"Use the client-side comp filter instead, or specify all specs."
        )

    hc = healer_count if healer_count is not None else len(target_comp.slots)

    parts: list[str] = []
    if duration_min_s is not None and duration_max_s is not None:
        parts.append(f"duration.{duration_min_s}.{duration_max_s}")
    if tanks is not None:
        parts.append(f"tank.{tanks}")
    if not omit_heal_constraint:
        parts.append(f"heal.{hc}")
    if melee is not None:
        parts.append(f"melee.{melee}")
    if ranged is not None:
        parts.append(f"ranged.{ranged}")

    # All specs use count=-1 ("at least one"). Per-spec minimums and exact
    # count enforcement are handled client-side via TargetComp matching.
    spec_counts: Counter[tuple[str, str]] = Counter()
    for slot in target_comp.slots:
        spec_counts[(slot.wow_class, slot.spec)] += 1

    sorted_tuples = sorted(
        spec_counts.items(),
        key=lambda kv: (CLASS_IDS[kv[0][0]], SPEC_IDS[kv[0]]),
    )

    encoded: list[str] = []
    for (wow_class, spec), _count in sorted_tuples:
        cls_id = CLASS_IDS.get(wow_class)
        spec_id = SPEC_IDS.get((wow_class, spec))
        if cls_id is None or spec_id is None:
            raise CompFilterError(
                f"No WCL ID mapping for {wow_class}/{spec}"
            )
        encoded.append(f"{cls_id}.{spec_id}.-1")

    parts.append(",".join(encoded))
    return "|".join(parts)


def build_comp_filters(
    target_comp: TargetComp,
    *,
    include_extra_healers: bool = False,
) -> list[tuple[int | None, str]]:
    """Return the (healer_count_hint, filter_string) tuples we'll query.

    Always returns the EXACT-N query (heal.N for the user's slot count).
    When `include_extra_healers=True`, also adds a BROAD query with no
    heal.N constraint — that catches any healer count where the user's
    specs are present (including ones beyond the user's slot count).

    The hint is `None` for the broad pool; callers use that to switch
    from exact matching to subset matching. Results from both pools are
    deduped downstream by (report_code, fight_id)."""
    target_n = len(target_comp.slots)
    filters: list[tuple[int | None, str]] = [
        (target_n, build_comp_filter(target_comp, healer_count=target_n)),
    ]
    if include_extra_healers:
        filters.append(
            (None, build_comp_filter(target_comp, omit_heal_constraint=True))
        )
    return filters

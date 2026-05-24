"""Per-log summary scoring for the manual log-pick workflow.

Each log's CDs are compared to the cohort median (computed per
(class, spec, spell, press_index)) to produce two intuitive numbers:

- avg_shift_ms: mean absolute timing difference from the median.
  Low = this log's presses land near typical times.
- n_outliers: count of presses whose shift exceeds the outlier threshold.
  High = several presses are well off the typical timing.

A log present-but-empty for some (cls, spec, spell, press_index) — i.e.
its healer skipped a press the rest of the cohort hit — is not currently
penalized; only existing presses are scored.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from wcl_bot.cooldowns.extract import FightCooldowns

# (wow_class, spec, primary spell_id, press_index-within-fight)
_PressKey = tuple[str, str, int, int]


@dataclass(frozen=True)
class LogScore:
    index: int                # 1-based display index
    fight: FightCooldowns
    n_presses: int            # total tracked CD presses in this log
    n_unique_spells: int      # distinct (class, spec, spell) combos pressed
    avg_shift_ms: int         # mean abs deviation from cohort median, ms
    n_outliers: int           # presses where shift > outlier_threshold_ms


def score_logs(
    fight_cds: list[FightCooldowns],
    *,
    outlier_threshold_ms: int = 10000,
) -> list[LogScore]:
    if not fight_cds:
        return []

    # 1. Index each fight's presses by (cls, spec, spell, press_index).
    per_log: list[dict[_PressKey, int]] = []
    for fc in fight_cds:
        grouped: dict[tuple[str, str, int], list[int]] = defaultdict(list)
        for ev in fc.events:
            grouped[(ev.healer.wow_class, ev.healer.spec, ev.spell.spell_id)].append(
                ev.time_into_fight_ms
            )
        indexed: dict[_PressKey, int] = {}
        for key, times in grouped.items():
            times.sort()
            for i, t in enumerate(times):
                indexed[(*key, i)] = t
        per_log.append(indexed)

    # 2. Cohort medians.
    cohort: dict[_PressKey, list[int]] = defaultdict(list)
    for indexed in per_log:
        for key, t in indexed.items():
            cohort[key].append(t)
    medians: dict[_PressKey, int] = {k: _median(v) for k, v in cohort.items()}

    # 3. Score each log.
    scores: list[LogScore] = []
    for i, (fc, indexed) in enumerate(zip(fight_cds, per_log)):
        shifts: list[int] = []
        outliers = 0
        unique_spells: set[tuple[str, str, int]] = set()
        for key, t in indexed.items():
            unique_spells.add(key[:3])
            shift = abs(t - medians[key])
            shifts.append(shift)
            if shift > outlier_threshold_ms:
                outliers += 1
        avg_shift = sum(shifts) // len(shifts) if shifts else 0
        scores.append(
            LogScore(
                index=i + 1,
                fight=fc,
                n_presses=len(indexed),
                n_unique_spells=len(unique_spells),
                avg_shift_ms=avg_shift,
                n_outliers=outliers,
            )
        )
    return scores


def _median(values: list[int]) -> int:
    n = len(values)
    s = sorted(values)
    if n % 2:
        return s[n // 2]
    return (s[n // 2 - 1] + s[n // 2]) // 2

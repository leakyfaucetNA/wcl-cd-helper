"""Cluster per-fight cooldown events into a single rotation timeline.

For each (class, spec, spell), bucket all timestamps across N fights into
clusters of consecutive presses within `cluster_tolerance_ms` of each other.
A cluster represents one "rotation slot" — e.g. Tranquility usage #1 vs #2
in the same fight cluster separately because they're 3+ min apart.

Each cluster is dropped if fewer than `min_confidence * N` unique fights
contributed to it, which strips out outlier or one-off CD presses.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from math import ceil, sqrt

from wcl_bot.cooldowns.extract import FightCooldowns
from wcl_bot.cooldowns.spells import CooldownSpell


@dataclass(frozen=True)
class AggregatedCast:
    healer_class: str
    healer_spec: str
    spell: CooldownSpell
    mean_time_ms: int
    fights_seen: int     # unique fights contributing to this cluster
    fights_total: int    # total fights aggregated
    std_dev_ms: int      # tightness of the cluster

    @property
    def confidence(self) -> float:
        return self.fights_seen / self.fights_total if self.fights_total else 0.0


def aggregate_cooldowns(
    fight_cds: list[FightCooldowns],
    *,
    cluster_tolerance_ms: int = 5000,
    min_confidence: float = 0.5,
    include_below_threshold: bool = False,
) -> list[AggregatedCast]:
    """Collapse per-fight events into a clustered rotation timeline.

    Args:
        fight_cds: per-fight cooldown timelines from extract_cooldowns().
        cluster_tolerance_ms: max gap between consecutive events in a cluster.
            Default 5000ms (5s) — tight enough that distinct rotation slots
            stay separated, loose enough to absorb per-kill jitter.
        min_confidence: drop clusters where fewer than ceil(N * conf) unique
            fights contributed. Default 0.5.
        include_below_threshold: if True, return all clusters regardless of
            confidence (useful for diagnostics).
    """
    n = len(fight_cds)
    if n == 0:
        return []

    # (class, spec, primary spell_id) -> [(time_ms, fight_idx), ...]
    grouped: dict[tuple[str, str, int], list[tuple[int, int]]] = defaultdict(list)
    spell_by_id: dict[int, CooldownSpell] = {}
    for fight_idx, fc in enumerate(fight_cds):
        for ev in fc.events:
            key = (ev.healer.wow_class, ev.healer.spec, ev.spell.spell_id)
            grouped[key].append((ev.time_into_fight_ms, fight_idx))
            spell_by_id[ev.spell.spell_id] = ev.spell

    min_fights = ceil(n * min_confidence) if min_confidence > 0 else 1
    out: list[AggregatedCast] = []
    for (cls, spec, spell_id), points in grouped.items():
        points.sort()
        for cluster in _cluster_by_gap(points, cluster_tolerance_ms):
            unique_fights = {fid for _, fid in cluster}
            if not include_below_threshold and len(unique_fights) < min_fights:
                continue
            times = [t for t, _ in cluster]
            mean = sum(times) // len(times)
            variance = sum((t - mean) ** 2 for t in times) / len(times)
            out.append(
                AggregatedCast(
                    healer_class=cls,
                    healer_spec=spec,
                    spell=spell_by_id[spell_id],
                    mean_time_ms=mean,
                    fights_seen=len(unique_fights),
                    fights_total=n,
                    std_dev_ms=int(sqrt(variance)),
                )
            )

    out.sort(key=lambda a: (a.mean_time_ms, a.healer_class, a.healer_spec))
    return out


def _cluster_by_gap(
    points: list[tuple[int, int]],
    tolerance_ms: int,
) -> list[list[tuple[int, int]]]:
    """Greedy: walk sorted points, start a new cluster when the gap from the
    previous timestamp exceeds tolerance. Cluster width can exceed tolerance
    when many consecutive presses chain together, but distinct rotation slots
    (separated by minutes) always end up in separate clusters."""
    if not points:
        return []
    clusters: list[list[tuple[int, int]]] = [[points[0]]]
    for point in points[1:]:
        if point[0] - clusters[-1][-1][0] <= tolerance_ms:
            clusters[-1].append(point)
        else:
            clusters.append([point])
    return clusters

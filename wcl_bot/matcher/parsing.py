"""Tolerant parsers for WCL JSON-blob responses.

Both `fightRankings` and `playerDetails` return scalar JSON whose exact key
names are not in public docs. These parsers try the most likely shape first
and fall back; on total mismatch they raise WCLSchemaError with the actual
top-level keys for diagnosis.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from wcl_bot.matcher.comp import HEALER_SPECS
from wcl_bot.wcl.client import WCLSchemaError


@dataclass(frozen=True)
class HealerEntry:
    source_id: int      # WCL-internal player ID within the report; matches event.sourceID
    name: str
    wow_class: str      # canonical title case, e.g. "Priest"
    spec: str           # canonical title case, e.g. "Holy"


@dataclass(frozen=True)
class RankingFight:
    report_code: str
    fight_id: int
    duration_ms: int
    guild_name: str | None
    server_region: str | None
    server_slug: str | None
    rank: int | None
    percentile: float | None
    healer_count: int | None  # from `healers` field in ranking entry; pre-filter hint


def parse_rankings_page(blob: Any) -> tuple[list[RankingFight], bool]:
    """Parse one page of fightRankings JSON. Returns (fights, has_more_pages)."""
    if not isinstance(blob, dict):
        raise WCLSchemaError(
            f"fightRankings: expected dict, got {type(blob).__name__}"
        )

    # Common shapes observed: top-level has "rankings": [...] and "hasMorePages": bool.
    # Some responses nest under "data".
    container = blob
    if "rankings" not in container and isinstance(container.get("data"), dict):
        container = container["data"]

    raw_rankings = container.get("rankings")
    if not isinstance(raw_rankings, list):
        raise WCLSchemaError(
            f"fightRankings: no 'rankings' list found. Top-level keys: {list(blob)}"
        )

    has_more = bool(
        container.get("hasMorePages")
        or container.get("hasMore")
        or container.get("nextPage")
    )

    fights = [_parse_ranking_entry(r) for r in raw_rankings if isinstance(r, dict)]
    return fights, has_more


def _parse_ranking_entry(entry: dict[str, Any]) -> RankingFight:
    # Report code: usually top-level "reportID" or nested "report.code".
    report_code = entry.get("reportID") or entry.get("reportCode")
    if report_code is None and isinstance(entry.get("report"), dict):
        report_code = entry["report"].get("code")
    if not report_code:
        raise WCLSchemaError(
            f"ranking entry missing report code; keys: {list(entry)}"
        )

    fight_id = entry.get("fightID") or entry.get("fightId")
    if fight_id is None and isinstance(entry.get("report"), dict):
        fight_id = entry["report"].get("fightID")
    if fight_id is None:
        raise WCLSchemaError(
            f"ranking entry missing fight id; keys: {list(entry)}"
        )

    duration = entry.get("duration") or entry.get("durationMS") or 0

    guild = entry.get("guild")
    guild_name: str | None
    if isinstance(guild, dict):
        guild_name = guild.get("name")
    elif isinstance(guild, str):
        guild_name = guild
    else:
        guild_name = entry.get("guildName")

    server = entry.get("server")
    if isinstance(server, dict):
        server_region = server.get("region")
        server_slug = server.get("name") or server.get("slug")
    else:
        server_region = entry.get("serverRegion")
        server_slug = entry.get("serverName") or entry.get("serverSlug")

    healer_count = entry.get("healers")
    if not isinstance(healer_count, int):
        healer_count = None

    return RankingFight(
        report_code=str(report_code),
        fight_id=int(fight_id),
        duration_ms=int(duration),
        guild_name=guild_name,
        server_region=server_region,
        server_slug=server_slug,
        rank=entry.get("rank"),
        percentile=entry.get("percentile"),
        healer_count=healer_count,
    )


# Lowercase healer class names for fallback icon-parsing.
_HEALER_CLASSES_LC = {c.lower() for c in HEALER_SPECS}
_HEALER_SPECS_LC = {
    (cls.lower(), spec.lower())
    for cls, specs in HEALER_SPECS.items()
    for spec in specs
}


def parse_healers(blob: Any) -> list[HealerEntry]:
    """Extract HealerEntry objects from a playerDetails response.

    Returns canonical title-cased class/spec strings; preserves the WCL
    source_id and player name for downstream event queries.
    """
    if not isinstance(blob, dict):
        raise WCLSchemaError(
            f"playerDetails: expected dict, got {type(blob).__name__}"
        )

    # Primary shape: data.reportData.report.playerDetails.data.playerDetails.healers[]
    # Find the playerDetails object regardless of how deep it is.
    pd = _find_player_details(blob)
    if pd is None:
        raise WCLSchemaError(
            f"playerDetails: could not locate playerDetails object; top-level keys: {list(blob)}"
        )

    if isinstance(pd, dict) and isinstance(pd.get("healers"), list):
        return [_to_healer_entry(e) for e in pd["healers"]]

    # Fallback: a flat "entries" list — filter to healer specs by icon/type.
    if isinstance(pd, dict) and isinstance(pd.get("entries"), list):
        out = []
        for e in pd["entries"]:
            entry = _to_healer_entry(e)
            if (entry.wow_class.lower(), entry.spec.lower()) in _HEALER_SPECS_LC:
                out.append(entry)
        return out

    raise WCLSchemaError(
        f"playerDetails: unrecognized inner shape; keys: "
        f"{list(pd) if isinstance(pd, dict) else type(pd).__name__}"
    )


def _to_healer_entry(entry: dict[str, Any]) -> HealerEntry:
    cls, spec = _extract_class_spec(entry)
    return HealerEntry(
        source_id=int(entry["id"]),
        name=str(entry.get("name", "")),
        wow_class=cls,
        spec=spec,
    )


def _find_player_details(blob: Any) -> Any:
    """Walk the blob looking for a dict that has 'healers' or 'entries'."""
    if isinstance(blob, dict):
        if "healers" in blob or "entries" in blob:
            return blob
        for v in blob.values():
            found = _find_player_details(v)
            if found is not None:
                return found
    return None


def _extract_class_spec(entry: dict[str, Any]) -> tuple[str, str]:
    # `type` is the class name; `icon` is usually "Class-Spec".
    cls = entry.get("type") or entry.get("class") or ""
    spec = entry.get("spec") or ""

    if not spec:
        icon = entry.get("icon", "")
        if isinstance(icon, str) and "-" in icon:
            icon_cls, icon_spec = icon.split("-", 1)
            if not cls:
                cls = icon_cls
            spec = icon_spec

    # `specs` may be a list of {spec, count, role} — pick the most-used.
    if not spec and isinstance(entry.get("specs"), list) and entry["specs"]:
        best = max(
            entry["specs"],
            key=lambda s: (s.get("count", 0) if isinstance(s, dict) else 0),
        )
        if isinstance(best, dict):
            spec = best.get("spec", "")

    return (str(cls).title(), str(spec).title())

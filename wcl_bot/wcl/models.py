"""Dataclass wrappers over the raw WCL GraphQL responses."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Zone:
    id: int
    name: str


@dataclass
class Fight:
    id: int
    encounter_id: int
    name: str
    kill: bool
    difficulty: int | None
    size: int | None
    start_time: int
    end_time: int
    friendly_players: list[int] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Fight:
        return cls(
            id=data["id"],
            encounter_id=data["encounterID"],
            name=data["name"],
            kill=bool(data.get("kill")),
            difficulty=data.get("difficulty"),
            size=data.get("size"),
            start_time=data["startTime"],
            end_time=data["endTime"],
            friendly_players=data.get("friendlyPlayers") or [],
        )


@dataclass
class Report:
    code: str
    title: str
    start_time: int
    end_time: int
    zone: Zone | None
    fights: list[Fight]

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Report:
        zone_data = data.get("zone")
        return cls(
            code=data["code"],
            title=data["title"],
            start_time=data["startTime"],
            end_time=data["endTime"],
            zone=(
                Zone(id=zone_data["id"], name=zone_data["name"])
                if zone_data
                else None
            ),
            fights=[Fight.from_dict(f) for f in data.get("fights") or []],
        )

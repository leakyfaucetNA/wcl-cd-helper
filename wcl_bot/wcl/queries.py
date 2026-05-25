"""GraphQL query strings for the WCL v2 API."""

# Guild ID lookup. `Guild` doesn't have a direct `reports` field — to fetch
# a guild's recent reports we need its ID first, then call reportData.reports.
GET_GUILD_BY_NAME = """
query GetGuildByName(
  $name: String!
  $serverSlug: String!
  $serverRegion: String!
) {
  guildData {
    guild(name: $name, serverSlug: $serverSlug, serverRegion: $serverRegion) {
      id
      name
    }
  }
}
"""

# Reports owned by a specific guild (by ID). Used by the Settings tab to
# discover the guild's current healer roster.
GET_REPORTS_FOR_GUILD = """
query GetReportsForGuild($guildID: Int!, $limit: Int!) {
  reportData {
    reports(guildID: $guildID, limit: $limit) {
      data {
        code
        title
        startTime
      }
    }
  }
}
"""

# Aggregate playerDetails across every kill fight in a report. WCL requires
# either fightIDs or a time window; we use a 24-hour window (no report is
# longer than that) to grab everything without an extra fights query.
GET_REPORT_PLAYER_DETAILS_ALL_KILLS = """
query GetReportPlayerDetailsAllKills($code: String!) {
  reportData {
    report(code: $code) {
      playerDetails(killType: Kills, startTime: 0, endTime: 86400000)
    }
  }
}
"""

# All zones (raid tiers / Mythic+ seasons) with their encounters. Used to
# populate the boss-picker dropdown on the web UI. Cache aggressively —
# zones change only when Blizzard releases a new raid.
GET_ZONES = """
query GetZones {
  worldData {
    zones {
      id
      name
      frozen
      expansion { id name }
      encounters {
        id
        name
      }
    }
  }
}
"""

GET_REPORT_FIGHTS = """
query GetReportFights($code: String!) {
  reportData {
    report(code: $code) {
      code
      title
      startTime
      endTime
      zone { id name }
      fights(killType: Kills) {
        id
        encounterID
        name
        kill
        difficulty
        size
        startTime
        endTime
        friendlyPlayers
      }
    }
  }
}
"""

# playerDetails returns a generic JSON blob keyed by role
# ("tanks" / "healers" / "dps"). Each entry has id, name, type (class),
# specs[], server, etc. Useful for healer-comp matching.
GET_REPORT_PLAYER_DETAILS = """
query GetReportPlayerDetails($code: String!, $fightIDs: [Int]) {
  reportData {
    report(code: $code) {
      playerDetails(fightIDs: $fightIDs)
    }
  }
}
"""

# Per-player healing table for a fight — includes total healing, activeTime
# (ms a player was performing actions), overheal, etc. We pull activeTime
# to filter out logs where a healer died early (their CD timings would be
# misleading for note-stealing).
GET_HEALING_TABLE = """
query GetHealingTable($code: String!, $fightID: Int!) {
  reportData {
    report(code: $code) {
      table(dataType: Healing, fightIDs: [$fightID])
    }
  }
}
"""

# Per-player parse percentiles for a specific (report, fight). Returns a
# scalar JSON blob — actual shape is verified empirically and parsed
# tolerantly in routes.py.
GET_REPORT_FIGHT_RANKINGS = """
query GetReportFightRankings($code: String!, $fightID: Int!) {
  reportData {
    report(code: $code) {
      rankings(fightIDs: [$fightID])
    }
  }
}
"""

# Fight time window (startTime/endTime in report-relative ms). Needed
# to convert raw event timestamps into time-into-fight.
GET_FIGHT_TIMES = """
query GetFightTimes($code: String!, $fightID: Int!) {
  reportData {
    report(code: $code) {
      fights(fightIDs: [$fightID]) {
        id
        startTime
        endTime
      }
    }
  }
}
"""

# One page of cast events scoped by absolute (report-relative) time window.
# Event timestamps are report-relative ms. Pagination: when nextPageTimestamp
# is non-null, re-call with startTime=nextPageTimestamp.
GET_CASTS_PAGE = """
query GetCastsPage(
  $code: String!
  $startTime: Float!
  $endTime: Float!
) {
  reportData {
    report(code: $code) {
      events(
        dataType: Casts
        startTime: $startTime
        endTime: $endTime
        limit: 10000
      ) {
        data
        nextPageTimestamp
      }
    }
  }
}
"""

# fightRankings returns a scalar JSON blob — exact key names are not in
# public docs and may change. Parsers in matcher/parsing.py must tolerate
# variation and raise WCLSchemaError on unknown shapes.
GET_ENCOUNTER_FIGHT_RANKINGS = """
query GetEncounterFightRankings(
  $encounterID: Int!
  $difficulty: Int!
  $page: Int!
  $metric: FightRankingMetricType
  $serverRegion: String
  $filter: String
) {
  worldData {
    encounter(id: $encounterID) {
      fightRankings(
        difficulty: $difficulty
        page: $page
        metric: $metric
        serverRegion: $serverRegion
        filter: $filter
      )
    }
  }
}
"""

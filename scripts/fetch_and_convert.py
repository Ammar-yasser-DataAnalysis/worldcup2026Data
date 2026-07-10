#!/usr/bin/env python3
"""
World Cup 2026 - Mirror & Star-Schema Converter
=================================================
1) Downloads the raw JSON files from openfootball/worldcup.json (2026 folder)
   and saves them as-is under data/raw/2026/  -> this is the "mirror".
2) Converts those JSON files into flat CSVs shaped for a Power BI star schema
   (fact_Matches, bridge_Goals, bridge_Teams, dim_Teams, dim_Players,
   dim_Stadium) and saves them under data/csv/.

Run manually:
    python scripts/fetch_and_convert.py

Designed to be run on a schedule by the GitHub Action in
.github/workflows/sync-worldcup-data.yml (every 15 minutes).
"""

import json
import re
import sys
from pathlib import Path

import requests

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
SOURCE_REPO_RAW = "https://raw.githubusercontent.com/openfootball/worldcup.json/master/2026"

FILES = [
    "worldcup.json",              # matches
    "worldcup.groups.json",       # group -> teams
    "worldcup.teams.json",        # team master data
    "worldcup.stadiums.json",     # stadium master data
    "worldcup.squads.json",       # player squads
    "worldcup.quali_playoffs.json",
]

ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = ROOT / "data" / "raw" / "2026"
CSV_DIR = ROOT / "data" / "csv"

RAW_DIR.mkdir(parents=True, exist_ok=True)
CSV_DIR.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Step 1: Mirror - download raw JSON files untouched
# ---------------------------------------------------------------------------
def download_mirror() -> dict:
    """Downloads every source file and returns a dict of {filename: parsed_json}."""
    data = {}
    for fname in FILES:
        url = f"{SOURCE_REPO_RAW}/{fname}"
        resp = requests.get(url, timeout=30)
        resp.raise_for_status()
        (RAW_DIR / fname).write_bytes(resp.content)
        data[fname] = resp.json()
        print(f"  mirrored {fname} ({len(resp.content):,} bytes)")
    return data


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def minute_to_number(minute: str):
    """Convert a minute string like '45+2' or '90+7' into a sortable float.
    '45+2' -> 45.02 so stoppage-time goals still sort after the base minute.
    """
    if minute is None:
        return None
    m = re.match(r"^(\d+)(?:\+(\d+))?$", str(minute).strip())
    if not m:
        return None
    base = int(m.group(1))
    stoppage = int(m.group(2)) if m.group(2) else 0
    return round(base + stoppage / 100, 2)


def csv_write(path: Path, rows: list, fieldnames: list):
    import csv
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for r in rows:
            writer.writerow(r)
    print(f"  wrote {path.name} ({len(rows)} rows)")


# ---------------------------------------------------------------------------
# Step 2: Build dim_Stadium
# ---------------------------------------------------------------------------
def build_dim_stadium(stadiums_json: dict) -> dict:
    """Returns {city_name: StadiumID} lookup, and writes dim_Stadium.csv."""
    rows = []
    city_to_id = {}
    for i, s in enumerate(stadiums_json["stadiums"], start=1):
        city_to_id[s["city"]] = i
        rows.append({
            "StadiumID": i,
            "StadiumName": s.get("name"),
            "City": s.get("city"),
            "CountryCode": s.get("cc"),
            "Capacity": s.get("capacity"),
            "Timezone": s.get("timezone"),
            "Coordinates": s.get("coords"),
        })
    csv_write(CSV_DIR / "dim_Stadium.csv", rows,
              ["StadiumID", "StadiumName", "City", "CountryCode", "Capacity", "Timezone", "Coordinates"])
    return city_to_id


# ---------------------------------------------------------------------------
# Step 3: Build dim_Teams
# ---------------------------------------------------------------------------
def build_dim_teams(teams_json: list) -> dict:
    """Returns {team_name: TeamID} lookup, and writes dim_Teams.csv."""
    rows = []
    name_to_id = {}
    for i, t in enumerate(teams_json, start=1):
        name_to_id[t["name"]] = i
        rows.append({
            "TeamID": i,
            "TeamName": t.get("name"),
            "FifaCode": t.get("fifa_code"),
            "Group": t.get("group"),
            "Confederation": t.get("confed"),
            "Continent": t.get("continent"),
            "FlagIcon": t.get("flag_icon"),
        })
    csv_write(CSV_DIR / "dim_Teams.csv", rows,
              ["TeamID", "TeamName", "FifaCode", "Group", "Confederation", "Continent", "FlagIcon"])
    return name_to_id


# ---------------------------------------------------------------------------
# Step 4: Build dim_Players (from squads)
# ---------------------------------------------------------------------------
def build_dim_players(squads_json: list, team_name_to_id: dict) -> None:
    rows = []
    player_id = 1
    for squad in squads_json:
        team_name = squad.get("name")
        team_id = team_name_to_id.get(team_name)
        for p in squad.get("players", []):
            club = p.get("club") or {}
            rows.append({
                "PlayerID": player_id,
                "TeamID": team_id,
                "TeamName": team_name,
                "SquadNumber": p.get("number"),
                "Position": p.get("pos"),
                "PlayerName": p.get("name"),
                "ClubName": club.get("name"),
                "ClubCountry": club.get("country"),
                "DateOfBirth": p.get("date_of_birth"),
            })
            player_id += 1
    csv_write(CSV_DIR / "dim_Players.csv", rows,
              ["PlayerID", "TeamID", "TeamName", "SquadNumber", "Position",
               "PlayerName", "ClubName", "ClubCountry", "DateOfBirth"])


# ---------------------------------------------------------------------------
# Step 5: Build fact_Matches, bridge_Teams, bridge_Goals
# ---------------------------------------------------------------------------
def build_match_tables(matches_json: dict, city_to_stadium_id: dict) -> None:
    match_rows = []
    bridge_team_rows = []
    goal_rows = []
    goal_id = 1

    for m in matches_json["matches"]:
        match_id = m.get("num")
        # Group-stage matches have no "num" field in this dataset -> synthesize one
        if match_id is None:
            match_id = f"{m['round']}_{m['date']}_{m['team1']}_{m['team2']}".replace(" ", "")

        score = m.get("score") or {}
        ft = score.get("ft") or [None, None]
        ht = score.get("ht") or [None, None]
        is_played = ft[0] is not None

        match_rows.append({
            "MatchID": match_id,
            "Round": m.get("round"),
            "IsKnockout": not str(m.get("round", "")).startswith("Matchday"),
            "MatchDate": m.get("date"),
            "MatchTime": m.get("time"),
            "Group": m.get("group"),
            "Team1": m.get("team1"),
            "Team2": m.get("team2"),
            "City": m.get("ground"),
            "StadiumID": city_to_stadium_id.get(m.get("ground")),
            "HomeScoreFT": ft[0],
            "AwayScoreFT": ft[1],
            "HomeScoreHT": ht[0],
            "AwayScoreHT": ht[1],
            "IsPlayed": is_played,
        })

        bridge_team_rows.append({"MatchID": match_id, "TeamName": m.get("team1"), "HomeAway": "Team1"})
        bridge_team_rows.append({"MatchID": match_id, "TeamName": m.get("team2"), "HomeAway": "Team2"})

        for side, team_key in (("goals1", "team1"), ("goals2", "team2")):
            scoring_side_team = m.get(team_key)
            other_team = m.get("team2") if team_key == "team1" else m.get("team1")
            for g in m.get(side, []) or []:
                is_own_goal = bool(g.get("owngoal"))
                # An own goal is scored by a player of `scoring_side_team`
                # but the goal is CREDITED to the opponent on the scoreboard.
                credited_team = other_team if is_own_goal else scoring_side_team
                goal_rows.append({
                    "GoalID": goal_id,
                    "MatchID": match_id,
                    "PlayerName": g.get("name"),
                    "PlayerTeam": scoring_side_team,
                    "CreditedTeam": credited_team,
                    "Minute": g.get("minute"),
                    "MinuteNumeric": minute_to_number(g.get("minute")),
                    "IsOwnGoal": is_own_goal,
                    "IsPenalty": bool(g.get("penalty")),
                })
                goal_id += 1

    csv_write(CSV_DIR / "fact_Matches.csv", match_rows,
              ["MatchID", "Round", "IsKnockout", "MatchDate", "MatchTime", "Group",
               "Team1", "Team2", "City", "StadiumID", "HomeScoreFT", "AwayScoreFT",
               "HomeScoreHT", "AwayScoreHT", "IsPlayed"])

    csv_write(CSV_DIR / "bridge_Teams.csv", bridge_team_rows,
              ["MatchID", "TeamName", "HomeAway"])

    csv_write(CSV_DIR / "bridge_Goals.csv", goal_rows,
              ["GoalID", "MatchID", "PlayerName", "PlayerTeam", "CreditedTeam",
               "Minute", "MinuteNumeric", "IsOwnGoal", "IsPenalty"])


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    print("Step 1/2: mirroring raw JSON from openfootball/worldcup.json ...")
    data = download_mirror()

    print("Step 2/2: building star-schema CSVs ...")
    city_to_stadium_id = build_dim_stadium(data["worldcup.stadiums.json"])
    team_name_to_id = build_dim_teams(data["worldcup.teams.json"])
    build_dim_players(data["worldcup.squads.json"], team_name_to_id)
    build_match_tables(data["worldcup.json"], city_to_stadium_id)

    print("Done.")


if __name__ == "__main__":
    try:
        main()
    except requests.HTTPError as e:
        print(f"HTTP error while fetching source data: {e}", file=sys.stderr)
        sys.exit(1)

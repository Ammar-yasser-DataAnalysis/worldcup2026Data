# World Cup 2026 Data Mirror + Star Schema CSVs

Auto-mirrors [`openfootball/worldcup.json`](https://github.com/openfootball/worldcup.json)
(2026 folder) every 15 minutes via GitHub Actions, and converts the JSON into
flat CSV files shaped for a Power BI star schema — so Power BI (or anything
else) can connect straight to `data/csv/*.csv` on `raw.githubusercontent.com`
without touching JSON at all.

## How it works

1. **`.github/workflows/sync-worldcup-data.yml`** runs on a cron schedule
   (`*/15 * * * *`) — every 15 minutes.
2. It runs **`scripts/fetch_and_convert.py`**, which:
   - Downloads the 6 source JSON files from the upstream repo and saves them
     unchanged under `data/raw/2026/` (this is the "mirror").
   - Converts them into star-schema CSVs under `data/csv/`.
3. If anything changed, the workflow commits and pushes automatically.

## CSV output (`data/csv/`)

| File | Grain | Notes |
|---|---|---|
| `dim_Teams.csv` | one row per team | TeamID, FifaCode, Group, Confederation, Continent |
| `dim_Stadium.csv` | one row per stadium | StadiumID, City, Capacity, Timezone, Coordinates |
| `dim_Players.csv` | one row per squad player | PlayerID, TeamID/TeamName, Position, Club, DOB |
| `fact_Matches.csv` | one row per match | MatchID, Round, Teams, City→StadiumID, FT/HT scores, IsPlayed, IsKnockout |
| `bridge_Teams.csv` | one row per (match, team) | resolves the Team1/Team2 columns into a long table for easy relationships |
| `bridge_Goals.csv` | one row per goal | PlayerName, PlayerTeam, **CreditedTeam** (handles own goals correctly), Minute, IsOwnGoal, IsPenalty |

**Own-goal logic:** the goal is attributed to the *scoring team's opponent*
in `CreditedTeam` (that's who it counts for on the scoreboard), while
`PlayerTeam` keeps the actual team of the player who scored it — so you can
still analyze "own goals conceded by team X" separately from "goals scored by
team X".

**Matches without a `num` in the source** (regular group-stage matches) get a
synthetic `MatchID` built from round + date + teams, so every match has a
stable unique key even before FIFA assigns official match numbers.

## Setup (from scratch)

1. Create a new **public** GitHub repo (e.g. `worldcup2026-mirror`).
2. Push everything in this folder to it:
   ```bash
   git init
   git add .
   git commit -m "initial mirror setup"
   git branch -M main
   git remote add origin https://github.com/<your-username>/<repo-name>.git
   git push -u origin main
   ```
3. Go to **Settings → Actions → General → Workflow permissions** and set it
   to **"Read and write permissions"** (needed so the bot can push the
   auto-updated CSVs).
4. That's it — the workflow will run automatically every 15 minutes. You can
   also trigger it manually from the **Actions** tab (`workflow_dispatch`).

## Connecting from Power BI

Use **Get Data → Web** with the *raw* GitHub URL for each CSV, e.g.:

```
https://raw.githubusercontent.com/<your-username>/<repo-name>/main/data/csv/fact_Matches.csv
```

Do this once per CSV (6 queries), then build your relationships in the model
view exactly like you did with the live JSON version — same grain, same
columns, just CSV instead of JSON, and it refreshes on its own upstream every
15 minutes so a normal **Refresh** in Power BI will pick up new results.

## Notes

- `data/raw/2026/` keeps the untouched upstream JSON too, in case you ever
  need a field that isn't in the CSVs yet — you can always extend
  `fetch_and_convert.py` to add columns.
- GitHub's cron scheduler is best-effort — during periods of high platform
  load, a `*/15` job can occasionally run a few minutes late. This does not
  affect correctness, only timeliness.

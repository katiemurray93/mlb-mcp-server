"""
MLB MCP Server
Exposes MLB Stats API data as MCP tools for Slackbot.
Supports multi-workspace Slack OAuth for workshop/distribution use.
"""

import os
import re
from collections import defaultdict
from datetime import date as date_cls, datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse

MLB_BASE = "https://statsapi.mlb.com/api/v1"

# ---------------------------------------------------------------------------
# Config — set these as env vars (locally in .env, on Heroku via config vars)
# ---------------------------------------------------------------------------
HOST = os.environ.get("HOST", "0.0.0.0")
PORT = int(os.environ.get("PORT", "8000"))

# Slack app credentials — get these from api.slack.com/apps after creating the app
SLACK_CLIENT_ID     = os.environ.get("SLACK_CLIENT_ID", "")
SLACK_CLIENT_SECRET = os.environ.get("SLACK_CLIENT_SECRET", "")

# The public base URL of this server (e.g. https://your-app.herokuapp.com)
# Used to build the OAuth redirect URI
APP_BASE_URL = os.environ.get("APP_BASE_URL", f"http://localhost:{PORT}")

mcp = FastMCP(
    "mlb-stats",
    host=HOST,
    port=PORT,
    json_response=True,
    stateless_http=True,
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=False,
    ),
)

# ---------------------------------------------------------------------------
# Slack OAuth endpoints
# Participants hit /slack/install → Slack OAuth → /slack/callback → done
# ---------------------------------------------------------------------------

@mcp.custom_route("/slack/install", methods=["GET"])
async def slack_install(request: Request) -> RedirectResponse:
    """Redirect to Slack's OAuth authorization page."""
    if not SLACK_CLIENT_ID:
        return HTMLResponse("SLACK_CLIENT_ID not configured.", status_code=500)
    redirect_uri = f"{APP_BASE_URL}/slack/callback"
    url = (
        f"https://slack.com/oauth/v2/authorize"
        f"?client_id={SLACK_CLIENT_ID}"
        f"&scope=mcp:connect"
        f"&redirect_uri={redirect_uri}"
    )
    return RedirectResponse(url)


@mcp.custom_route("/slack/callback", methods=["GET"])
async def slack_callback(request: Request) -> HTMLResponse:
    """Handle OAuth callback from Slack after user approves the install."""
    code = request.query_params.get("code")
    error = request.query_params.get("error")

    if error:
        return HTMLResponse(
            f"<h2>Installation cancelled</h2><p>{error}</p>",
            status_code=400,
        )
    if not code:
        return HTMLResponse("<h2>Missing code parameter.</h2>", status_code=400)

    redirect_uri = f"{APP_BASE_URL}/slack/callback"
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "https://slack.com/api/oauth.v2.access",
            data={
                "client_id":     SLACK_CLIENT_ID,
                "client_secret": SLACK_CLIENT_SECRET,
                "code":          code,
                "redirect_uri":  redirect_uri,
            },
        )
    data = resp.json()

    if not data.get("ok"):
        return HTMLResponse(
            f"<h2>OAuth error</h2><p>{data.get('error', 'unknown')}</p>",
            status_code=400,
        )

    team = data.get("team", {}).get("name", "your workspace")
    return HTMLResponse(f"""
        <html><body style="font-family:sans-serif;max-width:500px;margin:60px auto;text-align:center">
        <h2>✅ MLB Stats installed!</h2>
        <p>The app is now available in <strong>{team}</strong>.</p>
        <p>Open Slack, find the <strong>Slackbot</strong> DM, click the Apps button,
        add <strong>MLB Stats</strong>, and ask something like:<br><br>
        <em>"What MLB games are on today?"</em></p>
        </body></html>
    """)


def _get(path: str, **params) -> dict:
    url = f"{MLB_BASE}{path}"
    try:
        with httpx.Client(timeout=10) as client:
            r = client.get(url, params=params)
            r.raise_for_status()
            return r.json()
    except httpx.HTTPStatusError as exc:
        raise RuntimeError(
            f"MLB API returned HTTP {exc.response.status_code} for {path}"
        ) from exc
    except httpx.TimeoutException as exc:
        raise RuntimeError(f"MLB API request timed out for {path}") from exc
    except httpx.RequestError as exc:
        raise RuntimeError(f"Could not reach the MLB API for {path}: {exc}") from exc


@mcp.tool(
    description="Get MLB schedule and scores for today, or for a YYYY-MM-DD date.",
    annotations=ToolAnnotations(title="Get today's MLB games", readOnlyHint=True, openWorldHint=True),
    structured_output=False,
)
def get_todays_games(date: str = "") -> str:
    # MLB schedules by US Eastern time; the server runs on UTC, so default to
    # Eastern so "today" doesn't roll to the wrong day late in the US evening.
    target_date = date.strip() or datetime.now(ZoneInfo("America/New_York")).date().isoformat()

    try:
        data = _get(
            "/schedule",
            sportId=1,
            date=target_date,
            hydrate="linescore",
        )

        dates = data.get("dates", [])
        if not dates:
            return f"No games found for {target_date}."

        games = dates[0].get("games", [])
        if not games:
            return f"No games scheduled for {target_date}."

        lines = [f"MLB games for {target_date} ({len(games)} game{'s' if len(games) != 1 else ''}):\n"]

        for g in games:
            away = g["teams"]["away"]
            home = g["teams"]["home"]
            away_name = away["team"].get("teamName") or away["team"].get("name", "Unknown")
            home_name = home["team"].get("teamName") or home["team"].get("name", "Unknown")

            status_code = g["status"]["abstractGameState"]  # Preview / Live / Final
            detailed = g["status"]["detailedState"]

            away_score = away.get("score")
            home_score = home.get("score")

            ls = g.get("linescore", {})
            inning = ls.get("currentInning")
            inning_half = ls.get("inningHalf", "")  # "Top" / "Bottom"

            if status_code == "Preview":
                game_time = g.get("gameDate", "")[:16].replace("T", " ") + " UTC"
                line = f"  • {away_name} @ {home_name}  —  {detailed} ({game_time})"
            elif status_code == "Live":
                score = f"{away_name} {away_score}, {home_name} {home_score}"
                inn = f"{inning_half} {inning}" if inning else ""
                line = f"  • {score}  [{inn}]  🔴 LIVE"
            else:
                if away_score is not None and home_score is not None:
                    winner = away_name if away_score > home_score else home_name
                    score = f"{away_name} {away_score}, {home_name} {home_score}"
                    line = f"  • {score}  — {winner} win  ✅"
                else:
                    line = f"  • {away_name} @ {home_name}  — {detailed}"

            lines.append(line)

        return "\n".join(lines)
    except Exception as exc:
        return f"⚠️ Could not load game data: {exc}"


def _find_player(name: str) -> dict | None:
    data = _get("/people/search", names=name.strip(), hydrate="currentTeam")
    people = data.get("people") or []
    if not people:
        return None
    lowered = name.strip().lower()
    exact = [p for p in people if (p.get("fullName") or "").lower() == lowered]
    return (exact or people)[0]


def _season_stats(player_id: int, group: str, season: int) -> dict | None:
    data = _get(
        f"/people/{player_id}/stats",
        stats="season",
        group=group,
        season=season,
    )
    splits = ((data.get("stats") or [{}])[0].get("splits") or [])
    if not splits:
        return None
    return splits[0].get("stat") or {}


def _format_hitting(h: dict) -> str:
    return (
        f"{h.get('avg', '—')} AVG, "
        f"{h.get('homeRuns', 0)} HR, "
        f"{h.get('rbi', 0)} RBI, "
        f"{h.get('ops', '—')} OPS, "
        f"{h.get('stolenBases', 0)} SB, "
        f"{h.get('baseOnBalls', 0)} BB, "
        f"{h.get('strikeOuts', 0)} K "
        f"({h.get('gamesPlayed', 0)} G, {h.get('atBats', 0)} AB, "
        f"{h.get('hits', 0)} H, {h.get('doubles', 0)} 2B, {h.get('triples', 0)} 3B)"
    )


def _format_pitching(p: dict) -> str:
    return (
        f"{p.get('era', '—')} ERA, "
        f"{p.get('wins', 0)}-{p.get('losses', 0)} W-L, "
        f"{p.get('strikeOuts', 0)} K, "
        f"{p.get('baseOnBalls', 0)} BB, "
        f"{p.get('whip', '—')} WHIP, "
        f"{p.get('inningsPitched', '—')} IP, "
        f"{p.get('saves', 0)} SV "
        f"({p.get('gamesPlayed', 0)} G, {p.get('gamesStarted', 0)} GS)"
    )


@mcp.tool(
    description="Get a player's MLB stats by name. Defaults to current season but accepts a year like 2024. Works for hitters and pitchers. Example: 'Aaron Judge' or 'Gerrit Cole 2024'.",
    annotations=ToolAnnotations(title="Get player stats", readOnlyHint=True, openWorldHint=True),
    structured_output=False,
)
def get_player_stats(player_name: str, season: str = "") -> str:
    name = player_name.strip()
    if not name:
        return "Pass a player name, like Aaron Judge."

    player = _find_player(name)
    if not player:
        return f"Couldn't find an MLB player named *{name}*. Try the full name."

    yr = int(season.strip()) if season.strip().isdigit() else date_cls.today().year
    display = player.get("fullName") or name
    pos = (player.get("primaryPosition") or {}).get("abbreviation") or "?"
    team = (player.get("currentTeam") or {}).get("name") or "Team unknown"

    hitting = _season_stats(player["id"], "hitting", yr)
    pitching = _season_stats(player["id"], "pitching", yr)

    lines = [f"*{display}* — {team} ({pos}) — {yr} season\n"]

    if hitting:
        lines.append("Hitting: " + _format_hitting(hitting))
    if pitching:
        lines.append("Pitching: " + _format_pitching(pitching))
    if not hitting and not pitching:
        lines.append(f"No {yr} season stats found.")

    return "\n".join(lines)


@mcp.tool(
    description="Get a player's batting or pitching splits: vs LHP/RHP, home/away, by month, and with RISP. Key for platoon and lineup decisions. Defaults to current season; pass a year like 2024 for historical.",
    annotations=ToolAnnotations(title="Get player splits", readOnlyHint=True, openWorldHint=True),
    structured_output=False,
)
def get_player_splits(player_name: str, season: str = "") -> str:
    name = player_name.strip()
    if not name:
        return "Pass a player name, like Aaron Judge."

    player = _find_player(name)
    if not player:
        return f"Couldn't find an MLB player named *{name}*. Try the full name."

    yr = int(season.strip()) if season.strip().isdigit() else date_cls.today().year
    display = player.get("fullName") or name
    pos = (player.get("primaryPosition") or {}).get("abbreviation") or "?"
    is_pitcher = str(pos).upper() in ("P", "SP", "RP")
    group = "pitching" if is_pitcher else "hitting"

    data = _get(
        f"/people/{player['id']}/stats",
        stats="homeAndAway,byMonth,byDayOfWeek",
        group=group,
        season=yr,
    )

    lines = [f"*{display}* ({pos}) — {yr} splits\n"]
    stat_groups = data.get("stats") or []

    for sg in stat_groups:
        split_type = (sg.get("type") or {}).get("displayName") or ""
        splits = sg.get("splits") or []
        if not splits:
            continue

        lines.append(f"\n*{split_type}*")
        for sp in splits:
            label = sp.get("split", {}).get("description") or "Unknown"
            stat = sp.get("stat") or {}
            if is_pitcher:
                summary = (
                    f"{stat.get('era', '—')} ERA, "
                    f"{stat.get('strikeOuts', 0)} K, "
                    f"{stat.get('baseOnBalls', 0)} BB, "
                    f"{stat.get('inningsPitched', '—')} IP, "
                    f"{stat.get('avg', '—')} opp AVG"
                )
            else:
                summary = (
                    f"{stat.get('avg', '—')} AVG, "
                    f"{stat.get('ops', '—')} OPS, "
                    f"{stat.get('homeRuns', 0)} HR, "
                    f"{stat.get('rbi', 0)} RBI, "
                    f"{stat.get('atBats', 0)} AB"
                )
            lines.append(f"  {label}: {summary}")

    if len(lines) == 1:
        lines.append(f"No split data found for {yr}.")

    return "\n".join(lines)


@mcp.tool(
    description="Get a player's game-by-game log for a season. Shows recent performance trends, hot/cold streaks, and pitcher workload. Defaults to current season; pass a year like 2024. Returns the last 20 games by default.",
    annotations=ToolAnnotations(title="Get player game log", readOnlyHint=True, openWorldHint=True),
    structured_output=False,
)
def get_player_game_log(player_name: str, season: str = "", last_n: str = "20") -> str:
    name = player_name.strip()
    if not name:
        return "Pass a player name, like Aaron Judge."

    player = _find_player(name)
    if not player:
        return f"Couldn't find an MLB player named *{name}*. Try the full name."

    yr = int(season.strip()) if season.strip().isdigit() else date_cls.today().year
    n = int(last_n.strip()) if last_n.strip().isdigit() else 20
    display = player.get("fullName") or name
    pos = (player.get("primaryPosition") or {}).get("abbreviation") or "?"
    is_pitcher = str(pos).upper() in ("P", "SP", "RP")
    group = "pitching" if is_pitcher else "hitting"

    data = _get(
        f"/people/{player['id']}/stats",
        stats="gameLog",
        group=group,
        season=yr,
    )

    splits = ((data.get("stats") or [{}])[0].get("splits") or [])
    if not splits:
        return f"No game log found for *{display}* in {yr}."

    recent = splits[-n:]
    lines = [f"*{display}* ({pos}) — {yr} game log (last {len(recent)} games)\n"]

    for g in recent:
        stat = g.get("stat") or {}
        dt = g.get("date") or ""
        opp = (g.get("opponent") or {}).get("name") or "?"
        home = "vs" if g.get("isHome") else "@"

        if is_pitcher:
            ip = stat.get("inningsPitched", "—")
            k = stat.get("strikeOuts", 0)
            bb = stat.get("baseOnBalls", 0)
            er = stat.get("earnedRuns", 0)
            pitches = stat.get("numberOfPitches", "—")
            decision = stat.get("decision") or ""
            dec_bit = f" ({decision})" if decision else ""
            lines.append(f"  {dt} {home} {opp}: {ip} IP, {er} ER, {k} K, {bb} BB, {pitches} pitches{dec_bit}")
        else:
            ab = stat.get("atBats", 0)
            h = stat.get("hits", 0)
            hr = stat.get("homeRuns", 0)
            rbi = stat.get("rbi", 0)
            bb = stat.get("baseOnBalls", 0)
            k = stat.get("strikeOuts", 0)
            lines.append(f"  {dt} {home} {opp}: {h}-{ab}, {hr} HR, {rbi} RBI, {bb} BB, {k} K")

    return "\n".join(lines)


@mcp.tool(
    description="Compare two players side by side. Great for 'Player A vs Player B' roster decisions. Defaults to current season; pass a year like 2024.",
    annotations=ToolAnnotations(title="Compare players", readOnlyHint=True, openWorldHint=True),
    structured_output=False,
)
def compare_players(player_a: str, player_b: str, season: str = "") -> str:
    a_name = player_a.strip()
    b_name = player_b.strip()
    if not a_name or not b_name:
        return "Pass two player names to compare."

    player_a_data = _find_player(a_name)
    player_b_data = _find_player(b_name)
    if not player_a_data:
        return f"Couldn't find *{a_name}*. Try the full name."
    if not player_b_data:
        return f"Couldn't find *{b_name}*. Try the full name."

    yr = int(season.strip()) if season.strip().isdigit() else date_cls.today().year

    results = []
    for p in [player_a_data, player_b_data]:
        display = p.get("fullName") or "?"
        pos = (p.get("primaryPosition") or {}).get("abbreviation") or "?"
        team = (p.get("currentTeam") or {}).get("name") or "?"
        is_pitcher = str(pos).upper() in ("P", "SP", "RP")

        hitting = _season_stats(p["id"], "hitting", yr)
        pitching = _season_stats(p["id"], "pitching", yr)

        results.append({
            "display": display, "pos": pos, "team": team,
            "hitting": hitting, "pitching": pitching,
            "is_pitcher": is_pitcher,
        })

    lines = [f"*{results[0]['display']}* vs *{results[1]['display']}* — {yr}\n"]

    for r in results:
        lines.append(f"*{r['display']}* — {r['team']} ({r['pos']})")
        if r["hitting"]:
            lines.append("  Hitting: " + _format_hitting(r["hitting"]))
        if r["pitching"]:
            lines.append("  Pitching: " + _format_pitching(r["pitching"]))
        if not r["hitting"] and not r["pitching"]:
            lines.append(f"  No {yr} stats found.")
        lines.append("")

    # Add a quick edge summary for hitters
    a, b = results
    if a["hitting"] and b["hitting"]:
        ah, bh = a["hitting"], b["hitting"]
        edges_a, edges_b = [], []
        for stat, label in [("avg", "AVG"), ("ops", "OPS"), ("homeRuns", "HR"), ("rbi", "RBI"), ("stolenBases", "SB")]:
            va = float(ah.get(stat, 0) or 0)
            vb = float(bh.get(stat, 0) or 0)
            if va > vb:
                edges_a.append(label)
            elif vb > va:
                edges_b.append(label)
        if edges_a:
            lines.append(f"*{a['display']}* leads in: {', '.join(edges_a)}")
        if edges_b:
            lines.append(f"*{b['display']}* leads in: {', '.join(edges_b)}")

    return "\n".join(lines)


def _fmt(value, digits=3, pct=False):
    if value is None:
        return "—"
    if pct:
        return f"{value:.{digits}f}%"
    return f"{value:.{digits}f}"


def _quality_from_pitches(df) -> dict | None:
    if df is None or df.empty:
        return None

    pa = df.drop_duplicates(["game_pk", "at_bat_number"]) if "at_bat_number" in df.columns else df
    pa_n = len(pa)

    if "type" in df.columns:
        in_play = df[df["type"] == "X"]
    elif "description" in df.columns:
        in_play = df[df["description"].astype(str).str.startswith("hit_into_play")]
    else:
        in_play = df
    bip = in_play[in_play["launch_speed"].notna()] if "launch_speed" in in_play.columns else in_play.iloc[0:0]
    ev = bip["launch_speed"].dropna() if not bip.empty else None

    if "barrel" in df.columns and not bip.empty:
        barrels = int((bip["barrel"] == 1).sum())
    elif "launch_speed_angle" in df.columns and not bip.empty:
        barrels = int((bip["launch_speed_angle"] == 6).sum())
    else:
        barrels = 0

    hard = int((ev >= 95).sum()) if ev is not None and len(ev) else 0
    bip_n = len(ev) if ev is not None else 0

    xwoba_contact = None
    if "estimated_woba_using_speedangle" in df.columns and bip_n:
        xwoba_contact = float(bip["estimated_woba_using_speedangle"].dropna().mean())

    xwoba_pa = None
    if {"game_pk", "at_bat_number", "events"}.issubset(df.columns):
        last = df.sort_values("pitch_number").groupby(["game_pk", "at_bat_number"], as_index=False).tail(1)
        weights = []
        for _, row in last.iterrows():
            event = str(row.get("events") or "")
            if event == "strikeout":
                weights.append(0.0)
            elif event == "walk":
                weights.append(0.69)
            elif event == "hit_by_pitch":
                weights.append(0.72)
            else:
                x = row.get("estimated_woba_using_speedangle")
                if x == x:  # not NaN
                    weights.append(float(x))
        if weights:
            xwoba_pa = sum(weights) / len(weights)

    vs = {}
    if "p_throws" in df.columns:
        for hand, label in (("L", "vs LHP"), ("R", "vs RHP")):
            slice_df = df[df["p_throws"] == hand]
            if slice_df.empty:
                continue
            if "type" in slice_df.columns:
                slice_df = slice_df[slice_df["type"] == "X"]
            sbip = slice_df[slice_df["launch_speed"].notna()] if "launch_speed" in slice_df.columns else slice_df.iloc[0:0]
            if "estimated_woba_using_speedangle" in sbip.columns and not sbip.empty:
                vs[label] = float(sbip["estimated_woba_using_speedangle"].dropna().mean())

    return {
        "pa": pa_n,
        "bip": bip_n,
        "avg_ev": float(ev.mean()) if ev is not None and len(ev) else None,
        "max_ev": float(ev.max()) if ev is not None and len(ev) else None,
        "barrel_pct": (100 * barrels / bip_n) if bip_n else None,
        "hard_hit_pct": (100 * hard / bip_n) if bip_n else None,
        "xwoba_contact": xwoba_contact,
        "xwoba_pa": xwoba_pa,
        "vs": vs,
    }


@mcp.tool(
    description="Get Baseball Savant contact quality for a hitter: xwOBA, barrel %, hard-hit %, exit velo, and vs LHP/RHP. Defaults to full season (pass a year like 2024 for historical). For roster decisions, not box-score AVG.",
    annotations=ToolAnnotations(title="Get player quality of contact", readOnlyHint=True, openWorldHint=True),
    structured_output=False,
)
def get_player_quality(player_name: str, season: str = "") -> str:
    name = player_name.strip()
    if not name:
        return "Pass a hitter's name, like Aaron Judge or Freddie Freeman."

    player = _find_player(name)
    if not player:
        return f"Couldn't find an MLB player named *{name}*. Try the full name."

    pos = (player.get("primaryPosition") or {}).get("abbreviation") or "?"
    if str(pos).upper() in ("P", "SP", "RP"):
        return f"*{player.get('fullName') or name}* is listed as a pitcher. This tool is for hitter contact quality."

    import pybaseball
    from pybaseball import statcast_batter

    pybaseball.cache.enable()
    mlbam_id = int(player["id"])
    display = player.get("fullName") or name
    team = (player.get("currentTeam") or {}).get("name") or "Team unknown"

    yr = int(season.strip()) if season.strip().isdigit() else date_cls.today().year
    start = f"{yr}-03-01"
    end = f"{yr}-11-30"

    try:
        df = statcast_batter(start, end, player_id=mlbam_id)
    except Exception as exc:
        return f"Error fetching Savant data for {display}: {exc}"

    q = _quality_from_pitches(df)
    window = f"{yr} full season"

    if not q or q["pa"] == 0:
        return f"No Statcast batted-ball data for *{display}* in {yr}."

    lines = [
        f"*{display}* — {team} ({pos}) — contact quality",
        f"_Baseball Savant, {window}, {q['pa']} PA / {q['bip']} BIP_\n",
        f"xwOBA (PA approx): *{_fmt(q['xwoba_pa'])}*   xwOBA on contact: *{_fmt(q['xwoba_contact'])}*",
        f"Barrel %: *{_fmt(q['barrel_pct'], 1, pct=True)}*   Hard-hit %: *{_fmt(q['hard_hit_pct'], 1, pct=True)}*",
        f"Avg EV: *{_fmt(q['avg_ev'], 1)} mph*   Max EV: *{_fmt(q['max_ev'], 1)} mph*",
    ]
    if q["vs"]:
        bits = [f"{label} xwOBA(con): {_fmt(val)}" for label, val in q["vs"].items()]
        lines.append("Platoon: " + "   ".join(bits))
    lines.append("_Use this for roster/platoon calls; AVG/ERA live in get_player_stats._")
    return "\n".join(lines)


@mcp.tool(
    description="Get current MLB standings. Optional division or league filter such as AL East, NL West, American League, or National League. Omit the filter for all six divisions.",
    annotations=ToolAnnotations(title="Get MLB standings", readOnlyHint=True, openWorldHint=True),
    structured_output=False,
)
def get_standings(division: str = "") -> str:
    season = date_cls.today().year
    data = _get(
        "/standings",
        leagueId="103,104",
        season=season,
        hydrate="division,league",
    )
    records = data.get("records") or []
    if not records:
        return f"No standings available for {season}."

    wanted = division.strip().lower()
    lines = [f"MLB standings — {season}\n"]
    matched = 0

    for rec in records:
        div_name = (rec.get("division") or {}).get("nameShort") or (rec.get("division") or {}).get("name") or "Division"
        league_name = (rec.get("league") or {}).get("name") or ""
        label = f"{div_name}".strip()
        haystack = f"{league_name} {div_name}".lower()
        if wanted and wanted not in haystack and wanted not in div_name.lower() and wanted not in league_name.lower():
            continue
        matched += 1
        lines.append(f"*{div_name}*")
        teams = rec.get("teamRecords") or []
        teams = sorted(teams, key=lambda t: int(t.get("divisionRank") or 99))
        for t in teams:
            name = (t.get("team") or {}).get("name") or "Unknown"
            wins = t.get("wins", 0)
            losses = t.get("losses", 0)
            pct = t.get("winningPercentage", "—")
            gb = t.get("gamesBack") or "-"
            gb_bit = "—" if gb in ("-", "0") else f"{gb} GB"
            lines.append(f"  {t.get('divisionRank', '?')}. {name}  {wins}-{losses}  ({pct})  {gb_bit}")
        lines.append("")

    if wanted and matched == 0:
        return f"No standings matched *{division}*. Try AL East, NL West, American League, or National League."
    return "\n".join(lines).strip()


def _team_name(side: dict) -> str:
    return (side.get("team") or {}).get("name") or "TBD"


def _placeholder_team(name: str) -> bool:
    lowered = name.lower()
    return "seed" in lowered or "champion" in lowered


@mcp.tool(
    description="Get the current MLB postseason picture: series scores, who leads, live games, and the next scheduled playoff game. Optional filter such as Dodgers, ALDS, NLCS, or World Series.",
    annotations=ToolAnnotations(title="Get MLB postseason", readOnlyHint=True, openWorldHint=True),
    structured_output=False,
)
def get_postseason(series: str = "") -> str:
    season = date_cls.today().year
    try:
        data = _get("/schedule/postseason", season=season, hydrate="linescore")
    except Exception as exc:
        return f"⚠️ Could not load game data: {exc}"
    dates = data.get("dates") or []
    if not dates:
        return f"No postseason games found for {season}."

    grouped: dict[str, list] = defaultdict(list)
    for day in dates:
        for g in day.get("games") or []:
            desc = (g.get("description") or g.get("seriesDescription") or "Series").strip()
            key = re.sub(r"\s+Game\s+\d+\s*$", "", desc, flags=re.I).strip()
            grouped[key].append(g)

    wanted = series.strip().lower()
    lines = [f"MLB postseason — {season}\n"]
    matched = 0

    round_order = (
        "Wild Card",
        "Division Series",
        "ALDS",
        "NLDS",
        "Championship",
        "ALCS",
        "NLCS",
        "World Series",
    )

    def sort_key(item: tuple[str, list]) -> tuple[int, str]:
        name, _ = item
        idx = next((i for i, token in enumerate(round_order) if token.lower() in name.lower()), 99)
        return (idx, name)

    for name, games in sorted(grouped.items(), key=sort_key):
        games = sorted(games, key=lambda g: (g.get("officialDate") or "", g.get("seriesGameNumber") or 0))
        last = games[-1]
        away_name = _team_name(last["teams"]["away"])
        home_name = _team_name(last["teams"]["home"])
        if _placeholder_team(away_name) and _placeholder_team(home_name):
            continue

        blob = f"{name} {away_name} {home_name}".lower()
        if wanted and wanted not in blob:
            continue
        matched += 1

        played = [g for g in games if g.get("status", {}).get("abstractGameState") in ("Final", "Live")]
        latest = played[-1] if played else last
        away = latest["teams"]["away"]
        home = latest["teams"]["home"]
        away_w = (away.get("leagueRecord") or {}).get("wins", 0)
        home_w = (home.get("leagueRecord") or {}).get("wins", 0)
        needed = int((latest.get("gamesInSeries") or 3) // 2) + 1

        if away_w >= needed or home_w >= needed:
            winner = _team_name(away) if away_w > home_w else _team_name(home)
            lead = f"{winner} win the series {max(away_w, home_w)}-{min(away_w, home_w)}"
        elif away_w or home_w:
            leader = _team_name(away) if away_w > home_w else _team_name(home) if home_w > away_w else "Series tied"
            if away_w == home_w:
                lead = f"Series tied {away_w}-{home_w}"
            else:
                lead = f"{leader} lead {max(away_w, home_w)}-{min(away_w, home_w)}"
        else:
            lead = "Series not started"

        lines.append(f"*{name}* — {_team_name(away)} vs {_team_name(home)}")
        lines.append(f"  {lead} (first to {needed})")

        for g in games:
            state = g.get("status", {}).get("abstractGameState")
            detailed = (g.get("status", {}).get("detailedState") or "").strip()
            if detailed.lower() == "scheduled":
                state = "Preview"
            a = g["teams"]["away"]
            h = g["teams"]["home"]
            n = g.get("seriesGameNumber") or "?"
            date = g.get("officialDate") or ""
            if state == "Preview":
                continue
            if state == "Live":
                inn = g.get("linescore") or {}
                inn_bit = f"{inn.get('inningHalf', '')} {inn.get('currentInning', '')}".strip()
                lines.append(
                    f"  G{n} {date}: {_team_name(a)} {a.get('score')} @ {_team_name(h)} {h.get('score')}  🔴 LIVE {inn_bit}"
                )
            elif state == "Final":
                lines.append(
                    f"  G{n} {date}: {_team_name(a)} {a.get('score')}, {_team_name(h)} {h.get('score')}  — {detailed}"
                )

        upcoming = next((g for g in games if g.get("status", {}).get("abstractGameState") == "Preview"), None)
        if upcoming:
            a = upcoming["teams"]["away"]
            h = upcoming["teams"]["home"]
            lines.append(
                f"  Next: G{upcoming.get('seriesGameNumber')} {upcoming.get('officialDate')} "
                f"{_team_name(a)} @ {_team_name(h)}"
            )
        lines.append("")

    if wanted and matched == 0:
        return f"No postseason series matched *{series}*. Try Dodgers, ALDS, NLCS, or World Series."
    if matched == 0:
        return f"No active postseason series found for {season}."
    return "\n".join(lines).strip()


if __name__ == "__main__":
    import sys

    if "--http" in sys.argv:
        print(f"MLB MCP server on {APP_BASE_URL}/mcp")
        mcp.run(transport="streamable-http")
    else:
        mcp.run()

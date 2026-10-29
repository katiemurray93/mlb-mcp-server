# MLB MCP Server — Setup Guide (Workshop / Multi-Workspace)

This server runs on Heroku and lets participants from any Slack sandbox org install the app via a single link. You do the deploy once; they click once.

---

## Overview of what you'll do

1. Deploy the server to Heroku → get a permanent `https://` URL
2. Create the Slack app and paste that URL in two places
3. Enable distribution and grab the install link
4. Share the install link with workshop participants

---

## Step 1 — Deploy to Heroku

### 1a. Create the Heroku app

```bash
heroku login
heroku create your-mlb-mcp-server   # pick any unique name
```

### 1b. Set config vars

```bash
heroku config:set APP_BASE_URL=https://your-mlb-mcp-server.herokuapp.com
# You'll add SLACK_CLIENT_ID and SLACK_CLIENT_SECRET after step 2
```

### 1c. Push the code

```bash
git init          # if not already a git repo
git add .
git commit -m "initial"
git push heroku main
```

The server is now live at `https://your-mlb-mcp-server.herokuapp.com/mcp`.

---

## Step 2 — Create the Slack app

1. Go to https://api.slack.com/apps → **Create New App → From manifest**
2. Open `slack_manifest.json`, replace both instances of `YOUR-APP` with your actual Heroku app name, then paste the whole file
3. Click **Create**

On the app's **Basic Information** page, copy:
- **Client ID**
- **Client Secret**

Then set them on Heroku:

```bash
heroku config:set SLACK_CLIENT_ID=your_client_id
heroku config:set SLACK_CLIENT_SECRET=your_client_secret
```

---

## Step 3 — Enable distribution

In your Slack app settings, go to **Manage Distribution** and click **Activate Public Distribution**. Slack will check your OAuth redirect URL is configured — it should show a green checkmark.

Your shareable install link will be:

```
https://your-mlb-mcp-server.herokuapp.com/slack/install
```

That's the link you give to workshop participants.

---

## Step 4 — Participant install flow (what they do)

1. Click your install link
2. Slack shows the permissions screen — click **Allow**
3. They land on a success page confirming installation
4. In Slack: open the **Slackbot** DM → click **Apps** → add **MLB Stats**
5. Ask something like: *"What MLB games are on today?"* or *"What's the postseason picture?"*

---

## Local development (when iterating on tools)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Run locally (no Slack OAuth needed for tool testing)
python server.py --http
# Server on http://localhost:8000/mcp

# In a second terminal, expose with ngrok for Slack testing:
ngrok http 8000
```

To test a tool without Slack at all:
```python
from server import get_todays_games, get_postseason
print(get_todays_games())
print(get_postseason())
```

---

## Environment variables reference

| Variable | Required | Description |
|---|---|---|
| `APP_BASE_URL` | Yes | Full public URL, e.g. `https://your-app.herokuapp.com` |
| `SLACK_CLIENT_ID` | Yes (for OAuth) | From api.slack.com/apps Basic Information |
| `SLACK_CLIENT_SECRET` | Yes (for OAuth) | From api.slack.com/apps Basic Information |
| `PORT` | Auto (Heroku sets it) | HTTP port — don't set manually on Heroku |

---

## Available tools

| Tool | What it does |
|---|---|
| `get_todays_games` | Schedule and scores for today or any date |
| `get_player_stats` | Season hitting/pitching line by player name |
| `get_player_quality` | Savant contact quality: xwOBA, barrel %, exit velo |
| `get_standings` | Current standings, filterable by division/league |
| `get_postseason` | Postseason series scores and bracket, filterable by team/round |

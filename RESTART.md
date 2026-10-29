# Restart MLB Stats for Slackbot

Do this whenever you reboot, close the terminals, or ngrok gives you a new URL.
The tool only works while both processes below are running.

## 1. Start the Python server

```bash
cd "/Users/katie.murray/Library/Application Support/Claude-3p/local-agent-mode-sessions/d4748b41/00000000/477ddc1d/outputs/mlb-mcp-server"
source .venv/bin/activate
python server.py --http
```

Leave this terminal open. You want: `MLB MCP server on http://127.0.0.1:8000/mcp`

## 2. Start ngrok (second terminal)

```bash
ngrok http 8000
```

Copy the `https://....ngrok-free.dev` URL (no trailing slash).

## 3. Update Slack if the ngrok URL changed

1. Open https://api.slack.com/apps → **MLB Stats**
2. Left sidebar → **App Manifest**
3. In `mcp_servers` → `mlb-stats` → `url`, set:
   `https://YOUR-NEW-NGROK-HOST/mcp`
4. Save
5. If Slackbot still fails: **Features → MCP Servers** → **mlb-stats** → **Update Snapshot**

## 4. Test

In Slackbot: `What MLB games are on today?`

Optional: Slackbot → **+** → **Apps** → **MLB Stats** → **Reload tools**

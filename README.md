# TheoVsRose Bot 🎴

A Discord bot that tracks a 12-round card game between **Theo 🕷** and **Rose 🌹** using **reaction-based scoring** and logs everything to **Google Sheets (dark-themed)**. 
Includes a **13th-round tie-breaker** if the game is tied 6–6 after 12 rounds.

## Features
- 🕷 reaction = +1 point for **Theo**
- 🌹 reaction = +1 point for **Rose**
- Removing a reaction = undo (–1)
- Auto-detects end of game at 12 rounds; if 6–6, creates **Round 13** tie-breaker
- Announces the winner, increments **lifetime wins**, and resets the current game
- `/score` shows the current game and lifetime totals
- `/leaderboard` shows lifetime totals
- `/reset` resets the current game (Admin-only)
- `/round` posts a "Round Winner" prompt with both emojis (use this each round)

## Quick Start (Render / Docker / Local)

### 1) Create a Google Cloud Service Account
1. Go to Google Cloud Console → **APIs & Services** → **Credentials**.
2. Create **Service Account**, add role **Editor** (or least-privilege for Sheets).
3. Create a **JSON key** and download it as `service_account.json` and place beside `bot.py`.
4. Enable **Google Sheets API**.

### 2) Create a Google Sheet
- Create a new Google Sheet and share it with your service account email (e.g. `…@…iam.gserviceaccount.com`) with **Editor** access.
- Copy the Sheet ID from the URL (the long string between `/d/` and `/edit`).

### 3) Environment Variables
Set the following environment variables:
```
DISCORD_TOKEN=YOUR_DISCORD_BOT_TOKEN
SHEET_ID=YOUR_GOOGLE_SHEET_ID
GUILD_ID=OPTIONAL_GUILD_ID_FOR_COMMAND_SYNC   # e.g. 123456789012345678
BOT_ANNOUNCE_CHANNEL_ID=OPTIONAL_CHANNEL_ID   # to force announcements into a specific channel
```
> Note: The bot requires **Administrator** permissions (per your request).

### 4) Install & Run (Local)
```
python -m venv .venv
. .venv/bin/activate     # Windows: .\.venv\Scripts\activate
pip install -r requirements.txt
python bot.py
```

### 5) Deploy on Render.com (recommended)
- Create a **Web Service** from your repo (use `python bot.py` as start command).
- Add the environment variables.
- Upload `service_account.json` as a **Secret File** and set env var `GOOGLE_APPLICATION_CREDENTIALS=/etc/secrets/service_account.json`.

### 6) Invite the Bot
- Use Discord Developer Portal to create a bot, turn on **Message Content Intent**, and invite it with **Administrator**.
- In **OAuth2** → URL Generator, select: `applications.commands`, `bot`, and Administrator permission.

## Sheet Structure (created on first run if not present)

**Tabs**
- `State`
  - `game_id` | `round` | `theo_points` | `rose_points` | `tiebreaker` | `active`
- `Log`
  - `timestamp` | `guild_id` | `channel_id` | `message_id` | `user_id` | `emoji` | `player` | `delta` | `game_id` | `round`
- `Lifetime`
  - `player` | `lifetime_points` | `lifetime_wins`

The bot keeps `State` in sync, logs every reaction change in `Log`, and keeps `Lifetime` up to date on game end.

## Commands
- `/round` → posts a "Round Winner" message and pre-adds 🕷 and 🌹 reactions for convenience.
- `/score` → current game status + lifetime standings.
- `/leaderboard` → lifetime standings only.
- `/reset` → resets current game totals to 0–0 (Admin-only).
- `/setchannel` → set a default announcement channel for win/tie-breaker messages (Admin-only).

## Notes
- The bot only counts reactions on **its own round prompts**.
- A **game** is exactly **12 rounds**. If the totals are tied **6–6** after 12 rounds, the bot posts a **Round 13** prompt; the winner of that round wins the game (totals become 7–6).
- Removing a reaction from a round message **subtracts** the point for that player (undo).

Enjoy! 🎴🕷🌹

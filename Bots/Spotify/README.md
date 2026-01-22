# Spotify Bot

The Spotify bot lets viewers request songs and lets mods control playback.

It uses the Spotify Web API via the **spotipy** Python package.

---

## Setup (one-time)

### 1) Add Spotify keys to `.env`

Open `bot/.env` and fill in:

- `SPOTIPY_CLIENT_ID`
- `SPOTIPY_CLIENT_SECRET`

Leave `SPOTIPY_REDIRECT_URI` as-is unless you know you need to change it.

### 2) Enable the bot in `commands.txt`

Open `ChatManager/commands.txt` and make sure the Spotify bot is enabled:

```json
{ "id": "spotify", "enabled": true }
```

---

## Viewer commands

Commands start with `!`:

- `!sr <song or link>` — song request
- `!np` — now playing
- `!queue` — show queue

## Mod commands

- `!skip`
- `!play`
- `!pause`
- `!vol <0-100>`

Which roles can use which commands is configured in `ChatManager/commands.txt`.

---

## Troubleshooting

### Spotify bot won't authenticate

- Confirm `SPOTIPY_CLIENT_ID` and `SPOTIPY_CLIENT_SECRET` are correct in `.env`.
- Confirm `SPOTIPY_REDIRECT_URI` matches what your Spotify developer app expects.

### I don't want Spotify right now

Disable it:

1. Open `ChatManager/commands.txt`
2. Set the Spotify bot to `"enabled": false`
3. Restart the stack

# SSNChatWriter

SSNChatWriter connects to **SocialStream.Ninja (SSN)** and writes incoming chat messages into a local JSON file.

ChatManager then reads that file and does the rest (points, commands, routing).

You normally do not run this by itself — ChatSupervisor starts it for you.

---

## What you need to set

All settings come from `bot/.env`:

- `SSN_SESSION` — your SSN session code (from the dock page URL)
- `CHAT_FEED_PATH` — where the chat JSON is written (default is fine)

Optional tuning:

- `MAX_MESSAGES` — maximum messages kept in the file
- `ACTIVE_WINDOW_SECONDS` — time window used to decide if a user is “active”
- reconnect settings (`RECONNECT_*`)

---

## Output file

Default output file:

- `Overlays/UnifiedChat/chat_feed.json`

If that file is updating, SSNChatWriter is working.

---

## Common issues

### No messages appear in the overlay

1. Confirm SSN is running and connected to your platforms.
2. Confirm `.env` has the correct `SSN_SESSION`.
3. Check whether `Overlays/UnifiedChat/chat_feed.json` is changing while chat is active.

---

## Related docs

- Main guide: `../README.md`
- Overlays: `../Overlays/README.md`
- ChatManager: `../ChatManager/README.md`

# ChatManager

ChatManager is the “brain” that:

- reads the incoming chat feed file
- awards points
- detects commands (messages starting with `!`)
- routes commands to bot workers (Spotify, Slots, ...)
- writes overlay updates

You usually do not start ChatManager by itself — **ChatSupervisor** starts it for you.

---

## The main config file: `commands.txt`

Almost everything you will customize lives in:

- `ChatManager/commands.txt`

This file controls:

- which bots exist and whether they are enabled
- what commands exist and what they cost
- point earning rules
- which platforms get bot replies in chat vs overlay-only

### Most edited sections

#### 1) Points

Look for the `earning` section:

- `points_per_message`
- `points_per_minute_active`

Change numbers, save the file, restart the stack.

#### 2) Commands

Look for the `commands` array.

Each command includes:

- `command` (example: `slots`)
- `aliases` (example: `slot`)
- `cost_points`
- `cooldown_seconds`
- which bot should handle it (`bot` + `action`)

#### 3) Turn bots on/off

Look for the `bots` array and set:

- `"enabled": true` or `false`

If you disable a bot, consider also removing its commands from the `commands` array.

---

## Files ChatManager reads/writes

### Incoming feed

- Reads `chat_file` (normally: `Overlays/UnifiedChat/chat_feed.json`)

That path comes from `.env` via `${CHAT_FEED_PATH}`.

### Message pipeline (bus)

Under `ChatManager/bus/` you’ll see files like:

- `events.inbox.jsonl` (incoming normalized events)
- `spotify.inbox.jsonl`, `gamble.inbox.jsonl` (commands routed to bots)
- `*.outbox.jsonl` (bot responses)

These are “runtime” files. They are safe to delete when troubleshooting.

### State

Under `ChatManager/state/`:

- `user_state.json` (points, cooldowns, etc.)
- `offsets*.json` (where the readers are in the pipeline)
- `points_ledger.jsonl` (history of point changes)

---

## Commands for viewers

Commands start with `!`.

Examples (from the default config):

- `!points`
- `!slots 10`
- `!sr Blinding Lights by The Weeknd`

---

## Resetting ChatManager

Use the reset helper from the bot folder:

```bash
py clear_chatmanager_data.py --pipeline --state --yes
```

See: `../TOOLS.md`

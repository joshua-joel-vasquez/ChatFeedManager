# Gamble / Slots Bot

This bot runs the **slots** mini-game using viewer points.

Default command:

- `!slots <amount|max>`

Examples:

- `!slots 5`
- `!slots max`

---

## Where the rules live

Commands and costs are defined in:

- `ChatManager/commands.txt`

Look for the entry with:

- `"command": "slots"`
- `"aliases": ["slot"]`

You can change cooldowns, command cost, or even the command name there.

---

## Customizing wins / multipliers

Slots outcomes and multipliers are implemented inside:

- `Bots/Gamble/worker.py`

If you want to change the payout rules, symbols, or odds, that’s the file to edit.

Tip: if you want “configuration not code”, you can move the slot table into `ChatManager/commands.txt` (or a JSON file) and have the worker load it. That’s a common next step.

---

## Troubleshooting

### It says I’m queued but no result

That usually means the Gamble worker isn’t running.

1. Open `ChatManager/commands.txt`
2. Confirm the gamble bot is enabled:
   - `{"id": "gamble", "enabled": true}`
3. Restart the stack

---

## Related docs

- Main guide: `../../README.md`
- Bot list: `../README.md`

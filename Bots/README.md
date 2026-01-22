# Bots

Bots are the “workers” that respond to commands.

In this project, bots are started by ChatSupervisor based on what is enabled in:

- `ChatManager/commands.txt`

---

## Included bots

- **Spotify** (`Bots/Spotify`) — song requests and playback controls
- **Gamble / Slots** (`Bots/Gamble`) — slots game using points

## Turning a bot on/off

Open `ChatManager/commands.txt` and find the `"bots"` section.

Example:

```json
{
  "id": "spotify",
  "enabled": false
}
```

After changing this, restart the stack.

---

## Related docs

- Spotify: `Spotify/README.md`
- Gamble/Slots: `Gamble/README.md`
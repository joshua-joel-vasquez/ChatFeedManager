# ChatSupervisor

ChatSupervisor is the “boss” process that starts and monitors everything:

- Overlay web server (so OBS can load the overlay pages)
- SSNChatWriter (SSN → `chat_feed.json`)
- ChatManager services (ingestor / router_bank / emitter)
- Bot workers (Spotify, Gamble/Slots, etc.) defined in `ChatManager/commands.txt`

Most users never run this directly — they run it through `../run_all.py`.

---

## How it’s started

`run_all.py` launches:

```bash
python supervisor_inspector.py
```

and passes flags like overlay ports and OS mode.

---

## Key flags

- `--no-servers` — don’t start the web servers
- `--skip-writer` — don’t start SSNChatWriter
- `--no-workers` — don’t start bots
- `--same-console` — keep everything in one console window
- `--overlay-port 8080` — overlay web server port
- `--manager-port 8788` — ChatManager folder web server port

### Reliability

- `--restart-stale` — restart services/workers that appear stuck
- `--stale-services 45` — seconds before a service is considered stuck
- `--stale-workers 60` — seconds before a worker is considered stuck

### OS mode

- `--os auto` (default)
- `--os windows`
- `--os mac`

OS mode mainly affects how processes are spawned and how the supervisor shuts them down:

- **Windows mode:** uses `taskkill /T` so child process trees are stopped cleanly.
- **Mac mode:** uses process groups (`killpg`) so everything stops together.

---

## Environment (.env)

ChatSupervisor loads `bot/.env` once on startup so all child processes inherit the same settings.

See: `../.env.example`

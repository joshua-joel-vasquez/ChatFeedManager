# Tools & Common Tasks

This page is the “extra buttons” for ChatFeedManager.

If you only want a beginner setup, you can ignore this and just follow `README.md`.

---

## Running options (flags)

You run the system like this:

**Windows:**
```bash
py run_all.py
```

**Mac:**
```bash
python3 run_all.py
```

You can add flags after the command.

### Useful flags

- `--same-console`
  - Keeps everything in a single terminal window (simpler for troubleshooting).
- `--no-servers`
  - Does not start the two built-in web servers:
    - Overlay server (default port **8080**)
    - ChatManager file server (default port **8788**)
- `--skip-writer`
  - Does not start **SSNChatWriter** (use this if you’re not using SSN).
- `--no-workers`
  - Does not start bot workers (Spotify/Slots). You’ll still see chat + points.
- `--overlay-port 8080`
  - Change the overlay web server port.
- `--manager-port 8788`
  - Change the ChatManager web server port.

### Reliability (optional)

- `--restart-stale`
  - If a service looks stuck, the supervisor will restart it.
- `--stale-services 45`
- `--stale-workers 60`
- `--check-every 0.5`
- `--status-every 2.0`

### OS mode (only if you need it)

`run_all.py` auto-detects your OS. If you run into shutdown problems, you can force a mode:

- `--os windows`
- `--os mac`

Example:

```bash
py run_all.py --os windows
```

---

## Reset / clear data (helpful for testing)

The helper script is:

- `clear_chatmanager_data.py`

This clears “runtime” files like message pipelines, offsets, and cached points.

### The safest way: dry run first

```bash
py clear_chatmanager_data.py --all --dry-run
```

If the list looks right, run it for real:

```bash
py clear_chatmanager_data.py --all --yes
```

### Common reset recipes

- Clear message pipeline + offsets (keeps points):
  ```bash
  py clear_chatmanager_data.py --pipeline --state --yes
  ```

- Reset points (wipes point totals):
  ```bash
  py clear_chatmanager_data.py --reset-points --yes
  ```

- Clear overlay output files (keeps HTML):
  ```bash
  py clear_chatmanager_data.py --overlay --yes
  ```

---

## Change commands, points, or bot settings

All the “rules” live in:

- `ChatManager/commands.txt`

What you can change there:

- **Points:** under the `earning` section
- **Commands:** under the `commands` array (commands that go to bots)
- **Manager commands:** under `manager_commands` (handled directly by ChatManager)
- **Turn bots on/off:** under the `bots` array (`enabled: true/false`)

After you edit `commands.txt`, restart the stack.

---

## Port already in use

If you see “port already in use”:

- Something else is using **8080** (overlay) or **8788** (ChatManager).
- Fix by changing ports:

```bash
py run_all.py --overlay-port 8090 --manager-port 8790
```

Then your overlay URL becomes:

- `http://localhost:8090/overlays_points.html`

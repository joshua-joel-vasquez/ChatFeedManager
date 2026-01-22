# ChatFeedManager — Main Guide (Start Here)

This `bot/` folder is the **main** part of the project. If you want the overlay + points + bots running, you run it from here.

---

## What this does (plain English)

Think of this like a **chat control center** for your stream:

- **SocialStream.Ninja an outside application in which it links all social platform chats into 1 place (SSN)** pulls chat from your platforms.
- **SSNChatWriter** saves those incoming messages into a local file.
- **ChatManager** reads the chat file, awards points, and routes commands to bots.
- **Bots** (Spotify, Slots/Gamble, etc.) create replies/results.
- **Overlays** are simple web pages you add to OBS as a Browser Source.

The “boss” that starts everything is called **ChatSupervisor**. You normally launch it with `run_all.py`.

---

## Quick start (follow this first)

### 1) Install Python
You need **Python 3.11+** installed.

- **Windows:** during install, check **“Add Python to PATH”**.
- **Mac:** install Python 3 (or use the one you already have if it’s new enough).

### 2) Open a terminal in this `bot/` folder

This is just a window where you type commands.

**Windows (easy way):**
1. Open the `bot` folder in File Explorer
2. Click the address bar (the folder path)
3. Type `cmd` and press Enter

**Mac (easy way):**
1. Open “Terminal”
2. Type `cd ` (with a trailing space)
3. Drag the `bot` folder onto the Terminal window (it fills the path for you)
4. Press Enter

### 3) Install the required Python packages

In the terminal:

**Windows:**
```bash
py -m pip install -r requirements.txt
```

**Mac:**
```bash
python3 -m pip install -r requirements.txt
```

### 4) Put your private settings in one file: `.env`

Everything sensitive lives in **one** file:

- `bot/.env` ✅ (private, ignored by git)
- `bot/.env.example` ✅ (safe template)

Open `bot/.env` and fill in at least:

- `SSN_SESSION=...` (your SocialStream.Ninja session code)

Optional (only if you want the Spotify bot):

- `SPOTIPY_CLIENT_ID=...`
- `SPOTIPY_CLIENT_SECRET=...`

**Where to find `SSN_SESSION`:**
1. Open your SocialStream.Ninja dock page in a browser
2. Look at the address bar
3. Find `session=XXXX` and copy the part after `session=` into `.env`

### 5) Run everything

**Windows:**
```bash
py run_all.py
```

**Mac:**
```bash
python3 run_all.py
```

Leave this window open while streaming.

### 6) Check the overlay in your browser

Open a browser and go to:

- `http://localhost:8080/overlays_points.html`  (chat + points)
- `http://localhost:8080/overlay.html`          (basic overlay)
- `http://localhost:8080/overlay_pts_time.html` (variant overlay)

If you see a page, the overlay server is working.

### 7) Add the overlay to OBS

In OBS:

1. Add **Browser Source**
2. URL: `http://localhost:8080/overlays_points.html`
3. Set width/height (start with 1920×1080 and adjust)
4. Click OK

---

## Using it live (viewer commands)

Commands are typed in chat and start with **!**.

Common commands included in this project:

- `!points` — shows the user’s points
- `!slots 10` — spins slots using 10 points
- `!slots max` — spins slots using the most points allowed

Spotify commands (only if Spotify is enabled and configured):

- `!sr <song or link>` — song request
- `!np` — now playing
- `!queue` — show queue
- Mods: `!skip`, `!play`, `!pause`, `!vol 50`

Note: by default, some platforms (like TikTok) may show bot replies **only in the overlay** instead of posting back into chat. That’s on purpose to avoid platform issues/spam.

---

## How to stop it

When you’re done streaming:

1. Click the terminal window running the bot stack
2. Press **Ctrl + C**
3. Wait a second for it to shut down everything

If you ever get “stuck processes”, you can force an OS mode:

**Windows:**
```bash
py run_all.py --os windows
```

**Mac:**
```bash
python3 run_all.py --os mac
```

---

## Troubleshooting (most common issues)

### Overlay says “Connecting…” and never shows chat
1. Make sure you started the stack (`run_all.py`) and did not close the terminal.
2. Make sure SSN is running and your `SSN_SESSION` in `.env` matches your dock URL.
3. Open the overlay in a normal browser:
   - `http://localhost:8080/overlays_points.html`

### “Module not found” errors (requests / websockets / spotipy / dotenv)
You skipped dependency install. Run:

**Windows:**
```bash
py -m pip install -r requirements.txt
```

**Mac:**
```bash
python3 -m pip install -r requirements.txt
```

### Spotify bot errors
If you don’t want Spotify right now:

1. Open `ChatManager/commands.txt`
2. In the `"bots"` section, set the Spotify bot to `"enabled": false`

If you do want Spotify:

1. Fill in `SPOTIPY_CLIENT_ID` and `SPOTIPY_CLIENT_SECRET` in `.env`
2. Make sure `SPOTIPY_REDIRECT_URI` matches what your Spotify developer app expects

### Resetting everything (for testing)
There is a helper script that clears runtime files (bus/state/overlay output). See **Tools** below.

---

## Tools (reset, ports, flags)

See: `TOOLS.md`

---

## Documentation map (links to smaller READMEs)

- **ChatSupervisor (starts/monitors everything):** `ChatSupervisor/README.md`
- **ChatManager (points + command routing):** `ChatManager/README.md`
- **SSNChatWriter (SSN → chat_feed.json):** `SSNChatWriter/README.md`
- **Overlays (OBS browser sources):** `Overlays/README.md`
  - Unified overlay specifics: `Overlays/UnifiedChat/README.md`
- **Bots (all bots):** `Bots/README.md`
  - Spotify bot: `Bots/Spotify/README.md`
  - Gamble/Slots bot: `Bots/Gamble/README.md`

---

## Folder cheat-sheet (what lives where)

- `run_all.py` — starts everything (recommended)
- `ChatSupervisor/` — starts + monitors processes
- `ChatManager/` — reads chat, awards points, routes commands
- `SSNChatWriter/` — connects to SSN and writes chat feed file
- `Overlays/UnifiedChat/` — HTML overlay pages for OBS
- `Bots/` — bot workers (Spotify, slots, etc.)
- `clear_chatmanager_data.py` — reset helper

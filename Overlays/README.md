# Overlays (OBS Browser Sources)

Overlays are **local web pages** that OBS can display. They update live as chat arrives.

ChatSupervisor starts a tiny local web server (default port **8080**) that serves the overlay files from:

- `bot/Overlays/UnifiedChat/`

---

## Which overlay should I use?

Open these in your browser:

- `http://127.0.0.1:8080/overlays_points.html` — chat + points (recommended)
- `http://127.0.0.1:8080/overlay.html` — basic overlay
- `http://127.0.0.1:8080/overlay_pts_time.html` — variant overlay

---

## Add to OBS

1. In OBS, click **+** → **Browser Source**
2. URL: one of the links above
3. Set width/height (start with 1920×1080)
4. Click OK

Tip: If you don’t see updates, open the same URL in a normal browser first. If it works in a browser, it will work in OBS.

---

## Using OBS "Local File" (optional)

You can also point OBS at the **HTML file on disk** instead of a URL.

1. Add **Browser Source**
2. Check **Local File**
3. Select: `bot/Overlays/UnifiedChat/overlays_points.html`

Important: the bot stack (run_all.py) still needs to be running in the background,
because the overlay reads live data from `http://127.0.0.1:8080/`.

If you ever change the overlay port (run_all.py --overlay-port), add this to the end of your Local File URL:

- `?base=http://127.0.0.1:8080`  (replace 8080)

---

## Files overlays read

These files are generated while the system runs:

- `UnifiedChat/chat_feed.json` (incoming messages)
- `UnifiedChat/overlay_additions.jsonl` (overlay message stream)
- `UnifiedChat/overlay_events.jsonl` (events)
- `UnifiedChat/user_state.json` (points mirror for overlay)

These are runtime outputs. It is OK to delete them when troubleshooting.

---

## More details

See: `UnifiedChat/README.md`

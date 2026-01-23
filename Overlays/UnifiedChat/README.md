# UnifiedChat Overlay

This folder contains the actual HTML overlay files OBS loads.

---

## HTML pages

- `overlays_points.html` — recommended (chat + points)
- `overlay.html` — basic overlay
- `overlay_pts_time.html` — variant overlay

When ChatSupervisor is running, these are available at:

- `http://127.0.0.1:8080/overlays_points.html`
- `http://127.0.0.1:8080/overlay.html`
- `http://127.0.0.1:8080/overlay_pts_time.html`

If you use OBS **Local File** instead of a URL, keep `run_all.py` running. The
HTML will still fetch data from the local overlay server.

---

## What makes them update live?

These pages read runtime files that are written while the stack runs:

- `chat_feed.json` (written by SSNChatWriter)
- `overlay_additions.jsonl` (written by ChatManager)
- `overlay_events.jsonl` (written by ChatManager)
- `user_state.json` (points mirror written by ChatManager)

If you want a totally clean slate while testing, you can delete these runtime files (or use `clear_chatmanager_data.py`).

---

## Customizing the look

You can edit the HTML/CSS in these files like any normal web page.

Common beginner edits:

- change font size
- adjust spacing/margins
- move where messages appear

Tip: open the overlay in Chrome, right-click → Inspect (Developer Tools) to preview style changes.

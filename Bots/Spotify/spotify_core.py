import os
import re
from typing import Any, Dict, List, Optional, Tuple

import spotipy
from spotipy.oauth2 import SpotifyOAuth
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env"))

TRACK_URL_RE = re.compile(r"open\.spotify\.com/track/([A-Za-z0-9]+)")
TRACK_URI_RE = re.compile(r"spotify:track:([A-Za-z0-9]+)")
BY_ARTIST_RE = re.compile(r"^(?P<song>.+?)\s+by\s+(?P<artist>.+?)\s*$", re.IGNORECASE)


def clamp(n: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, n))


def extract_track_id(query: str) -> Optional[str]:
    if not query:
        return None
    m = TRACK_URL_RE.search(query)
    if m:
        return m.group(1)
    m = TRACK_URI_RE.search(query)
    if m:
        return m.group(1)
    return None


def fmt_track(track: Dict[str, Any]) -> str:
    if not track:
        return "Unknown Track"
    name = track.get("name", "Unknown")
    artists = ", ".join([a.get("name", "") for a in track.get("artists", []) if a.get("name")])
    return f"{name} — {artists}".strip(" —")


def make_spotify() -> spotipy.Spotify:
    scopes = "user-read-playback-state user-modify-playback-state user-read-currently-playing"
    cache_path = os.path.join(BASE_DIR, ".spotify_token_cache")
    return spotipy.Spotify(
        auth_manager=SpotifyOAuth(
            scope=scopes,
            open_browser=True,
            cache_path=cache_path,
        )
    )


def ensure_active_device(sp: spotipy.Spotify) -> Tuple[bool, str]:
    try:
        devices = sp.devices().get("devices", [])
        if not devices:
            return False, "No Spotify device found. Open Spotify and start playing something."
        return True, ""
    except Exception as e:
        return False, f"Could not read devices: {type(e).__name__}: {e}"


def get_now_playing(sp: spotipy.Spotify) -> Tuple[Optional[str], Optional[str]]:
    try:
        pb = sp.current_playback()
        if not pb or not pb.get("item"):
            return None, None
        item = pb["item"]
        return fmt_track(item), (item.get("external_urls", {}) or {}).get("spotify")
    except Exception:
        try:
            cp = sp.currently_playing()
            if not cp or not cp.get("item"):
                return None, None
            item = cp["item"]
            return fmt_track(item), (item.get("external_urls", {}) or {}).get("spotify")
        except Exception:
            return None, None


def get_queue(sp: spotipy.Spotify, limit: int) -> Tuple[Optional[str], List[str]]:
    try:
        q = sp.queue()  # requires newer spotipy
        now_item = q.get("currently_playing")
        queue_items = q.get("queue", []) or []
        now_str = fmt_track(now_item) if now_item else None
        up_next = [fmt_track(t) for t in queue_items[:limit]]
        return now_str, up_next
    except Exception:
        now_str, _ = get_now_playing(sp)
        return now_str, []


def search_track(sp: spotipy.Spotify, query: str) -> Optional[Dict[str, Any]]:
    try:
        r = sp.search(q=query, type="track", limit=3)
        items = (((r or {}).get("tracks") or {}).get("items")) or []
        return items[0] if items else None
    except Exception:
        return None


def search_track_robust(sp: spotipy.Spotify, raw: str) -> Optional[Dict[str, Any]]:
    q = (raw or "").strip()
    if not q:
        return None

    # 1) direct track id/uri/url
    tid = extract_track_id(q)
    if tid:
        try:
            return sp.track(tid)
        except Exception:
            pass

    # 2) "{Song} by {Artist}" format -> structured query
    m = BY_ARTIST_RE.match(q)
    if m:
        song = (m.group("song") or "").strip()
        artist = (m.group("artist") or "").strip()
        if song and artist:
            structured = f'track:"{song}" artist:"{artist}"'
            t = search_track(sp, structured)
            if t:
                return t

    # 3) try with and without quotes
    t = search_track(sp, q)
    if t:
        return t

    # 4) small cleanup attempts
    cleaned = re.sub(r"\s+", " ", q).strip()
    if cleaned != q:
        t = search_track(sp, cleaned)
        if t:
            return t

    return None


def add_to_queue(sp: spotipy.Spotify, track_uri: str) -> Tuple[bool, str]:
    try:
        sp.add_to_queue(track_uri)
        return True, ""
    except Exception as e:
        return False, f"Failed to add to queue: {type(e).__name__}: {e}"


def play(sp: spotipy.Spotify) -> Tuple[bool, str]:
    try:
        sp.start_playback()
        return True, ""
    except Exception as e:
        return False, f"Failed to play: {type(e).__name__}: {e}"


def pause(sp: spotipy.Spotify) -> Tuple[bool, str]:
    try:
        sp.pause_playback()
        return True, ""
    except Exception as e:
        return False, f"Failed to pause: {type(e).__name__}: {e}"


def skip(sp: spotipy.Spotify) -> Tuple[bool, str]:
    try:
        sp.next_track()
        return True, ""
    except Exception as e:
        return False, f"Failed to skip: {type(e).__name__}: {e}"


def set_volume(sp: spotipy.Spotify, vol: int) -> Tuple[bool, str]:
    try:
        sp.volume(clamp(vol, 0, 100))
        return True, ""
    except Exception as e:
        return False, f"Failed to set volume: {type(e).__name__}: {e}"

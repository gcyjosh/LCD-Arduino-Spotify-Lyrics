"""
Spotify → Arduino LCD Lyrics Display (Synced)
----------------------------------------------
Requirements:
  pip install spotipy pyserial requests
"""

import time
import re
import serial
import spotipy
import requests
from spotipy.oauth2 import SpotifyOAuth

# ── CONFIG ────────────────────────────────────────────────────────────────────
SPOTIPY_CLIENT_ID     = "6f530ae45c984f9f890acc0a09b90edf"
SPOTIPY_CLIENT_SECRET = "6fe3efa2b4314872933af086c7867f0e"
SPOTIPY_REDIRECT_URI  = "http://127.0.0.1:8080/callback"

SERIAL_PORT         = "/dev/tty.usbmodem101"
SERIAL_BAUD         = 9600
LCD_COLS            = 16
POLL_INTERVAL       = 5
FALLBACK_LINE_DELAY = 2.0
SCROLL_SPEED        = 0.35   # seconds between scroll steps for long titles
# ─────────────────────────────────────────────────────────────────────────────


def get_spotify_client():
    return spotipy.Spotify(auth_manager=SpotifyOAuth(
        client_id=SPOTIPY_CLIENT_ID,
        client_secret=SPOTIPY_CLIENT_SECRET,
        redirect_uri=SPOTIPY_REDIRECT_URI,
        scope="user-read-currently-playing",
        open_browser=True
    ))


def get_playback_state(sp):
    """
    Returns (artist, title, duration_ms, progress_ms) if playing,
    or None if stopped/paused/nothing.
    """
    result = sp.currently_playing()
    if result and result.get("is_playing") and result.get("item"):
        item = result["item"]
        return (
            item["artists"][0]["name"],
            item["name"],
            item["duration_ms"],
            result.get("progress_ms", 0)
        )
    return None


def fetch_synced_lyrics(artist, title, duration_ms):
    # Try lrclib.net first (synced lyrics), with 2 attempts
    for attempt in range(2):
        try:
            params = {"artist_name": artist, "track_name": title, "duration": duration_ms // 1000}
            resp   = requests.get("https://lrclib.net/api/get", params=params, timeout=8)
            if resp.status_code == 200:
                data = resp.json()
                lrc  = data.get("syncedLyrics")
                if lrc:
                    parsed = parse_lrc(lrc)
                    if parsed:
                        print(f"[lyrics] Got {len(parsed)} synced lines from lrclib.")
                        return parsed, True
                plain = data.get("plainLyrics")
                if plain:
                    print("[lyrics] No sync available, using lrclib plain lyrics.")
                    lines = [l.strip() for l in plain.splitlines() if l.strip()]
                    return lines, False
            break
        except Exception as e:
            print(f"[lyrics] lrclib attempt {attempt+1} failed: {e}")

    # Fall back to lyrics.ovh
    try:
        print("[lyrics] Trying lyrics.ovh as fallback...")
        url  = f"https://api.lyrics.ovh/v1/{requests.utils.quote(artist)}/{requests.utils.quote(title)}"
        resp = requests.get(url, timeout=8)
        if resp.status_code == 200:
            raw   = resp.json().get("lyrics", "")
            lines = [l.strip() for l in raw.splitlines() if l.strip()]
            if lines:
                print(f"[lyrics] Got {len(lines)} plain lines from lyrics.ovh.")
                return lines, False
    except Exception as e:
        print(f"[lyrics] lyrics.ovh failed: {e}")

    return None, False


def parse_lrc(lrc_text):
    pattern = re.compile(r'\[(\d+):(\d+(?:\.\d+)?)\](.*)')
    result  = []
    for line in lrc_text.splitlines():
        m = pattern.match(line.strip())
        if m:
            ts   = int(m.group(1)) * 60 + float(m.group(2))
            text = m.group(3).strip()
            if text:
                result.append((ts, text))
    result.sort(key=lambda x: x[0])
    return result or None


def split_lyric_line(text, width=LCD_COLS):
    parts = re.split(r'(?<=[,!?])\s+', text)
    result = []
    for part in parts:
        if len(part) <= width:
            result.append(part)
        else:
            words, current = part.split(), ""
            for word in words:
                if not current:
                    current = word
                elif len(current) + 1 + len(word) <= width:
                    current += " " + word
                else:
                    result.append(current)
                    current = word
            if current:
                result.append(current)
    return result or [""]


def build_frames_synced(synced_lines):
    phrases = []
    for ts, text in synced_lines:
        for phrase in split_lyric_line(text):
            phrases.append((ts, phrase))

    if not phrases:
        return []

    frames = []
    i = 0
    while i < len(phrases):
        ts = phrases[i][0]
        group = []
        j = i
        while j < len(phrases) and phrases[j][0] == ts:
            group.append(phrases[j][1])
            j += 1

        gap      = (phrases[j][0] - ts) if j < len(phrases) else 4.0
        n        = len(group)
        interval = max(0.5, min(3.0, gap / n))

        for k, phrase in enumerate(group):
            frame_ts = ts + k * interval
            row2     = group[k + 1] if k + 1 < n else ""
            frames.append((frame_ts, phrase, row2))

        i = j

    return frames


def build_frames_plain(plain_lines):
    phrases = []
    for line in plain_lines:
        phrases.extend(split_lyric_line(line))

    frames = []
    i = 0
    while i < len(phrases):
        p1 = phrases[i]
        if i + 1 < len(phrases):
            frames.append((p1, phrases[i + 1]))
            i += 2
        else:
            frames.append((p1, ""))
            i += 1
    return frames


def send_to_arduino(ser, line1, line2=""):
    line1 = line1[:LCD_COLS].ljust(LCD_COLS)
    line2 = line2[:LCD_COLS].ljust(LCD_COLS)
    ser.write(f"{line1}|{line2}\n".encode("utf-8"))


def scroll_title(ser, title, artist, sp, current_track):
    """
    Scroll a long title across row1 while artist stays on row2.
    Loops until lyrics are ready or track changes/stops.
    Returns None if still same track, or new state tuple if changed/stopped.
    """
    note      = "\x08"
    full      = note + " " + title
    padded    = full + "    "  # trailing spaces before loop
    display   = padded + full  # full scroll buffer

    # If it fits, just show it statically — no scroll needed
    if len(full) <= LCD_COLS:
        send_to_arduino(ser, full, artist[:LCD_COLS])
        return None

    pos = 0
    while True:
        window = display[pos:pos + LCD_COLS]
        send_to_arduino(ser, window, artist[:LCD_COLS])
        time.sleep(SCROLL_SPEED)

        pos += 1
        if pos >= len(padded):  # loop back
            pos = 0

        # Check playback state every scroll step
        state = get_playback_state(sp)
        if state is None:
            return "stopped"
        if (state[0], state[1]) != current_track:
            return state

        # Signal that we should break out (caller will check lyrics ready)
        if hasattr(scroll_title, "_stop") and scroll_title._stop:
            scroll_title._stop = False
            return None


def wait_until(target, sp, current_track):
    """
    Sleep until target wall time, checking every 0.2s.
    Returns:
      None         — time elapsed normally
      "stopped"    — playback stopped
      tuple        — new track started
    """
    while time.time() < target:
        time.sleep(0.2)
        state = get_playback_state(sp)
        if state is None:
            return "stopped"
        if (state[0], state[1]) != current_track:
            return state
    return None


def play_synced(ser, synced_lines, sp, current_track, progress_ms):
    frames     = build_frames_synced(synced_lines)
    start_s    = progress_ms / 1000.0
    wall_start = time.time() - start_s
    last_ts    = None

    print(f"[lyrics] {len(frames)} frames from {start_s:.1f}s")

    for ts, row1, row2 in frames:
        if ts < start_s - 2.0:
            last_ts = ts
            continue

        if ts != last_ts:
            result = wait_until(wall_start + ts, sp, current_track)
            if result is not None:
                return result   # stopped or new track
        last_ts = ts

        send_to_arduino(ser, row1, row2)
        print(f"LCD [{ts:.1f}s]: [{row1}] [{row2}]")

    return None


def play_plain(ser, plain_lines, sp, current_track):
    frames = build_frames_plain(plain_lines)
    print(f"[lyrics] {len(frames)} frames (plain, no timestamps)")

    for row1, row2 in frames:
        result = wait_until(time.time() + FALLBACK_LINE_DELAY, sp, current_track)
        if result is not None:
            return result
        send_to_arduino(ser, row1, row2)
        print(f"LCD: [{row1}] [{row2}]")

    return None


def main():
    print("[init] Connecting to Spotify (browser will open for login)...")
    sp = get_spotify_client()
    try:
        sp.currently_playing()
    except Exception:
        pass
    print("[init] Spotify auth complete.")

    print(f"[init] Opening serial port {SERIAL_PORT} @ {SERIAL_BAUD} baud...")
    ser          = serial.Serial()
    ser.port     = SERIAL_PORT
    ser.baudrate = SERIAL_BAUD
    ser.timeout  = 1
    ser.rtscts   = False
    ser.dsrdtr   = False
    ser.open()
    time.sleep(2)
    print("[init] Serial port open.")

    current_track  = None
    current_lyrics = None
    is_synced      = False

    send_to_arduino(ser, "Waiting for", "Spotify...")

    while True:
        state = get_playback_state(sp)

        # ── Nothing playing ──
        if not state:
            if current_track is not None:
                print("[spotify] Playback stopped.")
                send_to_arduino(ser, "Paused", "")
                current_track  = None
                current_lyrics = None
            time.sleep(POLL_INTERVAL)
            continue

        artist, title, duration_ms, progress_ms = state
        track = (artist, title)

        # ── New track ──
        if track != current_track:
            print(f"[spotify] Now playing: {artist} – {title}")
            current_track  = track
            current_lyrics = None

            # Start scrolling title while fetching lyrics in background
            # Fetch first so scroll duration = fetch time
            print(f"[lyrics] Fetching lyrics for {artist} – {title}...")

            note       = "\x08"
            full_title = note + " " + title

            # Show title immediately
            send_to_arduino(ser, full_title[:LCD_COLS], artist[:LCD_COLS])

            # Fetch lyrics
            lyrics, synced = fetch_synced_lyrics(artist, title, duration_ms)

            if not lyrics:
                print("[lyrics] Not found.")
                send_to_arduino(ser, "No lyrics", "found :(")
                current_lyrics = None
                time.sleep(POLL_INTERVAL)
                continue

            # Re-fetch progress after lyrics loaded
            fresh = get_playback_state(sp)
            if fresh and (fresh[0], fresh[1]) == (artist, title):
                progress_ms = fresh[3]
            else:
                # Track changed or stopped during fetch
                continue

            current_lyrics = lyrics
            is_synced      = synced

            # Scroll title if too long, until first lyric timestamp arrives
            if is_synced and len(full_title) > LCD_COLS:
                first_ts   = current_lyrics[0][0]
                scroll_end = time.time() - progress_ms / 1000.0 + first_ts

                note   = "\x08"
                full   = note + " " + title
                padded = full + "    "
                buf    = padded + full
                pos    = 0

                while time.time() < scroll_end:
                    window = buf[pos:pos + LCD_COLS]
                    send_to_arduino(ser, window, artist[:LCD_COLS])
                    time.sleep(SCROLL_SPEED)
                    pos = (pos + 1) % len(padded)

                    s = get_playback_state(sp)
                    if s is None or (s[0], s[1]) != track:
                        current_track  = None
                        current_lyrics = None
                        break

        # ── Play lyrics ──
        if current_lyrics:
            if is_synced:
                result = play_synced(ser, current_lyrics, sp, current_track, progress_ms)
            else:
                result = play_plain(ser, current_lyrics, sp, current_track)

            if result == "stopped":
                print("[spotify] Playback stopped during lyrics.")
                send_to_arduino(ser, "Paused", "")
                current_track  = None
                current_lyrics = None
            elif result is not None:
                # New track — loop immediately without sleeping
                current_track  = (result[0], result[1])
                current_lyrics = None
            else:
                # Lyrics finished naturally
                current_lyrics = None
        else:
            time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[exit] Stopped.")

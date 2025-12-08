"""
YAAP - Yet Another Audio Player
"""

import curses
import subprocess
import threading
import time
import json
import os
import tempfile
import urllib.request
import urllib.parse
import re
from typing import List, Dict, Optional
import sys
from pathlib import Path
import socket  # <- for mpv IPC


class YouTubeTUI:
    def __init__(self, stdscr):
        self.stdscr = stdscr
        self.search_query = ""
        self.search_input = ""
        self.results: List[Dict] = []
        self.selected_index = 0
        self.playing = False
        self.current_video: Optional[Dict] = None
        self.mpv_process: Optional[subprocess.Popen] = None
        self.cava_thread: Optional[threading.Thread] = None
        self.audio_only = True
        self.thumbnails: Dict[str, List[str]] = {}
        self.search_mode = False
        self.cava_output: List[str] = []
        self.lyrics: List[str] = []
        self.current_lyric_line = 0
        self.show_lyrics = True

        # MPV IPC + timing + synced lyrics
        self.mpv_socket_path: Optional[str] = None
        self.playback_time: float = 0.0
        self.playback_duration: float = 0.0
        self.synced_lyrics: List[tuple] = []  # (seconds, text)

        curses.start_color()
        curses.init_pair(1, curses.COLOR_CYAN, curses.COLOR_BLACK)
        curses.init_pair(2, curses.COLOR_GREEN, curses.COLOR_BLACK)
        curses.init_pair(3, curses.COLOR_YELLOW, curses.COLOR_BLACK)
        curses.init_pair(4, curses.COLOR_RED, curses.COLOR_BLACK)
        curses.init_pair(5, curses.COLOR_MAGENTA, curses.COLOR_BLACK)
        curses.init_pair(6, curses.COLOR_WHITE, curses.COLOR_BLACK)

        curses.curs_set(0)
        self.stdscr.timeout(100)

        curses.mousemask(curses.ALL_MOUSE_EVENTS | curses.REPORT_MOUSE_POSITION)

        self.has_cava = self.check_command("cava")

        self.thumb_dir = tempfile.mkdtemp(prefix="yaap_")

    def check_command(self, cmd):
        """Check if a command exists"""
        try:
            subprocess.run([cmd, "--version"], capture_output=True, timeout=1)
            return True
        except Exception:
            return False

    def download_thumbnail(self, video_id, thumb_url):
        """Download and convert thumbnail to ASCII"""
        if video_id in self.thumbnails:
            return

        try:
            thumb_path = os.path.join(self.thumb_dir, f"{video_id}.jpg")
            urllib.request.urlretrieve(thumb_url, thumb_path)

            try:
                result = subprocess.run(
                    ["jp2a", "--width=40", "--height=20", thumb_path],
                    capture_output=True,
                    text=True,
                    timeout=2,
                )
                if result.returncode == 0:
                    self.thumbnails[video_id] = result.stdout.split("\n")
                else:
                    self.thumbnails[video_id] = self.get_placeholder_thumb()
            except Exception:
                self.thumbnails[video_id] = self.get_placeholder_thumb()

        except Exception:
            self.thumbnails[video_id] = self.get_placeholder_thumb()

    def get_placeholder_thumb(self):
        """Get placeholder thumbnail"""
        return [
            "╔════════════════════════════════════════╗",
            "║                                        ║",
            "║                  ♪                     ║",
            "║               YouTube                  ║",
            "║                                        ║",
            "╚════════════════════════════════════════╝",
        ]

    def draw_header(self):
        """Draw the application header"""
        height, width = self.stdscr.getmaxyx()
        title = "♪ Yet Another Audio Player ♪"
        self.stdscr.attron(curses.color_pair(1) | curses.A_BOLD)
        self.stdscr.addstr(0, max(0, (width - len(title)) // 2), title)
        self.stdscr.attroff(curses.color_pair(1) | curses.A_BOLD)

        mode = "AUDIO" if self.audio_only else "VIDEO"
        lyrics_status = "LYRICS:ON" if self.show_lyrics else "LYRICS:OFF"

        status = f"Mode: {mode} | {lyrics_status}"
        if len(status) + 2 < width:
            self.stdscr.addstr(
                1, width - len(status) - 2, status, curses.color_pair(3)
            )

    def draw_search_box(self):
        """Draw the search input box"""
        height, width = self.stdscr.getmaxyx()
        self.stdscr.attron(curses.color_pair(2) | curses.A_BOLD)
        self.stdscr.addstr(3, 2, "Search: ")
        self.stdscr.attroff(curses.color_pair(2) | curses.A_BOLD)

        if self.search_mode:
            display_text = self.search_input + "█"
            self.stdscr.addstr(3, 11, " " * (width - 13))
            self.stdscr.addstr(
                3,
                11,
                display_text[: width - 13],
                curses.color_pair(3) | curses.A_BOLD,
            )
        else:
            display_query = (
                self.search_query
                if self.search_query
                else "[Click or press 's' to search]"
            )
            self.stdscr.addstr(3, 11, " " * (width - 13))
            self.stdscr.addstr(
                3, 11, display_query[: width - 13], curses.color_pair(6)
            )

    def draw_thumbnail(self, y_start, x_start, video_id):
        """Draw thumbnail (ASCII art or placeholder)"""
        height, width = self.stdscr.getmaxyx()

        if video_id not in self.thumbnails:
            thumb_lines = self.get_placeholder_thumb()
        else:
            thumb_lines = self.thumbnails[video_id]

        try:
            for i, line in enumerate(thumb_lines[:6]):
                if (
                    y_start + i < height - 2
                    and len(line) > 0
                    and x_start < width
                ):
                    self.stdscr.addstr(
                        y_start + i,
                        x_start,
                        line[: max(0, width - x_start - 1)],
                        curses.color_pair(1),
                    )
        except Exception:
            pass

    def draw_results(self):
        """Draw search results with thumbnails"""
        height, width = self.stdscr.getmaxyx()

        if self.playing:
            results_width = width // 2 - 2
        else:
            results_width = width - 4

        if not self.results:
            if self.search_query:
                self.stdscr.addstr(
                    6, 2, "no results found.", curses.color_pair(4)
                )
            else:
                self.stdscr.addstr(
                    6, 2, "search for music or videos", curses.color_pair(6)
                )
            return

        self.stdscr.attron(curses.color_pair(2) | curses.A_BOLD)
        self.stdscr.addstr(5, 2, f"results ({len(self.results)}):")
        self.stdscr.attroff(curses.color_pair(2) | curses.A_BOLD)

        available_height = height - 8
        results_per_page = max(1, available_height // 8)

        start_idx = max(0, self.selected_index - results_per_page + 1)
        end_idx = min(len(self.results), start_idx + results_per_page)

        y_pos = 7
        for i in range(start_idx, end_idx):
            if y_pos >= height - 2:
                break

            result = self.results[i]
            is_selected = i == self.selected_index

            self.draw_thumbnail(y_pos, 3, result.get("id", ""))

            prefix = "▶ " if is_selected else "  "
            title = result.get("title", "Unknown")[
                : max(0, results_width - 50)
            ]
            duration = result.get("duration", "N/A")
            channel = result.get("channel", "Unknown")[:30]

            title_x = 48

            if title_x < width:
                if is_selected:
                    self.stdscr.attron(
                        curses.color_pair(5)
                        | curses.A_REVERSE
                        | curses.A_BOLD
                    )
                    self.stdscr.addstr(
                        y_pos,
                        title_x,
                        f"{prefix}{title}"[: max(0, results_width - 46)],
                    )
                    self.stdscr.attroff(
                        curses.color_pair(5)
                        | curses.A_REVERSE
                        | curses.A_BOLD
                    )
                else:
                    self.stdscr.addstr(
                        y_pos,
                        title_x,
                        f"{prefix}{title}"[: max(0, results_width - 46)],
                    )

                info = f"  {channel} | {duration}"
                if y_pos + 1 < height:
                    self.stdscr.addstr(
                        y_pos + 1,
                        title_x,
                        info[: max(0, results_width - 46)],
                        curses.color_pair(6),
                    )

            y_pos += 8

    def draw_progress_bar(self, width: int) -> str:
        cur = self.format_time(self.playback_time)
        dur = self.format_time(self.playback_duration)

        # Leave space for times and spaces around bar
        bar_width = max(10, width - len(cur) - len(dur) - 4)
        pos = 0
        if self.playback_duration > 0:
            pos = int((self.playback_time / self.playback_duration) * bar_width)
            pos = max(0, min(bar_width - 1, pos))

        bar_chars = []
        for i in range(bar_width):
            if i == pos:
                bar_chars.append("○")
            else:
                bar_chars.append("─")

        bar = "".join(bar_chars)
        return f"{cur} {bar} {dur}"

    def draw_cava_visualizer(self):
        """Pretty Cava visualizer with vertical bars + time/progress inside box."""
        if not self.playing or not self.has_cava:
            return

        height, width = self.stdscr.getmaxyx()
        viz_x = width // 2 + 2
        viz_width = width // 2 - 4
        viz_height = 18  # total height including border

        if viz_width <= 10 or viz_x >= width:
            return

        try:
            # Draw outer box
            self.stdscr.attron(curses.color_pair(2))
            if 7 < height:
                self.stdscr.addstr(
                    7, viz_x, "╔" + "═" * (viz_width - 2) + "╗"
                )

            for i in range(1, viz_height - 1):
                if 7 + i < height - 1:
                    self.stdscr.addstr(
                        7 + i,
                        viz_x,
                        "║" + " " * (viz_width - 2) + "║",
                    )

            if 7 + viz_height - 1 < height:
                self.stdscr.addstr(
                    7 + viz_height - 1,
                    viz_x,
                    "╚" + "═" * (viz_width - 2) + "╝",
                )

            # Draw progress bar at the top inner line (inside the box)
            if 8 < height:
                inner_width = max(0, viz_width - 2)
                progress = self.draw_progress_bar(inner_width)
                self.stdscr.addstr(
                    8,
                    viz_x + 1,
                    progress[:inner_width],
                    curses.color_pair(3) | curses.A_BOLD,
                )

            # Visualizer area below the progress bar
            top_inner = 9
            bottom_inner = 7 + viz_height - 2  # last inner row

            if self.cava_output:
                # We use the first cava_output line (levels) and turn it into vertical bars
                line = self.cava_output[0][: max(0, viz_width - 2)]
                blocks = "▁▂▃▄▅▆▇█"

                for col, ch in enumerate(line):
                    if col >= viz_width - 2:
                        break

                    if ch in blocks:
                        level = blocks.index(ch) + 1  # 1..8
                    else:
                        level = 1

                    # Map level to number of rows to fill
                    max_rows = max(1, bottom_inner - top_inner)
                    # Scale 1..8 -> 1..max_rows
                    height_cols = max(1, int(level * max_rows / 8))

                    for v in range(height_cols):
                        row = bottom_inner - v
                        if top_inner <= row <= bottom_inner and row < height - 1:
                            try:
                                self.stdscr.addstr(
                                    row,
                                    viz_x + 1 + col,
                                    "█",
                                    curses.color_pair(2),
                                )
                            except curses.error:
                                pass

            self.stdscr.attroff(curses.color_pair(2))
        except Exception:
            # If anything goes wrong, don't crash the UI
            pass

    def draw_lyrics(self):
        """Draw lyrics pane"""
        if not self.playing or not self.show_lyrics:
            return

        height, width = self.stdscr.getmaxyx()
        lyrics_x = width // 2 + 2
        lyrics_y = 24
        lyrics_width = width // 2 - 4
        lyrics_height = height - lyrics_y - 8

        if lyrics_width <= 4 or lyrics_y >= height:
            return

        try:
            self.stdscr.attron(curses.color_pair(3))
            if lyrics_y < height:
                self.stdscr.addstr(
                    lyrics_y,
                    lyrics_x,
                    "╔" + "═" * (lyrics_width - 2) + "╗",
                )
            for i in range(1, lyrics_height - 1):
                if lyrics_y + i < height - 6:
                    self.stdscr.addstr(
                        lyrics_y + i,
                        lyrics_x,
                        "║" + " " * (lyrics_width - 2) + "║",
                    )
            if lyrics_y + lyrics_height - 1 < height:
                self.stdscr.addstr(
                    lyrics_y + lyrics_height - 1,
                    lyrics_x,
                    "╚" + "═" * (lyrics_width - 2) + "╝",
                )

            title = ""
            if lyrics_y < height:
                self.stdscr.addstr(
                    lyrics_y,
                    lyrics_x + max(0, (lyrics_width - len(title)) // 2),
                    title,
                    curses.color_pair(3) | curses.A_BOLD,
                )
            self.stdscr.attroff(curses.color_pair(3))

            if self.lyrics:
                start_line = max(0, self.current_lyric_line - 3)
                for i, line in enumerate(
                    self.lyrics[start_line : start_line + lyrics_height - 2]
                ):
                    if lyrics_y + 1 + i < height - 6:
                        is_current = start_line + i == self.current_lyric_line
                        color = (
                            curses.color_pair(3) | curses.A_BOLD
                            if is_current
                            else curses.color_pair(6)
                        )
                        text = line[: max(0, lyrics_width - 3)].center(
                            max(0, lyrics_width - 3)
                        )
                        self.stdscr.addstr(
                            lyrics_y + 1 + i, lyrics_x + 1, text, color
                        )
            else:
                msg = "No lyrics available"
                if lyrics_y + 2 < height:
                    self.stdscr.addstr(
                        lyrics_y + 2,
                        lyrics_x + max(0, (lyrics_width - len(msg)) // 2),
                        msg,
                        curses.color_pair(6),
                    )
        except Exception:
            pass

    def format_time(self, seconds: Optional[float]) -> str:
        """Format seconds -> MM:SS"""
        if seconds is None:
            return "--:--"
        try:
            seconds = int(seconds)
            mins = seconds // 60
            secs = seconds % 60
            return f"{mins:02d}:{secs:02d}"
        except Exception:
            return "--:--"

    def draw_now_playing(self):
        """Draw now playing information"""
        height, width = self.stdscr.getmaxyx()

        if self.current_video:
            y_pos = height - 5
            if y_pos < 0:
                return
            self.stdscr.attron(curses.color_pair(1) | curses.A_BOLD)
            self.stdscr.addstr(y_pos, 2, "♪ Now Playing:")
            self.stdscr.attroff(curses.color_pair(1) | curses.A_BOLD)

            title = self.current_video.get("title", "Unknown")[
                : max(0, width // 2 - 6)
            ]
            if y_pos + 1 < height:
                self.stdscr.addstr(
                    y_pos + 1, 2, title, curses.color_pair(3)
                )

            status = "Playing" if self.playing else "Stopped"
            if y_pos + 2 < height:
                time_str = ""
                if self.playing:
                    cur = self.format_time(self.playback_time)
                    if self.playback_duration:
                        dur = self.format_time(self.playback_duration)
                        time_str = f" | {cur} / {dur}"
                    else:
                        time_str = f" | {cur}"
                self.stdscr.addstr(
                    y_pos + 2,
                    2,
                    f"Status: {status}{time_str}",
                    curses.color_pair(2),
                )

    def draw_help(self):
        """Draw help/keybindings"""
        height, width = self.stdscr.getmaxyx()
        help_text = [
            "s:Search | Enter:Play | Space:Stop | q:Quit | m:Mode | l:Lyrics",
            "↑↓:Navigate | n:Next | p:Previous | Mouse:Click to search/play",
        ]

        y_pos = height - 2
        for i, text in enumerate(help_text):
            if y_pos + i < height:
                self.stdscr.addstr(
                    y_pos + i, 2, text[: max(0, width - 4)], curses.color_pair(4)
                )

    def search_youtube_fast(self, query: str):
        """Primary search using yt-dlp JSON"""
        try:
            cmd = [
                "yt-dlp",
                "--flat-playlist",
                "--dump-single-json",
                "--default-search",
                "ytsearch10",
                "--no-warnings",
                "--socket-timeout",
                "10",
                query,
            ]

            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=25
            )

            if result.returncode != 0:
                return self.search_youtube_fallback(query)

            results = []
            try:
                data = json.loads(result.stdout)
                entries = data.get("entries", [])

                for entry in entries[:10]:
                    duration_seconds = entry.get("duration", 0)
                    if duration_seconds:
                        mins = int(duration_seconds // 60)
                        secs = int(duration_seconds % 60)
                        duration_str = f"{mins}:{secs:02d}"
                    else:
                        duration_str = "Live"

                    video_id = entry.get("id", "")
                    video_data = {
                        "title": entry.get("title", "Unknown"),
                        "id": video_id,
                        "url": entry.get(
                            "url", f"https://youtube.com/watch?v={video_id}"
                        ),
                        "duration": duration_str,
                        "channel": entry.get(
                            "uploader", entry.get("channel", "Unknown")
                        ),
                        "thumbnail": entry.get(
                            "thumbnail",
                            f"https://i.ytimg.com/vi/{video_id}/default.jpg",
                        ),
                    }
                    results.append(video_data)

                    if video_data["thumbnail"]:
                        threading.Thread(
                            target=self.download_thumbnail,
                            args=(video_data["id"], video_data["thumbnail"]),
                            daemon=True,
                        ).start()

            except json.JSONDecodeError:
                return self.search_youtube_fallback(query)

            return results
        except subprocess.TimeoutExpired:
            return self.search_youtube_fallback(query)
        except Exception:
            return []

    def search_youtube_fallback(self, query: str):
        """Fallback text-mode search using yt-dlp"""
        try:
            cmd = [
                "yt-dlp",
                "--get-id",
                "--get-title",
                "--get-duration",
                "--default-search",
                "ytsearch10",
                "--no-warnings",
                query,
            ]

            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=20
            )

            lines = result.stdout.strip().split("\n")
            results = []

            for i in range(0, len(lines) - 2, 3):
                try:
                    title = lines[i]
                    duration = lines[i + 1]
                    video_id = lines[i + 2]

                    video_data = {
                        "title": title,
                        "id": video_id,
                        "url": f"https://youtube.com/watch?v={video_id}",
                        "duration": duration,
                        "channel": "YouTube",
                        "thumbnail": f"https://i.ytimg.com/vi/{video_id}/default.jpg",
                    }
                    results.append(video_data)

                    threading.Thread(
                        target=self.download_thumbnail,
                        args=(video_id, video_data["thumbnail"]),
                        daemon=True,
                    ).start()
                except Exception:
                    continue

            return results
        except Exception:
            return []

    def fetch_lyrics(self, title: str):
        """Fetch lyrics (plain or synced) from lrclib"""
        self.lyrics = []
        self.current_lyric_line = 0
        self.synced_lyrics = []

        try:
            query = urllib.parse.quote_plus(title)
            url = f"https://lrclib.net/api/search?q={query}"

            with urllib.request.urlopen(url, timeout=8) as resp:
                data = resp.read().decode("utf-8", errors="ignore")
                results = json.loads(data)

            if not results:
                self.lyrics = ["No lyrics found for this track."]
                return

            track = results[0]

            plain = track.get("plainLyrics") or ""
            synced = track.get("syncedLyrics") or ""

            # Prefer synced lyrics if available
            if synced:
                synced_list: List[tuple] = []

                for raw_line in synced.splitlines():
                    if not raw_line.strip():
                        continue

                    timestamps = re.findall(r"\[([0-9:.]+)\]", raw_line)
                    text = re.sub(r"\[[0-9:.]+\]", "", raw_line).strip()
                    if not text:
                        continue

                    for ts in timestamps:
                        try:
                            if "." in ts:
                                m, s = ts.split(":")
                                sec = float(m) * 60 + float(s)
                            else:
                                m, s = ts.split(":")
                                sec = int(m) * 60 + int(s)
                            synced_list.append((sec, text))
                        except Exception:
                            continue

                synced_list.sort(key=lambda x: x[0])

                if synced_list:
                    self.synced_lyrics = synced_list
                    self.lyrics = [line for _, line in synced_list]
                    self.current_lyric_line = 0
                    return

            # Fallback to plain lyrics
            if plain:
                lines = [
                    ln.strip() for ln in plain.splitlines() if ln.strip()
                ]
                if lines:
                    self.lyrics = lines
                    self.current_lyric_line = 0
                    return

            self.lyrics = ["Lyrics not available for this track."]
            self.current_lyric_line = 0

        except Exception:
            self.lyrics = ["Lyrics unavailable (network error or not found)."]
            self.current_lyric_line = 0

    def update_cava_output(self):
        """Spawn cava and generate visualizer output"""
        if not self.has_cava:
            return

        try:
            config_dir = os.path.join(self.thumb_dir, "cava_config")
            os.makedirs(config_dir, exist_ok=True)
            config_file = os.path.join(config_dir, "config")

            config_text = """
[general]
bars = 40

[input]
source = auto

[output]
method = raw
raw_target = /dev/stdout
data_format = ascii
"""

            with open(config_file, "w") as f:
                f.write(config_text)

            cava_cmd = ["cava", "-p", config_file]

            process = subprocess.Popen(
                cava_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                bufsize=1,
            )

            blocks = ["▁", "▂", "▃", "▄", "▅", "▆", "▇", "█"]

            while self.playing and process.poll() is None:
                line = process.stdout.readline()
                if not line:
                    break

                line = line.strip()
                if not line:
                    continue

                try:
                    raw_vals = [int(v) for v in line.split(";") if v.strip()]
                    if not raw_vals:
                        continue

                    max_val = max(raw_vals) or 1

                    vals = []
                    for v in raw_vals:
                        level = int(v * 7 / max_val)
                        level = max(0, min(7, level))
                        vals.append(level)

                    viz_line = "".join(blocks[level] for level in vals)
                    # store a single line; visualizer will stack it
                    self.cava_output = [viz_line]

                except Exception:
                    continue

                time.sleep(0.03)

            try:
                process.terminate()
            except Exception:
                pass

        except Exception:
            self.has_cava = False
            self.cava_output = []

    def build_mpv_socket_path(self) -> str:
        """Path for mpv IPC socket"""
        return os.path.join(self.thumb_dir, "mpv_socket")

    def query_mpv_property(self, prop: str):
        """Query a property from mpv via IPC."""
        if not self.mpv_socket_path or not os.path.exists(self.mpv_socket_path):
            return None

        try:
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.settimeout(0.5)
            s.connect(self.mpv_socket_path)
            cmd = (
                json.dumps({"command": ["get_property", prop]}).encode("utf-8")
                + b"\n"
            )
            s.sendall(cmd)
            data = b""
            while True:
                chunk = s.recv(4096)
                if not chunk:
                    break
                data += chunk
                if b"\n" in chunk:
                    break
            s.close()

            for line in data.splitlines():
                try:
                    resp = json.loads(line.decode("utf-8"))
                    if resp.get("error") == "success":
                        return resp.get("data")
                except Exception:
                    continue
        except Exception:
            return None

        return None

    def monitor_mpv(self):
        """Background thread to keep track of playback time and duration."""
        while (
            self.playing
            and self.mpv_process is not None
            and self.mpv_process.poll() is None
        ):
            pos = self.query_mpv_property("time-pos")
            dur = self.query_mpv_property("duration")
            if pos is not None:
                self.playback_time = pos
            if dur is not None:
                self.playback_duration = dur
            time.sleep(0.3)

    def play_video(self, video: Dict):
        """Start mpv playback and all background helpers"""
        if self.mpv_process:
            self.stop_playback()

        try:
            self.mpv_socket_path = self.build_mpv_socket_path()
            self.playback_time = 0.0
            self.playback_duration = 0.0

            cmd = [
                "mpv",
                "--volume=100",
                "--really-quiet",
                f"--input-ipc-server={self.mpv_socket_path}",
            ]
            if self.audio_only:
                cmd.append("--no-video")
            cmd.append(video["url"])

            self.mpv_process = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

            self.playing = True
            self.current_video = video

            # Fetch lyrics (may populate synced_lyrics)
            self.fetch_lyrics(video["title"])

            # Start mpv monitor thread
            threading.Thread(
                target=self.monitor_mpv, daemon=True
            ).start()

            # Start Cava visualizer if available
            if self.has_cava:
                self.cava_thread = threading.Thread(
                    target=self.update_cava_output, daemon=True
                )
                self.cava_thread.start()

            # Start synced lyrics updater (only if we have synced lyrics)
            if self.synced_lyrics:
                threading.Thread(
                    target=self.animate_lyrics, daemon=True
                ).start()

        except Exception:
            self.playing = False

    def animate_lyrics(self):
        """Advance lyrics based on actual mpv playback time via IPC."""
        if not self.synced_lyrics:
            return

        while (
            self.playing
            and self.mpv_process is not None
            and self.mpv_process.poll() is None
        ):
            t = self.playback_time
            idx = 0
            for i, (ts, _) in enumerate(self.synced_lyrics):
                if ts <= t:
                    idx = i
                else:
                    break
            self.current_lyric_line = idx
            time.sleep(0.1)

    def stop_playback(self):
        """Stop mpv + reset visualizer, lyrics, IPC"""
        if self.mpv_process:
            self.mpv_process.terminate()
            try:
                self.mpv_process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.mpv_process.kill()
            self.mpv_process = None

        self.playing = False
        self.cava_output = []
        self.playback_time = 0.0
        self.playback_duration = 0.0
        self.synced_lyrics = []

        if self.mpv_socket_path:
            try:
                if os.path.exists(self.mpv_socket_path):
                    os.remove(self.mpv_socket_path)
            except Exception:
                pass
            self.mpv_socket_path = None

    def handle_mouse(self, mouse_event):
        try:
            _, x, y, _, bstate = curses.getmouse()
            height, width = self.stdscr.getmaxyx()

            # Click in search box
            if y == 3 and 11 <= x < width - 2:
                self.search_mode = True
                self.search_input = self.search_query
                curses.curs_set(1)
                return

            # Click on results
            if self.results and y >= 7:
                available_height = height - 8
                results_per_page = max(1, available_height // 8)
                start_idx = max(
                    0, self.selected_index - results_per_page + 1
                )

                relative_y = y - 7
                result_idx = start_idx + (relative_y // 8)

                if 0 <= result_idx < len(self.results):
                    if result_idx == self.selected_index or (
                        bstate & curses.BUTTON1_CLICKED
                    ):
                        self.selected_index = result_idx
                        self.play_video(self.results[result_idx])

        except curses.error:
            pass

    def handle_input(self, key):
        height, width = self.stdscr.getmaxyx()

        if key == curses.KEY_MOUSE:
            self.handle_mouse(key)
            return True

        if self.search_mode:
            if key == 27:
                self.search_mode = False
                curses.curs_set(0)
                return True
            elif key in (ord("\n"), curses.KEY_ENTER, 10):
                if self.search_input.strip():
                    self.search_query = self.search_input.strip()
                    self.search_mode = False
                    curses.curs_set(0)

                    self.stdscr.addstr(
                        5,
                        2,
                        "Searching..." + " " * 50,
                        curses.color_pair(3),
                    )
                    self.stdscr.refresh()

                    self.results = self.search_youtube_fast(
                        self.search_query
                    )
                    self.selected_index = 0
                return True
            elif key in (curses.KEY_BACKSPACE, 127, 8):
                if self.search_input:
                    self.search_input = self.search_input[:-1]
                return True
            elif 32 <= key <= 126:
                self.search_input += chr(key)
                return True
            return True

        if key == ord("q"):
            return False
        elif key == ord("s"):
            self.search_mode = True
            self.search_input = self.search_query
            curses.curs_set(1)
        elif key == ord("l"):
            self.show_lyrics = not self.show_lyrics
        elif key == curses.KEY_UP and self.results:
            self.selected_index = max(0, self.selected_index - 1)
        elif key == curses.KEY_DOWN and self.results:
            self.selected_index = min(
                len(self.results) - 1, self.selected_index + 1
            )
        elif key in (ord("\n"), curses.KEY_ENTER, 10):
            if self.results and 0 <= self.selected_index < len(
                self.results
            ):
                self.play_video(self.results[self.selected_index])
        elif key == ord(" "):
            self.stop_playback()
        elif key == ord("m"):
            self.audio_only = not self.audio_only
        elif key == ord("n") and self.results:
            self.selected_index = (self.selected_index + 1) % len(
                self.results
            )
            if self.playing:
                self.play_video(self.results[self.selected_index])
        elif key == ord("p") and self.results:
            self.selected_index = (self.selected_index - 1) % len(
                self.results
            )
            if self.playing:
                self.play_video(self.results[self.selected_index])

        return True

    def run(self):
        try:
            while True:
                # auto-stop when mpv finishes naturally
                if (
                    self.mpv_process is not None
                    and self.mpv_process.poll() is not None
                    and self.playing
                ):
                    self.stop_playback()

                self.stdscr.clear()

                self.draw_header()
                self.draw_search_box()
                self.draw_results()

                if self.playing:
                    self.draw_cava_visualizer()
                    self.draw_lyrics()

                self.draw_now_playing()
                self.draw_help()

                self.stdscr.refresh()

                key = self.stdscr.getch()
                if key != -1:
                    if not self.handle_input(key):
                        break

                time.sleep(0.01)
        finally:
            self.stop_playback()


def main():
    missing = []

    try:
        subprocess.run(
            ["yt-dlp", "--version"],
            capture_output=True,
            check=True,
            timeout=2,
        )
    except Exception:
        missing.append("yt-dlp")

    try:
        subprocess.run(
            ["mpv", "--version"],
            capture_output=True,
            check=True,
            timeout=2,
        )
    except Exception:
        missing.append("mpv")

    if missing:
        print("Error: Required dependencies not found!")
        print("\nMissing:", ", ".join(missing))
        print("\nInstall on Arch:")
        print("  sudo pacman -S mpv yt-dlp cava jp2a")
        sys.exit(1)

    try:
        subprocess.run(
            ["cava", "--version"], capture_output=True, timeout=1
        )
        print("✓ Cava detected")
    except Exception:
        print("ℹ Cava not found - install with: sudo pacman -S cava")

    try:
        subprocess.run(
            ["jp2a", "--version"], capture_output=True, timeout=1
        )
        print("✓ jp2a detected - thumbnails enabled")
    except Exception:
        print("ℹ jp2a not found (optional) - install with: sudo pacman -S jp2a")

    print("\nStarting YAAP...")
    time.sleep(1)

    curses.wrapper(lambda stdscr: YouTubeTUI(stdscr).run())


if __name__ == "__main__":
    main()

"""MusicLens — Windows desktop music recognition overlay."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import queue
import threading
import time
import tkinter as tk
import webbrowser
from tkinter import ttk
from collections.abc import Callable
from pathlib import Path
from PIL import Image, ImageDraw
import pystray

import numpy as np
import pyaudiowpatch as pyaudio
from shazamio import Shazam

from core import extract_song, make_wav, song_key, suppress_voice

LOG = logging.getLogger("musiclens")

CAPTURE_SECONDS = 12
CHUNK_FRAMES = 1024
MIN_RMS = 0.0025  # Avoid querying Shazam when system audio is effectively silent.
ACTIVITY_SECONDS = 0.25
ACTIVITY_WINDOWS = 2  # Ignore very short system sounds and clicks.
POPUP_MS = 9000
COOLDOWN_ON_RATE_LIMIT_SECONDS = 600
COLOR_BG = "#141923"
COLOR_INNER = "#202838"
COLOR_TEXT = "#F1F5F9"
COLOR_MUTED = "#A7B0C0"
COLOR_ACCENT = "#82E0BB"


async def recognize_audio(wav_bytes: bytes) -> dict | None:
    # ShazamIO's requests are unofficial and can be limited or changed remotely.
    shazam = Shazam()
    result = await asyncio.wait_for(shazam.recognize(wav_bytes), timeout=22)
    return extract_song(result)


def capture_output_audio(stop_event: threading.Event) -> tuple[bytes, float]:
    """Record only system output (WASAPI loopback), not microphone audio."""
    frames = []
    with pyaudio.PyAudio() as audio:
        device = audio.get_default_wasapi_loopback()
        channels = max(1, int(device["maxInputChannels"]))
        rate = int(device["defaultSampleRate"])
        with audio.open(
            format=pyaudio.paInt16,
            channels=channels,
            rate=rate,
            input=True,
            input_device_index=int(device["index"]),
            frames_per_buffer=CHUNK_FRAMES,
        ) as stream:
            remaining = rate * CAPTURE_SECONDS
            while remaining > 0 and not stop_event.is_set():
                count = min(CHUNK_FRAMES, remaining)
                frames.append(stream.read(count, exception_on_overflow=False))
                remaining -= count
    if stop_event.is_set():
        return b"", 0.0
    pcm = b"".join(frames)
    if not pcm:
        return b"", 0.0
    # Work on a small strided view to minimize processing cost.
    samples = np.frombuffer(pcm, dtype="<i2")[::16].astype(np.float32)
    rms = float(np.sqrt(np.mean(samples * samples))) / 32768.0 if samples.size else 0.0
    return make_wav(pcm, rate=rate, channels=channels), rms


def wait_for_output_activity(
    stop_event: threading.Event,
    state: Callable[[], tuple[bool, bool, float]],
) -> bool:
    """Listen cheaply for sustained system output; return when a search is due."""
    with pyaudio.PyAudio() as audio:
        device = audio.get_default_wasapi_loopback()
        channels = max(1, int(device["maxInputChannels"]))
        rate = int(device["defaultSampleRate"])
        with audio.open(
            format=pyaudio.paInt16,
            channels=channels,
            rate=rate,
            input=True,
            input_device_index=int(device["index"]),
            frames_per_buffer=CHUNK_FRAMES,
        ) as stream:
            audible_windows = 0
            frames = max(CHUNK_FRAMES, int(rate * ACTIVITY_SECONDS))
            while not stop_event.is_set():
                manual, enabled, due = state()
                if manual or not enabled:
                    return False
                pcm = stream.read(frames, exception_on_overflow=False)
                samples = np.frombuffer(pcm, dtype="<i2")[::16].astype(np.float32)
                rms = float(np.sqrt(np.mean(samples * samples))) / 32768.0 if samples.size else 0.0
                audible_windows = audible_windows + 1 if rms >= MIN_RMS else 0
                if audible_windows >= ACTIVITY_WINDOWS and time.monotonic() >= due:
                    return True
    return False


class MusicLens:
    def __init__(self, activation_requested: Callable[[], bool] | None = None) -> None:
        self.activation_requested = activation_requested or (lambda: False)
        self.root = tk.Tk()
        self.root.withdraw()
        self.root.title("MusicLens")
        self.root.configure(bg=COLOR_BG)
        self.root.resizable(False, False)
        self.root.report_callback_exception = self._report_ui_error
        self._bar_size = (326, 137)
        self._move_to_right_edge()

        self.events: queue.Queue[tuple[str, object]] = queue.Queue()
        self.stop_event = threading.Event()
        self.wakeup = threading.Event()
        self.state_lock = threading.Lock()
        self.enabled = True
        self.manual_requested = False
        self.interval = 15
        self.separation_mode = "off"
        self.next_due = 0.0
        self.rate_limit_until = 0.0
        self.last_song_key = ""
        self.last_song_date = ""
        self.history_path = Path(os.getenv("LOCALAPPDATA", str(Path.home()))) / "MusicLens" / "recognition-history.json"
        self.song_alert_dates = self._load_song_alert_dates()
        self.popup: tk.Toplevel | None = None
        self.popup_after_id: str | None = None
        self.closing = False

        self._build_ui()
        self.root.withdraw()
        self.tray = self._create_tray()
        self.tray.run_detached()
        self.root.after(150, self._drain_events)
        self.root.after(250, self._poll_activation)
        self.worker = threading.Thread(target=self._worker, name="musiclens-audio")
        self.worker.start()
        self.root.protocol("WM_DELETE_WINDOW", self._hide_settings)

    def _load_song_alert_dates(self) -> dict[str, str]:
        try:
            data = json.loads(self.history_path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError, TypeError):
            return {}

    def _save_song_alert_date(self, key: str, today: str) -> None:
        self.song_alert_dates[key] = today
        try:
            self.history_path.parent.mkdir(parents=True, exist_ok=True)
            self.history_path.write_text(
                json.dumps(self.song_alert_dates, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except OSError:
            LOG.warning("Could not save recognition history", exc_info=True)

    def _create_tray(self) -> pystray.Icon:
        def interval_action(seconds: int):
            return lambda icon, item: self.events.put(("interval", seconds))

        def interval_checked(seconds: int):
            return lambda item: self.interval == seconds

        image = Image.new("RGBA", (64, 64), (20, 25, 35, 255))
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle((1, 1, 62, 62), radius=13, outline=(130, 224, 187), width=3)
        draw.line([(35, 11), (35, 43)], fill=(130, 224, 187), width=6)
        draw.line([(35, 12), (49, 9), (49, 37)], fill=(130, 224, 187), width=6)
        draw.ellipse((19, 38, 37, 52), fill=(130, 224, 187))
        draw.ellipse((34, 32, 51, 46), fill=(130, 224, 187))
        menu = pystray.Menu(
            pystray.MenuItem("MusicLens 상태/설정", self._tray_settings, default=True),
            pystray.MenuItem("자동 인식 켜기", self._tray_toggle, checked=lambda item: self.enabled),
            pystray.MenuItem("지금 검색", self._tray_search),
            pystray.MenuItem("설정 (인식 주기)", pystray.Menu(
                *(pystray.MenuItem(f"{seconds}초", interval_action(seconds),
                                      checked=interval_checked(seconds))
                  for seconds in (15, 30, 60, 120))
            )),
            pystray.MenuItem("종료", self._tray_quit),
        )
        return pystray.Icon("MusicLens", image, "MusicLens", menu)

    def _tray_settings(self, icon, item) -> None:
        self.events.put(("settings", None))

    def _tray_toggle(self, icon, item) -> None:
        self.events.put(("toggle", None))

    def _tray_search(self, icon, item) -> None:
        self.events.put(("search", None))

    def _tray_quit(self, icon, item) -> None:
        self.events.put(("quit", None))

    def _show_settings(self) -> None:
        self.root.deiconify()
        self.root.state("normal")
        self.root.lift()
        self.root.focus_force()

    def _poll_activation(self) -> None:
        if self.activation_requested():
            self._show_settings()
        if not self.closing:
            self.root.after(250, self._poll_activation)

    def _hide_settings(self) -> None:
        self.root.withdraw()

    def _move_to_right_edge(self) -> None:
        width, height = self._bar_size
        screen_w = self.root.winfo_screenwidth()
        self.root.geometry(f"{width}x{height}+{max(0, screen_w - width - 20)}+48")

    def _build_ui(self) -> None:
        bar = tk.Frame(self.root, bg=COLOR_BG, highlightbackground="#3A4558", highlightthickness=1)
        bar.pack(fill="both", expand=True)
        top = tk.Frame(bar, bg=COLOR_BG)
        top.pack(fill="x", padx=12, pady=(9, 2))
        tk.Label(top, text="♫  MusicLens", bg=COLOR_BG, fg=COLOR_TEXT,
                 font=("Malgun Gothic", 11, "bold")).pack(side="left")
        tk.Button(top, text="×", command=self._hide_settings, bg=COLOR_BG, fg=COLOR_MUTED,
                  activebackground=COLOR_INNER, activeforeground=COLOR_TEXT,
                  borderwidth=0, font=("Segoe UI", 12), cursor="hand2").pack(side="right")

        middle = tk.Frame(bar, bg=COLOR_BG)
        middle.pack(fill="x", padx=10, pady=(2, 3))
        self.toggle = tk.Button(middle, text="자동 인식 끄기", command=self._toggle,
                                bg=COLOR_INNER, fg=COLOR_ACCENT, activebackground="#28354A",
                                activeforeground=COLOR_ACCENT, relief="flat", padx=7, pady=4,
                                font=("Malgun Gothic", 9), cursor="hand2")
        self.toggle.pack(side="left")
        self.once = tk.Button(middle, text="지금 찾기", command=self._recognize_once,
                              bg=COLOR_INNER, fg=COLOR_TEXT, activebackground="#28354A",
                              activeforeground=COLOR_TEXT, relief="flat", padx=7, pady=4,
                              font=("Malgun Gothic", 9), cursor="hand2")
        self.once.pack(side="left", padx=(6, 4))
        self.interval_choice = ttk.Combobox(middle, values=["15초", "30초", "60초", "120초"],
                                             state="readonly", width=5, font=("Malgun Gothic", 9))
        self.interval_choice.set("15초")
        self.interval_choice.pack(side="right")
        self.interval_choice.bind("<<ComboboxSelected>>", self._update_interval)
        self.separation_choice = ttk.Combobox(
            middle, values=["분리 안 함", "빠른 음성 억제"], state="readonly", width=10,
            font=("Malgun Gothic", 9))
        self.separation_choice.set("분리 안 함")
        self.separation_choice.pack(side="right", padx=(4, 0))
        self.separation_choice.bind("<<ComboboxSelected>>", self._update_separation)

        self.status_var = tk.StringVar(value="자동 인식 중 · 재생 중인 소리만 인식")
        tk.Label(bar, textvariable=self.status_var, anchor="w", bg=COLOR_BG,
                 fg=COLOR_MUTED, font=("Malgun Gothic", 8)).pack(fill="x", padx=12, pady=(1, 1))
        self.last_result_var = tk.StringVar(value="최근 검색: 아직 없음")
        tk.Label(bar, textvariable=self.last_result_var, anchor="w", bg=COLOR_BG,
                 fg=COLOR_MUTED, font=("Malgun Gothic", 8)).pack(fill="x", padx=12, pady=(0, 6))

        # Allow dragging this control strip without intercepting browser clicks elsewhere.
        for widget in (bar, top):
            widget.bind("<Button-1>", self._begin_drag)
            widget.bind("<B1-Motion>", self._do_drag)

    def _begin_drag(self, event: tk.Event) -> None:
        self.drag_delta = (event.x_root - self.root.winfo_x(), event.y_root - self.root.winfo_y())

    def _do_drag(self, event: tk.Event) -> None:
        offset_x, offset_y = self.drag_delta
        self.root.geometry(f"+{event.x_root - offset_x}+{event.y_root - offset_y}")

    def _set_status(self, message: str) -> None:
        self.status_var.set(message)

    def _report_ui_error(self, exception, value, tb) -> None:
        LOG.error("UI callback failed", exc_info=(exception, value, tb))
        self.status_var.set(f"UI 오류: {exception.__name__} · 로그 확인")

    def _update_interval(self, event: tk.Event | None = None) -> None:
        self._set_interval(int(self.interval_choice.get().replace("초", "")))

    def _update_separation(self, event: tk.Event | None = None) -> None:
        self.separation_mode = "voice_suppress" if self.separation_choice.get() == "빠른 음성 억제" else "off"
        self._set_status("빠른 음성 억제 사용" if self.separation_mode == "voice_suppress" else "음성 억제 사용 안 함")

    def _set_interval(self, interval: int) -> None:
        with self.state_lock:
            self.interval = interval
            self.next_due = min(self.next_due, time.monotonic() + interval)
        self.interval_choice.set(f"{interval}초")
        self.tray.update_menu()
        self.wakeup.set()

    def _toggle(self) -> None:
        with self.state_lock:
            self.enabled = not self.enabled
            active = self.enabled
            self.next_due = time.monotonic() if active else float("inf")
        self.toggle.config(text="자동 인식 끄기" if active else "자동 인식 켜기")
        self.tray.update_menu()
        self._set_status("자동 인식 활성화" if active else "자동 인식 중지")
        self.wakeup.set()

    def _recognize_once(self) -> None:
        with self.state_lock:
            self.manual_requested = True
        self._set_status("수동 인식 예약됨")
        self.wakeup.set()

    def _worker(self) -> None:
        def activity_state() -> tuple[bool, bool, float]:
            with self.state_lock:
                return self.manual_requested, self.enabled, max(self.next_due, self.rate_limit_until)

        while not self.stop_event.is_set():
            with self.state_lock:
                forced = self.manual_requested
                enabled = self.enabled
            if not forced and not enabled:
                self.wakeup.wait(timeout=1.0)
                self.wakeup.clear()
                continue
            if not forced:
                try:
                    self.events.put(("status", "시스템 소리 대기 중…"))
                    if not wait_for_output_activity(self.stop_event, activity_state):
                        continue
                except Exception:
                    LOG.exception("System audio activity monitor failed")
                    self.events.put(("status", "소리 감지 오류 · 잠시 후 재시도"))
                    self.wakeup.wait(timeout=3.0)
                    self.wakeup.clear()
                    continue
            now = time.monotonic()
            with self.state_lock:
                forced = self.manual_requested
                if not forced and (not self.enabled or now < max(self.next_due, self.rate_limit_until)):
                    continue
                cooldown = self.rate_limit_until
                self.manual_requested = False
                self.next_due = now + self.interval
            if now < cooldown:
                self.events.put(("status", "요청 제한 보호 중 · 잠시 후 자동 재개"))
                continue
            try:
                self.events.put(("status", "소리 듣는 중… (12초)"))
                wav_bytes, rms = capture_output_audio(self.stop_event)
                if self.stop_event.is_set():
                    return
                if rms < MIN_RMS:
                    self.events.put(("status", "소리가 없어 검색 생략"))
                    continue
                if self.separation_mode == "voice_suppress":
                    self.events.put(("status", "음성과 음악 분리 중…"))
                    wav_bytes = suppress_voice(wav_bytes)
                self.events.put(("status", "음악 검색 중…"))
                song = asyncio.run(recognize_audio(wav_bytes))
                if song is None:
                    LOG.info("Recognition result: no match")
                    self.events.put(("no_match", None))
                else:
                    self.events.put(("song", (song, forced)))
            except Exception as exc:  # A failing network or audio driver should not terminate the app.
                LOG.exception("Audio capture or music identification failed")
                status = getattr(exc, "status", None)
                msg = str(exc)
                if status == 429 or "429" in msg or "rate limit" in msg.lower():
                    with self.state_lock:
                        self.rate_limit_until = time.monotonic() + COOLDOWN_ON_RATE_LIMIT_SECONDS
                        self.next_due = max(self.next_due, self.rate_limit_until)
                    self.events.put(("status", "Shazam 호출 제한 · 10분 쉬는 중"))
                elif isinstance(exc, (TimeoutError, asyncio.TimeoutError)):
                    self.events.put(("status", "검색 시간 초과 · 다음 주기에 재시도"))
                else:
                    self.events.put(("status", f"검색 오류: {type(exc).__name__} · 다음 주기에 재시도"))
            finally:
                with self.state_lock:
                    # Space the next request from completion to limit API usage.
                    self.next_due = max(self.next_due, time.monotonic() + self.interval)

    def _drain_events(self) -> None:
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "status":
                    self._set_status(str(payload))
                elif kind == "song":
                    song, forced = payload
                    assert isinstance(song, dict)
                    key = song_key(song)
                    checked_at = time.strftime("%H:%M:%S")
                    today = time.strftime("%Y-%m-%d")
                    if self.song_alert_dates.get(key) != today:
                        self.last_song_key = key
                        self.last_song_date = today
                        self._save_song_alert_date(key, today)
                        self._show_popup(song)
                        self.last_result_var.set(f"{checked_at} {song['title'][:15]} · 새 곡 알림")
                        LOG.info("Recognition result: new song; notification shown")
                    else:
                        self.last_result_var.set(f"{checked_at} {song['title'][:15]} · 같은 곡 알림 생략")
                        LOG.info("Recognition result: same song; notification suppressed")
                    self._set_status(f"인식됨: {song['title'][:25]}")
                elif kind == "no_match":
                    self.last_result_var.set(f"{time.strftime('%H:%M:%S')} 검색: 일치하는 곡 없음")
                    self._set_status("일치하는 곡 없음 · 계속 탐색")
                elif kind == "toggle":
                    self._toggle()
                elif kind == "search":
                    self._recognize_once()
                elif kind == "settings":
                    self._show_settings()
                elif kind == "interval":
                    self._set_interval(int(payload))
                elif kind == "quit":
                    self._close()
                    return
        except queue.Empty:
            pass
        if not self.stop_event.is_set():
            self.root.after(150, self._drain_events)

    def _show_popup(self, song: dict) -> None:
        if self.popup is not None and self.popup.winfo_exists():
            self.popup.destroy()
        pop = tk.Toplevel(self.root)
        self.popup = pop
        pop.overrideredirect(True)
        pop.attributes("-topmost", True)
        pop.configure(bg=COLOR_INNER)
        width, height = 326, 106
        x = max(0, self.root.winfo_screenwidth() - width - 20)
        y = max(0, self.root.winfo_screenheight() - height - 72)
        pop.geometry(f"{width}x{height}+{x}+{y}")
        frame = tk.Frame(pop, bg=COLOR_INNER, highlightbackground="#45566F", highlightthickness=1)
        frame.pack(fill="both", expand=True)
        heading = tk.Frame(frame, bg=COLOR_INNER)
        heading.pack(fill="x", padx=12, pady=(9, 0))
        tk.Label(heading, text="♫  음악을 찾았어요", fg=COLOR_ACCENT, bg=COLOR_INNER,
                 font=("Malgun Gothic", 9, "bold")).pack(side="left")
        tk.Button(heading, text="×", command=pop.destroy, bg=COLOR_INNER, fg=COLOR_MUTED,
                  borderwidth=0, font=("Segoe UI", 11)).pack(side="right")
        title = song.get("title", "")
        artist = song.get("artist", "")
        tk.Label(frame, text=(title if len(title) <= 42 else title[:39] + "…"), fg=COLOR_TEXT, bg=COLOR_INNER, anchor="w",
                 font=("Malgun Gothic", 10, "bold")).pack(fill="x", padx=13)
        bottom = tk.Frame(frame, bg=COLOR_INNER)
        bottom.pack(fill="x", padx=13)
        tk.Label(bottom, text=(artist if len(artist) <= 36 else artist[:33] + "…"), fg=COLOR_MUTED, bg=COLOR_INNER,
                 font=("Malgun Gothic", 9)).pack(side="left")
        if song.get("url"):
            tk.Button(bottom, text="Shazam ↗", command=lambda: webbrowser.open(song["url"]),
                      fg=COLOR_ACCENT, bg=COLOR_INNER, borderwidth=0,
                      cursor="hand2", font=("Malgun Gothic", 8)).pack(side="right")
        pop.after(POPUP_MS, lambda: pop.destroy() if pop.winfo_exists() else None)

    def _close(self) -> None:
        if self.closing:
            return
        self.closing = True
        self.stop_event.set()
        self.wakeup.set()
        self.tray.stop()
        if self.popup and self.popup.winfo_exists():
            self.popup.destroy()
        self.root.withdraw()
        self._finish_close()

    def _finish_close(self) -> None:
        if self.worker.is_alive():
            self.root.after(100, self._finish_close)
        else:
            self.worker.join()
            self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()


if __name__ == "__main__":
    MusicLens().run()

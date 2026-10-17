"""Pure, testable helpers for MusicLens."""
from __future__ import annotations

import io
import wave
from urllib.parse import urlparse


def make_wav(pcm: bytes, *, rate: int, channels: int, sample_width: int = 2) -> bytes:
    """Package in-memory PCM samples as a WAV (never written to disk)."""
    if rate <= 0 or channels <= 0 or sample_width <= 0:
        raise ValueError("Invalid audio format")
    out = io.BytesIO()
    with wave.open(out, "wb") as wav:
        wav.setnchannels(channels)
        wav.setsampwidth(sample_width)
        wav.setframerate(rate)
        wav.writeframes(pcm)
    return out.getvalue()


def extract_song(response: dict) -> dict | None:
    """Extract song metadata from Shazam response; empty matches are not songs."""
    if not isinstance(response, dict) or not response.get("matches"):
        return None
    track = response.get("track")
    if not isinstance(track, dict):
        return None
    title = str(track.get("title") or "").strip()
    artist = str(track.get("subtitle") or "").strip()
    if not title:
        return None
    link = str(track.get("url") or "").strip()
    parsed = urlparse(link)
    if not (parsed.scheme == "https" and parsed.hostname in {"shazam.com", "www.shazam.com"}):
        link = ""
    return {"title": title, "artist": artist or "아티스트 정보 없음", "url": link}


def song_key(song: dict) -> str:
    return "\u241f".join((str(song.get("title", "")).casefold(), str(song.get("artist", "")).casefold()))

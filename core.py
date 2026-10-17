"""Pure, testable helpers for MusicLens."""
from __future__ import annotations

import io
import wave
from urllib.parse import urlparse

import numpy as np


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


def suppress_voice(wav_bytes: bytes, *, strength: float = 0.55) -> bytes:
    """Quickly reduce the speech band before recognition.

    This is intentionally lightweight and dependency-free: it attenuates the
    frequency range where most speech energy sits. It is not a full source
    separation model, but is fast enough for real-time desktop recognition.
    """
    strength = float(np.clip(strength, 0.0, 1.0))
    with wave.open(io.BytesIO(wav_bytes), "rb") as source:
        channels = source.getnchannels()
        width = source.getsampwidth()
        rate = source.getframerate()
        frames = source.readframes(source.getnframes())
    if width != 2 or not frames:
        return wav_bytes

    audio = np.frombuffer(frames, dtype="<i2").astype(np.float32)
    audio = audio.reshape(-1, channels)
    mono = audio.mean(axis=1)
    size = 2048
    hop = 1024
    window = np.hanning(size).astype(np.float32)
    result = np.zeros_like(audio)
    weights = np.zeros(len(mono), dtype=np.float32)
    freqs = np.fft.rfftfreq(size, 1.0 / rate)
    voice_band = (freqs >= 250.0) & (freqs <= 3800.0)
    gain = np.ones_like(freqs, dtype=np.float32)
    gain[voice_band] = 1.0 - 0.55 * strength
    for start in range(0, len(mono), hop):
        end = min(start + size, len(mono))
        length = end - start
        frame = np.zeros(size, dtype=np.float32)
        frame[:length] = mono[start:end] * window[:length]
        spectrum = np.fft.rfft(frame)
        filtered = np.fft.irfft(spectrum * gain, n=size)[:length]
        result[start:end] += filtered[:, None] * window[:length, None]
        weights[start:end] += window[:length] ** 2
    valid = weights > 1e-6
    reduced = np.zeros_like(mono)
    reduced[valid] = result[valid, 0] / weights[valid]
    reduced[~valid] = mono[~valid]
    # Preserve stereo shape while using the separated mono signal for matching.
    output = np.repeat(reduced[:, None], channels, axis=1)
    output = np.clip(output, -32768, 32767).astype("<i2")
    return make_wav(output.tobytes(), rate=rate, channels=channels, sample_width=width)


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

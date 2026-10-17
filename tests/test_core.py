import io
import sys
import unittest
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core import extract_song, make_wav, song_key, suppress_voice


class CoreTests(unittest.TestCase):
    def test_wav_header(self):
        wav = make_wav(b"\x00\x00" * 160, rate=16000, channels=1)
        with wave.open(io.BytesIO(wav)) as f:
            self.assertEqual(f.getframerate(), 16000)
            self.assertEqual(f.getnchannels(), 1)
            self.assertEqual(f.getnframes(), 160)

    def test_song_extraction(self):
        response = {"matches": [{"id": "x"}], "track": {"title": "Song", "subtitle": "Artist", "url": "https://www.shazam.com/track/1"}}
        result = extract_song(response)
        self.assertEqual(result["artist"], "Artist")
        self.assertEqual(result["url"], "https://www.shazam.com/track/1")

    def test_no_match(self):
        self.assertIsNone(extract_song({"matches": [], "track": {"title": "Bad"}}))

    def test_bad_url_rejected(self):
        response = {"matches": [{}], "track": {"title": "Song", "url": "https://shazam.com.example.net/track/1"}}
        self.assertEqual(extract_song(response)["url"], "")

    def test_song_key_casefold(self):
        self.assertEqual(song_key({"title": "ABC", "artist": "KJH"}), song_key({"title": "abc", "artist": "kjh"}))

    def test_suppress_voice_returns_valid_wav(self):
        source = make_wav(b"\x00\x00" * 4096, rate=16000, channels=1)
        reduced = suppress_voice(source)
        with wave.open(io.BytesIO(reduced), "rb") as f:
            self.assertEqual(f.getframerate(), 16000)
            self.assertEqual(f.getnchannels(), 1)
            self.assertEqual(f.getnframes(), 4096)


if __name__ == "__main__":
    unittest.main()

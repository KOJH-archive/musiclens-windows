import asyncio
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import recognize_audio


class RecognitionTests(unittest.TestCase):
    def test_installed_shazam_api_shape(self):
        class FakeShazam:
            async def recognize(self, data):
                self.data = data
                return {"matches": [{"id": "sample"}], "track": {"title": "Song", "subtitle": "Artist"}}

        fake = FakeShazam()
        with patch("app.Shazam", return_value=fake):
            song = asyncio.run(recognize_audio(b"wav bytes"))
        self.assertEqual(fake.data, b"wav bytes")
        self.assertEqual(song["title"], "Song")


if __name__ == "__main__":
    unittest.main()

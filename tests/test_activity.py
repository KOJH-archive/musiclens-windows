import sys
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app


class FakeStream:
    def __init__(self, levels):
        self.levels = levels
        self.reads = 0

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def read(self, count, exception_on_overflow=False):
        level = self.levels[self.reads]
        self.reads += 1
        return level.to_bytes(2, "little", signed=True) * count


class FakeAudio:
    def __init__(self, stream):
        self.stream = stream

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def get_default_wasapi_loopback(self):
        return {"maxInputChannels": 1, "defaultSampleRate": 48000, "index": 1}

    def open(self, **kwargs):
        return self.stream


class ActivityTests(unittest.TestCase):
    def test_waits_for_sustained_sound(self):
        stream = FakeStream([0, 0, 1000, 1000])
        with patch.object(app.pyaudio, "PyAudio", return_value=FakeAudio(stream)):
            found = app.wait_for_output_activity(threading.Event(), lambda: (False, True, 0.0))
        self.assertTrue(found)
        self.assertEqual(stream.reads, 4)

    def test_disabled_monitor_stops_without_reading(self):
        stream = FakeStream([])
        with patch.object(app.pyaudio, "PyAudio", return_value=FakeAudio(stream)):
            found = app.wait_for_output_activity(threading.Event(), lambda: (False, False, 0.0))
        self.assertFalse(found)
        self.assertEqual(stream.reads, 0)


if __name__ == "__main__":
    unittest.main()

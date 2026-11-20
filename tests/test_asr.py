import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import wave

from bili_study.asr import transcribe


def write_wav(path, value, channels=1):
    with wave.open(str(path), "wb") as output:
        output.setparams((channels, 2, 16000, 0, "NONE", "not compressed"))
        output.writeframes(value.to_bytes(2, "little", signed=True) * (16000 * channels))


class TranscriptionTests(unittest.TestCase):
    def test_silence_and_bad_format_block_publication(self):
        with tempfile.TemporaryDirectory() as temp:
            wav = Path(temp) / "audio.wav"
            dest = Path(temp) / "asr.json"
            write_wav(wav, 0)
            with self.assertRaisesRegex(ValueError, "静音"):
                transcribe(wav, dest)
            write_wav(wav, 100, channels=2)
            with self.assertRaisesRegex(ValueError, "16 kHz"):
                transcribe(wav, dest)
            self.assertFalse(dest.exists())

    def test_cache_hit_invalid_cache_and_input_change(self):
        with tempfile.TemporaryDirectory() as temp:
            wav = Path(temp) / "audio.wav"
            dest = Path(temp) / "asr.json"
            write_wav(wav, 100)
            result = {"language": "zh", "segments": [{"start": 0.0, "end": 1.02, "text": "黄金"}]}
            with patch("mlx_whisper.transcribe", return_value=result) as engine:
                generated = transcribe(wav, dest)
                self.assertEqual(generated["segments"][0]["end"], 1.0)
                self.assertEqual(transcribe(wav, dest), generated)
                self.assertEqual(engine.call_count, 1)
                generated["segments"] = [{"id": 0, "start": 1, "end": -1, "text": "x"}]
                dest.write_text(json.dumps(generated))
                transcribe(wav, dest)
                self.assertEqual(engine.call_count, 2)
                write_wav(wav, 200)
                transcribe(wav, dest)
                self.assertEqual(engine.call_count, 3)

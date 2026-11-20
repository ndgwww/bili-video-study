import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from bili_study import media


class MediaTests(unittest.TestCase):
    info = {
        "aid": 9,
        "owner": {"name": "UP"},
        "title": "两集",
        "pages": [
            {"page": 1, "cid": 17, "duration": 45},
            {"page": 2, "cid": 28, "duration": 51},
        ],
    }
    url = "https://www.bilibili.com/video/BV1xx411c7mm/"

    def test_resolve_selected_and_out_of_bounds(self):
        with patch.object(media, "_api", return_value=self.info):
            self.assertEqual(media.resolve(self.url + "?p=2")["cid"], 28)
            with self.assertRaisesRegex(media.MediaError, "可选 1–2"):
                media.resolve(self.url + "?p=3")

    def test_soft_caption_track_is_decoded_and_tagged(self):
        from io import BytesIO

        track = dict(
            lan="zh-CN", lan_doc="中文", id=2, subtitle_url="//example.test/file"
        )
        payload = json.dumps(
            {"body": [{"from": 0.5, "to": 2.0, "content": "字幕样例"}]}
        ).encode()
        with (
            patch.object(
                media, "_api", return_value={"subtitle": {"subtitles": [track]}}
            ),
            patch.object(media, "_http", return_value=BytesIO(payload)),
        ):
            result = media._platform({"aid": 5, "cid": 123, "duration": 10})
        self.assertEqual(result["source"], "platform")
        self.assertEqual(result["status"], "acquired")
        self.assertEqual(
            result["segments"], [dict(id=0, start=0.5, end=2.0, text="字幕样例")]
        )

    def test_reuse_hardsub_without_ocr_flag(self):
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch.object(media, "_platform") as fetch,
        ):
            sample = {
                "source": "screen_ocr",
                "status": "acquired",
                "cid": 123,
                "roi": [0, 0.7, 1, 0.2],
                "segments": [{"id": 0, "start": 0, "end": 1, "text": "字幕样例"}],
            }
            (Path(tmp) / "captions.json").write_text(json.dumps(sample))
            self.assertEqual(media.prepare_captions({"cid": 123}, tmp), sample)
            fetch.assert_not_called()


if __name__ == "__main__":
    unittest.main()

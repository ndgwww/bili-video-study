import json
import tempfile
import unittest
import wave
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from bili_study import cli
from bili_study.core import digest, make_material, publication_folder, put_json, read_json, segments_index
from bili_study.publisher import publish, render

META = {"bvid": "BV1xx411c7mm", "part": 2, "cid": 333, "title": "测试",
        "owner": "示例", "duration": 2, "audio_duration": 2}
ROWS = [{"id":0,"start":0.0,"end":1.0,"text":"上个月"},
        {"id":1,"start":1.25,"end":2.0,"text":"下降百分之三"}]


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.folder = self.root / "runs" / META["bvid"] / str(META["cid"])
        self.args = SimpleNamespace(url="https://www.bilibili.com/video/BV1xx411c7mm/?p=2",
                                    ocr=False, roi=None, model="test", runs_dir=None)

    def prepare(self, subtitles=None, speech=None):
        def make_wav(meta, folder):
            with wave.open(str(folder/"audio.wav"),"wb") as f:
                f.setparams((1,2,16000,0,"NONE",""))
                f.writeframes(b'\x01\x00'*32000)

        def transcribe(wav, output, model):
            put_json(output, {"segments":ROWS})

        fetch = subtitles or (lambda *a,**kw: {"status":"no_visible_track","segments":[]})
        with (patch.object(cli,"ROOT",self.root),
              patch("bili_study.media.resolve",return_value=META.copy()),
              patch("bili_study.media.prepare_audio",side_effect=make_wav),
              patch("bili_study.media.prepare_captions",side_effect=fetch),
              patch("bili_study.asr.transcribe",side_effect=speech or transcribe)):
            cli.prepare(self.args)
        return read_json(self.folder/"manifest.json")

    def evidence(self, meta):
        put_json(self.folder/"evidence.json",{
            **{k:META[k] for k in ("bvid","cid","part")},
            "material_sha256":meta["stages"]["material"]["sha256"],
            "claims":[{"id":"V01","claim":"下降","segments":[1],"check_question":"下降多少？"}]})

    def test_selected_part_time_and_no_caption(self):
        meta = self.prepare()
        self.evidence(meta)
        (self.folder/"drafts/study.md").write_text("# 笔记\n## 结论 V01\n下降 {{seg:1}}")
        published = publish(self.folder,"study",meta)
        self.assertIn("?p=2&t=1",(self.folder/published["output"]).read_text())
        self.assertEqual(meta["stages"]["captions"]["query_status"],"no_visible_track")

    def test_default_publication_moves_with_project_and_reuses_identity(self):
        with patch("bili_study.core.__file__", self.root / "src/bili_study/core.py"):
            self.folder.mkdir(parents=True)
            entry = publication_folder(self.folder, "研究/案例")
            expected = self.root / "生成文档" / f"研究_案例（{META['bvid']}-{META['cid']}）"
            self.assertEqual(entry.resolve(), expected.resolve())
            self.assertTrue(expected.is_dir())
            self.assertFalse(entry.readlink().is_absolute())
            self.assertEqual(publication_folder(self.folder, "新标题").resolve(), expected.resolve())
            isolated = self.root / "cold" / META["bvid"] / str(META["cid"])
            self.assertEqual(publication_folder(isolated, "新标题"), isolated)

    def test_captions_failure_preserves_speech(self):
        def fail(*a,**kw):
            raise OSError("network")
        meta = self.prepare(subtitles=fail)
        self.assertEqual(meta["stages"]["captions"]["query_status"],"failed")
        self.assertEqual(meta["stages"]["material"]["status"],"done")

    def test_failed_speech_can_resume(self):
        def fail(*a):
            raise ValueError("无有效讲话")
        with self.assertRaises(ValueError):
            self.prepare(speech=fail)
        meta = read_json(self.folder/"manifest.json")
        self.assertEqual(meta["stages"]["asr"]["status"],"failed")
        self.assertEqual(self.prepare()["stages"]["asr"]["status"],"done")

    def test_bad_evidence_never_overwrites(self):
        meta = self.prepare()
        self.evidence(meta)
        draft = self.folder/"drafts/study.md"
        draft.write_text("# 测试\nV01 {{seg:1}}")
        publish(self.folder,"study",meta)
        before = digest(self.folder/"视频学习笔记.md")
        for text in ("# 测试\nV01 {{seg:77}}","# 测试\nV66 {{seg:1}}"):
            draft.write_text(text)
            with self.assertRaises(ValueError):
                publish(self.folder,"study",meta)
            self.assertEqual(before,digest(self.folder/"视频学习笔记.md"))

    def test_review_requires_original_segment_and_external_source(self):
        meta = self.prepare()
        self.evidence(meta)
        draft = self.folder/"drafts/review.md"
        for ref,link in [(0,"https://www.imf.org/"),(1,"https://www.bilibili.com/")]:
            draft.write_text("# 报告\n## V01 核查\n有支持 {{seg:%d}} [来源](%s)"%(ref,link))
            with self.assertRaises(ValueError):
                publish(self.folder,"review",meta)
        draft.write_text("# 报告\n## V01 核查\n部分支持 {{seg:1}} [来源](https://www.imf.org/)")
        self.assertEqual(publish(self.folder,"review",meta)["output"],"外部核查与补充.md")

    def test_changed_material_requires_editor_review(self):
        meta = self.prepare()
        self.evidence(meta)
        (self.folder/"写作材料.md").write_text("改动")
        with self.assertRaisesRegex(ValueError,"已变化"):
            publish(self.folder,"study",meta)

    def test_raw_caption_remains_separate(self):
        text=make_material(META, ROWS, {"status":"acquired","source":"OCR","segments":[
            {"start":1.5,"end":2,"text":"下降3%"}]})
        self.assertEqual(text.count("同期字幕候选"),1)
        self.assertIn("下降百分之三",text)
        self.assertIn("下降3%",text)

    def test_out_of_bounds_and_empty_speech(self):
        for rows in ([],[dict(ROWS[0],end=3)],[ROWS[0],ROWS[0]]):
            with self.assertRaises(ValueError):
                segments_index(rows,2)

import argparse
import json
import re
import wave
from pathlib import Path

from .core import atomic_text, digest, locked, make_material, put_json, read_json, segments_index, srt_text, publication_folder
from .publisher import publish

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MODEL = "mlx-community/whisper-large-v3-turbo"


def safe_error(error):
    return re.sub(r"https?://\S+", "[网络地址]", str(error))[:500]


class Journal:
    def __init__(self, folder, meta):
        self.path = folder / "manifest.json"
        self.data = read_json(self.path) if self.path.exists() else {"stages": {}}
        self.data.update({key: meta[key] for key in ("bvid", "cid", "part", "duration", "title", "owner")})
        self.data["part_title"] = meta.get("part_title", self.data.get("part_title", ""))
        self.data["url"] = f"https://www.bilibili.com/video/{meta['bvid']}/?p={meta['part']}"
        for key in ("resolve", "media", "asr", "captions", "material", "study", "review"):
            self.data["stages"].setdefault(key, {"status": "pending"})
        self.set("resolve", "done")

    def set(self, stage, status, **fields):
        self.data["stages"][stage] = {"status": status, **fields}
        put_json(self.path, self.data)

    def step(self, stage, action):
        self.set(stage, "running")
        try:
            result = action()
        except (Exception, KeyboardInterrupt) as exc:
            self.set(stage, "failed", error=safe_error(exc) or "已中断")
            raise
        self.set(stage, "done", **result)


def prepare(args):
    from . import media
    from .asr import transcribe

    meta = media.resolve(args.url)
    runs = args.runs_dir if args.runs_dir else ROOT / "runs"
    folder = runs / meta["bvid"] / str(meta["cid"])
    with locked(folder):
        log = Journal(folder, meta)
        (folder/"drafts").mkdir(exist_ok=True)
        wav = folder/"audio.wav"
        cap_file = folder/"captions.json"
        recognition = folder/"asr.json"

        def audio_stage():
            media.prepare_audio(meta, folder)
            with wave.open(str(wav), "rb") as stream:
                if stream.getnchannels() != 1 or stream.getframerate() != 16000:
                    raise ValueError("WAV 需要 16 kHz 单声道")
                duration = stream.getnframes() / stream.getframerate()
            if duration <= 0 or abs(duration-meta["duration"]) > max(4,meta["duration"]*0.02):
                raise ValueError("音轨时长与分 P 不符")
            log.data["audio_duration"] = duration
            return {"output": wav.name, "sha256": digest(wav)}

        def subtitle_stage():
            try:
                info = media.prepare_captions(meta, folder, use_ocr=args.ocr, roi=args.roi)
            except Exception as error:
                info = {"status":"failed", "source":"unknown", "segments":[],
                        "error":safe_error(error)}
            put_json(cap_file, info)
            return {"query_status":info["status"], "source":info.get("source"),
                    "count":len(info.get("segments",[])), "sha256":digest(cap_file)}

        def speech_stage():
            transcribe(wav, recognition, args.model)
            items = read_json(recognition)["segments"]
            segments_index(items, log.data["audio_duration"])
            return {"model":args.model,"count":len(items),"sha256":digest(recognition)}

        def material_stage():
            items = read_json(recognition)["segments"]
            captions = read_json(cap_file)
            data = make_material(meta, items, captions)
            results = publication_folder(folder, meta["title"])
            atomic_text(results/"口播转写.srt", srt_text(items))
            caption_export = results/"原片字幕.srt"
            if captions.get("segments"):
                atomic_text(caption_export, srt_text(captions["segments"]))
            else:
                caption_export.unlink(missing_ok=True)
            out = folder/"写作材料.md"
            atomic_text(out,data)
            return {"output":out.name,"sha256":digest(out)}

        log.step("media", audio_stage)
        log.step("captions", subtitle_stage)
        log.step("asr", speech_stage)
        log.step("material", material_stage)
        for kind in ("study","review"):
            old = log.data["stages"][kind]
            if old.get("status") == "done" and old.get("material_sha256") != log.data["stages"]["material"]["sha256"]:
                log.set(kind,"pending", reason="写作材料已变更，需重新发布")
    return {"run":str(folder),"materials":str(folder/"写作材料.md"),"stages":log.data["stages"]}


def publication(args):
    folder = args.folder.resolve()
    if not (folder/"manifest.json").is_file():
        raise ValueError("找不到该任务的 manifest.json")
    with locked(folder):
        log = Journal(folder,read_json(folder/"manifest.json"))
        log.step(args.document,lambda: publish(folder,args.document,log.data))
        other = "review" if args.document == "study" else "study"
        previous = log.data["stages"][other]
        if previous.get("status") == "done" and previous.get("evidence_sha256") != digest(folder/"evidence.json"):
            log.set(other, "pending", reason="观点索引已变化，需核对后重新发布")
    return log.data["stages"][args.document]


def region(value):
    try:
        x,y,w,h = map(float,value.split(","))
        if not (0 <= x < 1 and 0 <= y < 1 and w > 0 and h > 0 and x+w <= 1 and y+h <= 1):
            raise ValueError
    except ValueError:
        raise argparse.ArgumentTypeError("需传从左上角起算的 x,y,w,h，数值归一化至 0–1")
    return (x,y,w,h)


def main():
    parser = argparse.ArgumentParser(prog="bili-study",description="提取指定分 P 的写作材料，并按段号校验发布。")
    commands = parser.add_subparsers(dest="command",required=True)
    prep = commands.add_parser("prepare")
    prep.add_argument("url")
    prep.add_argument("--runs-dir",type=Path,help="独立运行目录，可用于冷启动验收")
    prep.add_argument("--model",default=DEFAULT_MODEL)
    prep.add_argument("--ocr",action=argparse.BooleanOptionalAction,default=True,help="平台轨不可用时扫描画面底部字幕（默认开启）")
    prep.add_argument("--roi",type=region,help="OCR 区域，左上角归一化坐标 x,y,w,h")
    pub = commands.add_parser("publish")
    pub.add_argument("folder",type=Path)
    pub.add_argument("--document",choices=("study","review"),required=True)
    args = parser.parse_args()
    if args.command=="prepare" and args.roi and not args.ocr:
        parser.error("--roi 需与 --ocr 同用")
    if args.command == "prepare" and args.ocr and args.roi is None:
        args.roi = (0.025, 0.72, 0.95, 0.275)
    try:
        result = prepare(args) if args.command=="prepare" else publication(args)
    except (ValueError, KeyError, RuntimeError, OSError, ImportError) as e:
        parser.exit(1,safe_error(e)+"\n")
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=="__main__":
    main()

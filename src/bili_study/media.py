"""Acquire public Bilibili media and independently sourced subtitles."""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import wave
from pathlib import Path

API = "https://api.bilibili.com"
BVID = re.compile(r"BV[0-9a-zA-Z]{10}")
HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": "https://www.bilibili.com/"}


class MediaError(RuntimeError):
    pass


def _http(url, *, cookie=None):
    headers = dict(HEADERS)
    if cookie and urllib.parse.urlsplit(url).hostname == "api.bilibili.com":
        headers["Cookie"] = "SESSDATA=" + urllib.parse.quote(cookie, safe="%")
    try:
        return urllib.request.urlopen(
            urllib.request.Request(url, headers=headers), timeout=45
        )
    except urllib.error.HTTPError as err:
        raise MediaError(f"HTTP {err.code}") from None
    except (urllib.error.URLError, TimeoutError) as err:
        raise MediaError(f"网络请求失败 ({type(err).__name__})") from None


def _api(path, args, *, login=False):
    url = API + path + "?" + urllib.parse.urlencode(args)
    with _http(url, cookie=os.getenv("BILI_SESSDATA") if login else None) as response:
        info = json.load(response)
    if info.get("code") != 0 or info.get("data") is None:
        raise MediaError(f"哔哩哔哩接口 {path} 返回 {info.get('code')}")
    return info["data"]


def resolve(url):
    url = url.strip()
    if BVID.fullmatch(url):
        url = "https://www.bilibili.com/video/" + url
    bits = urllib.parse.urlsplit(url)
    if bits.hostname == "b23.tv" and bits.scheme in ("http", "https"):
        with _http(url) as response:
            bits = urllib.parse.urlsplit(response.geturl())
    if bits.hostname not in (
        "www.bilibili.com",
        "bilibili.com",
        "m.bilibili.com",
    ) or bits.scheme not in ("http", "https"):
        raise MediaError("请输入 B 站 BV 视频链接或 b23.tv 短链")
    match = re.fullmatch(r"/video/(BV[0-9A-Za-z]{10})/?", bits.path)
    if not match:
        raise MediaError("仅支持带 BV 号的普通视频")
    parts = urllib.parse.parse_qs(bits.query, keep_blank_values=True).get("p", ["1"])
    if len(parts) != 1 or not re.fullmatch("[1-9][0-9]{0,5}", parts[0]):
        raise MediaError("分 P 必须是正整数")
    part = int(parts[0])
    bv = match.group(1)
    info = _api("/x/web-interface/view", {"bvid": bv})
    page = next((x for x in info["pages"] if x.get("page") == part), None)
    if page is None:
        raise MediaError(f"分 P {part} 不存在，可选 1–{len(info['pages'])}")
    return dict(
        bvid=bv,
        aid=info["aid"],
        cid=page["cid"],
        part=part,
        part_title=page.get("part", ""),
        owner=info.get("owner", {}).get("name", ""),
        title=info["title"],
        duration=page["duration"],
        url=f"https://www.bilibili.com/video/{bv}/?p={part}",
    )


def _json(path, value):
    tmp = path.with_suffix(path.suffix + ".part")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    tmp.replace(path)


def _download_dash(meta, kind, dest):
    data = _api(
        "/x/player/playurl",
        {"bvid": meta["bvid"], "cid": meta["cid"], "qn": 64, "fnval": 16, "fourk": 1},
        login=True,
    )
    streams = (data.get("dash") or {}).get(kind) or []
    if kind == "video":
        streams = [
            s for s in streams if s.get("codecs", "").startswith(("avc", "hvc", "hev"))
        ]
    elif kind == "audio":
        streams = [s for s in streams if s.get("codecs", "").startswith("mp4a")]
    if not streams:
        raise MediaError(f"所选分 P 无可用 DASH {kind} 流")
    chosen = max(streams, key=lambda s: s.get("bandwidth", 0))
    urls = [
        chosen.get("baseUrl") or chosen.get("base_url"),
        *(chosen.get("backupUrl") or chosen.get("backup_url") or []),
    ]
    temp = dest.with_suffix(dest.suffix + ".part")
    for url in filter(None, urls):
        try:
            with _http(url) as response, temp.open("wb") as output:
                expected = int(response.headers.get("Content-Length", 0))
                shutil.copyfileobj(response, output)
            size = temp.stat().st_size
            if size == 0 or (expected and size != expected):
                raise MediaError("CDN 响应未完整")
            temp.replace(dest)
            return
        except (OSError, MediaError):
            temp.unlink(missing_ok=True)
    raise MediaError(f"未能完整取得所选分 P 的 {kind} 流")


def _download(meta, kind, dest):
    try:
        _download_dash(meta, kind, dest)
        return
    except (MediaError, OSError, KeyError, ValueError):
        pass
    # Public API responses can change independently of yt-dlp's extractor.
    # Isolate its temporary files; never persist signed media URLs or cookies.
    binary = shutil.which("yt-dlp") or str(Path(sys.executable).parent / "yt-dlp")
    if not Path(binary).is_file():
        raise MediaError("公开媒体接口未取得音轨/视频，需安装 yt-dlp 后重试")
    selector = "bestaudio[ext=m4a]" if kind == "audio" else "bestvideo[ext=mp4]"
    with tempfile.TemporaryDirectory(dir=dest.parent) as tmp:
        target = Path(tmp) / dest.name
        cmd = [
            binary,
            "--ignore-config",
            "--no-playlist",
            "--no-warnings",
            "--quiet",
            "--socket-timeout",
            "30",
            "--retries",
            "2",
            "--no-progress",
            "-f",
            selector,
            "-o",
            str(target),
            "--",
            meta["url"],
        ]
        proc = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if proc.returncode or not target.is_file() or target.stat().st_size == 0:
            raise MediaError("公开媒体接口及 yt-dlp 均未能取得所选分 P 的媒体")
        target.replace(dest)


def _audio_ok(path, duration):
    try:
        with wave.open(str(path)) as audio:
            n = audio.getnframes()
            return (
                n > 0
                and audio.getsampwidth() == 2
                and audio.getnchannels() == 1
                and audio.getframerate() == 16000
                and abs(n / 16000 - duration) <= 2.5
                and path.stat().st_size >= n * 2
            )
    except (OSError, wave.Error, EOFError):
        return False


def prepare_audio(meta, run_dir):
    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    original = run_dir / "media.m4a"
    wav = run_dir / "audio.wav"
    if (
        original.is_file()
        and original.stat().st_size
        and _audio_ok(wav, meta["duration"])
    ):
        return wav
    tmp = wav.with_name("audio.part.wav")
    ffmpeg = shutil.which("ffmpeg") or str(Path(sys.executable).parent / "ffmpeg")
    if Path(ffmpeg).is_file():
        convert = [
            ffmpeg,
            "-nostdin",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(original),
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-c:a",
            "pcm_s16le",
            str(tmp),
        ]
    elif shutil.which("afconvert"):
        convert = [
            "afconvert",
            "-f",
            "WAVE",
            "-d",
            "LEI16@16000",
            "-c",
            "1",
            str(original),
            str(tmp),
        ]
    else:
        raise MediaError("音频转码需要 ffmpeg")
    for attempt in range(2):
        if attempt or not original.exists():
            _download(meta, "audio", original)
        tmp.unlink(missing_ok=True)
        proc = subprocess.run(
            convert, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE
        )
        if proc.returncode == 0 and _audio_ok(tmp, meta["duration"]):
            tmp.replace(wav)
            return wav
    raise MediaError("音轨解码失败或时长与所选分 P 不符")


def _platform(meta):
    data = _api(
        "/x/player/wbi/v2", {"aid": meta["aid"], "cid": meta["cid"]}, login=True
    )
    tracks = [
        t
        for t in (data.get("subtitle") or {}).get("subtitles", [])
        if t.get("subtitle_url")
    ]
    if not tracks:
        return {
            "status": "login_required"
            if data.get("need_login_subtitle")
            else "no_visible_track",
            "source": "platform",
            "segments": [],
        }

    # Prefer the reported speech language, using Chinese when the platform
    # provides no language hint. A non-AI flag alone does not identify an author.
    speech = (data.get("asr_language") or "zh").lower().split("-")[0]
    aliases = {
        "cmn": "zh",
        "yue": "zh",
        "zho": "zh",
        "eng": "en",
        "jpn": "ja",
        "kor": "ko",
    }
    preferred = aliases.get(speech, speech)

    def rank(t):
        lang = t.get("lan", "").lower()
        ai = lang.startswith("ai-") or t.get("ai_type", 0) != 0
        return (lang.removeprefix("ai-").split("-")[0] != preferred, ai)

    fetch_failed = False
    for t in sorted(tracks, key=rank):
        url = t["subtitle_url"]
        try:
            with _http("https:" + url if url.startswith("//") else url) as r:
                cues = json.load(r).get("body", [])
        except (MediaError, OSError, ValueError):
            fetch_failed = True
            continue
        rows = []
        for cue in cues:
            text = cue.get("content", "").strip()
            start = float(cue["from"])
            end = float(cue["to"])
            if text and 0 <= start < end and start < meta["duration"]:
                rows.append(
                    dict(
                        id=len(rows),
                        start=start,
                        end=min(end, meta["duration"]),
                        text=text,
                    )
                )
        if rows:
            return dict(
                status="acquired",
                source="platform",
                language=t.get("lan"),
                reported_speech_language=data.get("asr_language"),
                track_kind="platform_ai"
                if rank(t)[1]
                else "platform_origin_unspecified",
                track_label=t.get("lan_doc"),
                track_id=t.get("id_str") or t.get("id"),
                segments=rows,
            )
    return dict(
        status="failed" if fetch_failed else "no_visible_track",
        source="platform",
        segments=[],
    )


def prepare_captions(meta, run_dir, *, use_ocr=False, roi=None):
    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    path = run_dir / "captions.json"
    if path.is_file():
        old = json.loads(path.read_text())
        if old.get("cid") == meta["cid"] and old.get("status") == "acquired":
            if old.get("source") == "platform" or (
                old.get("source") == "screen_ocr"
                and (not use_ocr or old.get("ocr_version") == 2)
                and (roi is None or old.get("roi") == list(roi))
            ):
                return old
    try:
        result = _platform(meta)
    except (MediaError, OSError, ValueError, KeyError) as err:
        result = {
            "status": "failed",
            "source": "platform",
            "segments": [],
            "error": type(err).__name__,
        }
    result.update(
        platform_status=result["status"],
        cid=meta["cid"],
        bvid=meta["bvid"],
        part=meta["part"],
    )
    if use_ocr and result["status"] != "acquired":
        if roi is None:
            raise MediaError("画面字幕 OCR 需要先抽帧确认字幕区域，再传入归一化 ROI")
        x, y, w, h = roi
        if min(x, y) < 0 or min(w, h) <= 0 or x + w > 1.000001 or y + h > 1.000001:
            raise MediaError("ROI 必须位于画面内")
        media = run_dir / "video.mp4"
        if not media.is_file():
            _download(meta, "video", media)
        script = Path(__file__).with_name("caption_ocr.swift")
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "observations.json"
            subprocess.run(
                ["swift", str(script), str(media), str(out), *[str(n) for n in roi]],
                check=True,
            )
            ocr = json.loads(out.read_text())
            found = ocr["segments"]
        result.update(
            source="screen_ocr",
            language="zh-Hans",
            roi=list(roi),
            sample_interval=1.0,
            segments=found,
            examined_frames=ocr["examined_frames"],
            unread_frames=ocr["unread_frames"],
            ocr_version=2,
            timing="approximate one-second frame observations",
            status="acquired" if found else "no_visible_track",
        )
    _json(path, result)
    return result

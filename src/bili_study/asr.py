"""Transcribe a 16 kHz mono PCM WAV on Apple Silicon, retaining source timestamps."""
import hashlib
import json
import math
import os
from importlib.metadata import version
from pathlib import Path
import tempfile
import wave

DEFAULT_MODEL = "mlx-community/whisper-large-v3-turbo"
PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _model_source(model):
    location = Path(model).expanduser()
    if model == DEFAULT_MODEL:
        local = PROJECT_ROOT / ".models" / "whisper-large-v3-turbo"
        if (local / "weights.safetensors").is_file() and (local / "config.json").is_file():
            location = local
    if not location.is_dir():
        return model, {"repository": model}
    identity = {}
    for name in ("config.json", "weights.safetensors", "weights.npz"):
        path = location / name
        if path.is_file():
            stat = path.stat()
            identity[name] = [stat.st_size, stat.st_mtime_ns]
    return str(location.resolve()), {"directory": str(location.resolve()), "files": identity}


def _valid_segments(segments, duration):
    if not isinstance(segments, list) or not segments:
        return False
    previous_start = 0.0
    for i, seg in enumerate(segments):
        if not isinstance(seg, dict) or seg.get("id") != i or not isinstance(seg.get("text"), str):
            return False
        start, end = seg.get("start"), seg.get("end")
        if not all(isinstance(v, (int, float)) and math.isfinite(v) for v in (start, end)):
            return False
        if not (previous_start <= start < end <= duration) or not seg["text"].strip():
            return False
        previous_start = start
    return True


def transcribe(wav: Path, output: Path, model: str = DEFAULT_MODEL):
    """Return the cached or newly produced ASR document. Caller serializes writes per CID."""
    import numpy as np

    wav, output = Path(wav), Path(output)
    with wave.open(str(wav), "rb") as source:
        if (source.getframerate(), source.getnchannels(), source.getsampwidth()) != (16000, 1, 2):
            raise ValueError("转写输入需为 16 kHz 单声道 16 位 PCM WAV")
        nframes = source.getnframes()
        duration = nframes / 16000
    with wav.open("rb") as source:
        audio_hash = hashlib.file_digest(source, "sha256").hexdigest()
    location, identity = _model_source(model)
    settings = {"language": "zh", "task": "transcribe", "condition_on_previous_text": False}
    cache_key = {"wav_sha256": audio_hash, "model": str(model), "model_identity": identity,
                 "engine_version": version("mlx-whisper"), "settings": settings, "schema_version": 1}
    if output.is_file():
        try:
            cached = json.loads(output.read_text())
            if isinstance(cached, dict) and cached.get("cache_key") == cache_key and _valid_segments(cached.get("segments"), duration):
                return cached
        except (ValueError, OSError):
            pass
    import mlx_whisper

    with wave.open(str(wav), "rb") as source:
        raw = source.readframes(nframes)
    if not raw or len(raw) != nframes * 2:
        raise ValueError("音频为空或文件不完整")
    samples = np.frombuffer(raw, dtype="<i2")
    if not np.any(samples):
        raise ValueError("音频为全静音，没有可用口播")
    result = mlx_whisper.transcribe(samples.astype(np.float32) / 32768.0, path_or_hf_repo=location,
                                    verbose=None, **settings)
    segments = []
    for item in result["segments"]:
        start, raw_end = float(item["start"]), float(item["end"])
        # Whisper pads its final analysis window. Only timestamp audio that actually exists.
        end = min(duration, raw_end)
        if not item["text"].strip() or start >= duration or end <= start:
            continue
        segment = {"id": len(segments), "start": start, "end": end, "text": item["text"].strip()}
        if raw_end > duration:
            segment["model_end_before_padding_trim"] = raw_end
        segments.append(segment)
    if not _valid_segments(segments, duration):
        raise ValueError("没有可用的单调分段转写，无法生成正式笔记")
    document = {"source": "audio_asr", "engine": "mlx-whisper", "model": str(model),
                "language": result["language"], "duration": duration, "cache_key": cache_key,
                "text": "".join(s["text"] for s in segments), "segments": segments}
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", dir=output.parent, encoding="utf-8", delete=False) as stream:
        temp = Path(stream.name)
        try:
            json.dump(document, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.replace(temp, output)
        finally:
            temp.unlink(missing_ok=True)
    return document

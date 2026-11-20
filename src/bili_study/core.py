"""File formats shared by acquisition, the editor and publication."""
import contextlib
import fcntl
import hashlib
import json
import math
import os
import re
import tempfile
from pathlib import Path


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def atomic_text(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False, mode="w",
                                     encoding="utf-8") as pending:
        temp = Path(pending.name)
        try:
            pending.write(text)
            pending.flush()
            os.fsync(pending.fileno())
        except BaseException:
            temp.unlink(missing_ok=True)
            raise
    try:
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def put_json(path, data):
    atomic_text(path, json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + "\n")


def digest(path):
    with Path(path).open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


@contextlib.contextmanager
def locked(folder):
    folder.mkdir(parents=True, exist_ok=True)
    with (folder / ".lock").open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def segments_index(rows, duration):
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError("缺少有效音频时长")
    index = {}
    previous = -1.0
    for row in rows:
        key, start, end = row["id"], row["start"], row["end"]
        if type(key) is not int or key < 0 or key in index:
            raise ValueError(f"转写段编号无效或重复：{key}")
        if not (previous <= start and 0 <= start < end <= duration):
            raise ValueError(f"转写段 {key} 时间越界或无序：{start}..{end} / {duration}")
        if not row["text"].strip():
            raise ValueError(f"转写段 {key} 文本为空")
        index[key] = row
        previous = start
    if not index:
        raise ValueError("无有效口播，暂停出稿")
    return index


def make_material(meta, rows, captions):
    """Keep raw strings independent. Nearby screen text is only a candidate."""
    cues = sorted(captions.get("segments", []), key=lambda cue: cue["start"])
    out = [f"# {meta['title']} · 写作材料", "",
           f"BV {meta['bvid']} / CID {meta['cid']} / P{meta['part']}",
           f"字幕：{captions.get('source', 'unknown')}；状态 {captions['status']}。",
           "同时间的字幕可能含画面标签或识别误差。按主题阅读并核对关键差异。", ""]
    first = 0
    for row in rows:
        start, end = row["start"], row["end"]
        while first < len(cues) and cues[first]["end"] <= start:
            first += 1
        matches = []
        for i in range(first, len(cues)):
            cue = cues[i]
            if cue["start"] >= end:
                break
            if cue["end"] > start and cue["text"] not in matches:
                matches.append(cue["text"])
        out.append(f"[段 {row['id']} | {start:.2f}–{end:.2f}] {row['text'].strip()}")
        if matches:
            out.append("  同期字幕候选：" + " / ".join(matches))
    return "\n".join(out) + "\n"


def srt_text(rows):
    def clock(seconds):
        ms = round(seconds * 1000)
        hours, ms = divmod(ms, 3600000)
        minutes, ms = divmod(ms, 60000)
        secs, ms = divmod(ms, 1000)
        return f"{hours:02d}:{minutes:02d}:{secs:02d},{ms:03d}"

    cues = []
    for i, row in enumerate(rows, 1):
        cues.append(f"{i}\n{clock(row['start'])} --> {clock(row['end'])}\n{row['text'].strip()}\n")
    return "\n".join(cues)


def publication_folder(run, title):
    """Keep custom runs self-contained; default runs link to their permanent result directory."""
    runs_root = Path(__file__).resolve().parents[2] / "runs"
    if run.resolve().parent.parent != runs_root:
        return run
    entry = run / "成品"
    if not entry.is_symlink():
        safe_title = re.sub(r"[\x00-\x1f/:]", "_", title).strip()[:45] or "视频"
        target = runs_root.parent / "生成文档" / f"{safe_title}（{run.parent.name}-{run.name}）"
        target.mkdir(parents=True, exist_ok=True)
        entry.symlink_to(os.path.relpath(target, run.resolve()), target_is_directory=True)
    return entry

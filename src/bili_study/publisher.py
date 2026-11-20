"""Validate editorial drafts and replace the publication only after all checks."""
import re
from urllib.parse import urlsplit

from .core import atomic_text, digest, read_json, segments_index, publication_folder

NAMES = {"study": "视频学习笔记.md", "review": "外部核查与补充.md"}
REF = re.compile(r"\{\{seg:(\d+)\}\}")
VID = re.compile(r"\bV\d{2,}\b", re.ASCII)


def validate_evidence(evidence, meta, rows):
    if any(evidence.get(k) != meta[k] for k in ("cid", "bvid", "part")):
        raise ValueError("观点索引不属于当前分 P")
    if evidence.get("material_sha256") != meta["stages"]["material"]["sha256"]:
        raise ValueError("观点索引未绑定本次写作材料，需复核后更新摘要值")
    claims = {}
    for claim in evidence["claims"]:
        key = claim["id"]
        if not VID.fullmatch(key) or key in claims or not claim["claim"].strip():
            raise ValueError("观点编号或正文无效")
        ids = claim.get("segments", [])
        if not ids or any(type(i) is not int or i not in rows for i in ids):
            raise ValueError(f"{key} 的片段依据不完整")
        claims[key] = claim
    if not claims:
        raise ValueError("缺少观点索引")
    return claims


def render(draft, kind, meta, rows, claims):
    if not draft.lstrip().startswith("# ") or not REF.search(draft):
        raise ValueError("草稿需要标题和真实片段引用")
    if re.search(r'https?://(?:www\.)?bilibili\.com/[^)\s]*[?&]t=', draft):
        raise ValueError("原片时间链接须由片段标记生成")
    mentioned = set(VID.findall(draft))
    if mentioned - claims.keys():
        raise ValueError("草稿引用未登记的观点")
    if kind == "study" and mentioned != set(claims):
        raise ValueError("学习笔记未覆盖全部主要观点")
    if kind == "review":
        selected = {key for key, c in claims.items() if c.get("check_question")}
        sections = re.split(r"(?m)(?=^## )", draft)
        reviewed = set()
        for section in sections:
            heading = re.match(r"## (V\d{2,})(?!\d)", section)
            if not heading:
                continue
            key = heading[1]
            if key in reviewed or key not in selected:
                raise ValueError(f"重复或未选定的核查项：{key}")
            links = re.findall(r'\]\((https://[^\s)]+)\)', section)
            sources = [url for url in links if urlsplit(url).hostname and
                       not (urlsplit(url).hostname == "bilibili.com" or
                            urlsplit(url).hostname.endswith(".bilibili.com"))]
            if not sources or not set(map(int, REF.findall(section))).intersection(claims[key]["segments"]):
                raise ValueError(f"{key} 缺少外部直接来源或对应原片依据")
            if not any(term in section for term in ("有支持", "部分支持", "与证据冲突", "证据不足")):
                raise ValueError(f"{key} 缺少核查判断")
            reviewed.add(key)
        if reviewed != selected:
            raise ValueError("核查报告未覆盖全部选中观点")

    def replace(mark):
        row = rows.get(int(mark[1]))
        if row is None:
            raise ValueError(f"未知段号 {mark[1]}")
        t = int(row["start"])
        url = f"https://www.bilibili.com/video/{meta['bvid']}/?p={meta['part']}&t={t}"
        return f"[{t//60:02d}:{t%60:02d}]({url})"
    result = REF.sub(replace, draft)
    if "{{" in result or "}}" in result:
        raise ValueError("草稿仍含未解析标记")
    return result


def publish(folder, kind, meta):
    stage = meta["stages"]
    if any(stage.get(s, {}).get("status") != "done" for s in ("asr", "media", "material")):
        raise ValueError("采集或材料阶段未完成")
    for name, file in (("asr", "asr.json"), ("material", "写作材料.md"), ("captions", "captions.json")):
        if stage[name].get("sha256") != digest(folder / file):
            raise ValueError(f"{file} 已变化，请重做 prepare")
    rows = segments_index(read_json(folder/"asr.json")["segments"], meta["audio_duration"])
    evidence_path = folder/"evidence.json"
    claims = validate_evidence(read_json(evidence_path), meta, rows)
    draft_path = folder/"drafts"/(kind+".md")
    result = render(draft_path.read_text(), kind, meta, rows, claims)
    destination = publication_folder(folder, meta["title"]) / NAMES[kind]
    atomic_text(destination, result)
    return {"output": str(destination.relative_to(folder)), "sha256": digest(destination),
            "draft_sha256": digest(draft_path), "evidence_sha256": digest(evidence_path),
            "material_sha256": stage["material"]["sha256"]}

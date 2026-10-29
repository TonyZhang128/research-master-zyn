"""按已审阅的归类计划导入研究文档；原文只读，摘录必须能在原文找到。"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
from urllib.parse import quote
import xml.etree.ElementTree as ET
import zipfile

MAIN_DOCUMENTS = {
    "00_Research_Overview.md", "01_Benchmark_Protocol.md",
    "02_Related_Work.md", "04_Research_Findings.md",
}


def document_text(path: Path) -> str:
    """读取 Markdown/文本或 DOCX 正文；其他格式须先提供可核对的提取文本。"""
    if path.suffix.lower() in {".md", ".txt"}:
        return path.read_text(encoding="utf-8-sig").replace("\r\n", "\n")
    if path.suffix.lower() == ".docx":
        try:
            with zipfile.ZipFile(path) as archive:
                root = ET.fromstring(archive.read("word/document.xml"))
        except (zipfile.BadZipFile, KeyError, ET.ParseError) as error:
            raise ValueError(f"无法读取 DOCX 正文: {path}") from error
        ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
        return "\n".join("".join(p.itertext()) for p in root.findall(".//w:p", ns))
    raise ValueError(f"{path.suffix} 不能直接合并；请在计划的 text_source 中提供经核对的 Markdown/文本")


def resolve_file(value: str, plan_directory: Path) -> Path:
    path = Path(value).expanduser()
    path = path if path.is_absolute() else plan_directory / path
    if not path.is_file():
        raise ValueError(f"导入文件不存在或不是文件: {path}")
    return path.resolve()


def destination_path(value: str, staging: Path) -> Path:
    """只允许主科研文档和有明确编号的方法/实验，阻止路径越界与代码覆盖。"""
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or "\\" in value:
        raise ValueError(f"不安全的文档目标: {value}")
    if len(path.parts) == 2 and path.parts[0] == "docs" and path.name in MAIN_DOCUMENTS:
        return staging / path
    if (len(path.parts) == 4 and path.parts[:2] == ("docs", "03_Methods_Experiments")
            and re.fullmatch(r"M-\d{3}_[\w-]+", path.parts[2])
            and (path.name == "Method.md" or re.fullmatch(r"EXP-\d{3}\.md", path.name))):
        target = staging / path
        method_id = path.parts[2].split("_", 1)[0]
        if any(folder != target.parent for folder in
               (staging / "docs/03_Methods_Experiments").glob(f"{method_id}_*")):
            raise ValueError(f"方法 ID 已对应其他方法目录: {method_id}")
        if not target.exists():
            template_name = "Method.md" if path.name == "Method.md" else "Experiment.md"
            text = (staging / ".agents/templates" / template_name).read_text(encoding="utf-8")
            text = text.replace("M-xxx", method_id)
            if path.name != "Method.md":
                text = text.replace("EXP-xxx", path.stem)
                duplicates = list((staging / "docs/03_Methods_Experiments").glob(f"*/{path.name}"))
                if duplicates:
                    raise ValueError(f"实验 ID 已在其他方法使用: {path.stem}")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
            # 实验文件的 Method.md 链接必须指向同目录内的真实方法文档。
            if path.name != "Method.md":
                destination_path(str(path.parent / "Method.md"), staging)
        return target
    raise ValueError(f"不允许的科研文档目标: {value}")


def merge_section(target: Path, heading: str, content: str, original: Path) -> None:
    """把原文摘录插入指定已有章节末尾，保留模板和已有内容。"""
    text = target.read_text(encoding="utf-8")
    match = re.search(r"^(#{1,6})[ \t]+" + re.escape(heading) + r"[ \t]*$", text, re.MULTILINE)
    if not match:
        raise ValueError(f"目标章节不存在: {target.name} / {heading}")
    next_heading = re.search(r"^#{1," + str(len(match.group(1))) + r"}[ \t]+", text[match.end():], re.MULTILINE)
    insertion = match.end() + next_heading.start() if next_heading else len(text)
    link = quote(Path(os.path.relpath(original, target.parent)).as_posix(), safe="/")
    # 用引文块明确区分历史记录与新仓库的实测结果，也避免原文标题改变目标结构。
    quoted = "\n".join("> " + line for line in content.splitlines())
    block = f"\n\n导入记录（[原文]({link})；未在本仓库重新验证）：\n\n{quoted}\n\n"
    target.write_text(text[:insertion].rstrip() + block + text[insertion:], encoding="utf-8")


def import_documents(plan_path: Path, staging: Path) -> int:
    """执行 JSON 计划，保留原件/哈希；计划任一步失败均由上层取消发布。"""
    plan_path = plan_path.expanduser().resolve()
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    documents = plan.get("documents") if isinstance(plan, dict) else None
    if not isinstance(documents, list) or not documents:
        raise ValueError("导入计划必须包含非空 documents 数组")
    records = []
    seen = set()
    for item in documents:
        if not isinstance(item, dict) or not isinstance(item.get("source"), str):
            raise ValueError("每项导入记录必须包含 source 文件路径")
        source = resolve_file(item["source"], plan_path.parent)
        if source in seen:
            raise ValueError(f"同一来源应在一个 sections 列表中归类，不能重复导入: {source}")
        seen.add(source)
        if "text_source" in item and not isinstance(item["text_source"], str):
            raise ValueError("text_source 必须是已提取文本的文件路径")
        text_source = resolve_file(item["text_source"], plan_path.parent) if item.get("text_source") else source
        text = document_text(text_source)
        checksum = hashlib.sha256(source.read_bytes()).hexdigest()
        archive = staging / "docs/_imports" / checksum / "original" / source.name
        archive.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, archive)
        extracted = archive.parent.parent / "extracted.md"
        extracted.write_text(text, encoding="utf-8")
        sections = item.get("sections")
        if not isinstance(sections, list) or not sections:
            raise ValueError(f"缺少已归类的 sections: {source}")
        merged = []
        for section in sections:
            if not isinstance(section, dict) or not all(isinstance(section.get(k), str) and section[k].strip()
                                                       for k in ("destination", "heading", "content")):
                raise ValueError("章节合并项必须包含非空 destination、heading 和 content")
            content = section["content"].replace("\r\n", "\n").strip()
            if content not in text:
                raise ValueError(f"合并摘录不能在原文中找到: {source} / {section['heading']}")
            target = destination_path(section["destination"], staging)
            merge_section(target, section["heading"], content, archive)
            merged.append({"destination": section["destination"], "heading": section["heading"],
                           "content_sha256": hashlib.sha256(content.encode()).hexdigest()})
        records.append({"source": str(source), "source_sha256": checksum,
                        "archived_original": archive.relative_to(staging).as_posix(),
                        "extracted_text": extracted.relative_to(staging).as_posix(),
                        "text_source_sha256": hashlib.sha256(text_source.read_bytes()).hexdigest(),
                        "merged_sections": merged})
    manifest = staging / ".agents/import-manifest.json"
    manifest.write_text(json.dumps({"documents": records}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return len(records)

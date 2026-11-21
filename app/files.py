import csv
import io
import os
import stat
from pathlib import Path, PurePosixPath
from fastapi import HTTPException

SKIP = {".git", "node_modules", "__pycache__", ".venv", ".opencode"}
MAX_UPLOAD = 25 * 1024 * 1024


def workspace(config, sid):
    return config.data_dir / "sessions" / sid / "workspace"


def safe_path(root: Path, name: str):
    path = PurePosixPath(name)
    if not name or "\\" in name or "\x00" in name or path.is_absolute() or any(p in ("..", ".git") or p.startswith(".") for p in path.parts):
        raise HTTPException(400, "无效的文件路径")
    target = root / str(path)
    current = root
    for part in path.parts:
        current /= part
        if current.is_symlink():
            raise HTTPException(400, "不允许访问符号链接")
    try:
        target.resolve().relative_to(root.resolve())
    except ValueError:
        raise HTTPException(400, "文件路径超出当前会话")
    return target


def inventory(root):
    result = []
    if not root.exists():
        return result
    for base, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = sorted(d for d in dirs if not d.startswith(".") and d not in SKIP and not (Path(base) / d).is_symlink())
        for name in sorted(files):
            p = Path(base) / name
            if name.startswith(".") or p.is_symlink():
                continue
            try:
                info = p.stat()
                if not stat.S_ISREG(info.st_mode):
                    continue
                result.append({"path": p.relative_to(root).as_posix(), "name": name, "size": info.st_size,
                               "modified_at": info.st_mtime, "source": "upload" if p.relative_to(root).parts[0] == "uploads" else "output"})
            except FileNotFoundError:
                continue
            if len(result) >= 1000:
                return result
    return result


def preview(path):
    if not path.is_file():
        raise HTTPException(404, "文件不存在")
    size = path.stat().st_size
    suffix = path.suffix.lower()
    if suffix in {".png", ".jpg", ".jpeg", ".gif", ".webp"}:
        return {"kind": "image", "content": "", "size": size, "truncated": False}
    if size > MAX_UPLOAD:
        return {"kind": "binary", "content": "文件较大，请下载查看", "size": size, "truncated": False}
    try:
        if suffix == ".pdf":
            from pypdf import PdfReader
            pages = PdfReader(path).pages
            content = "\n\n".join((p.extract_text() or "") for p in pages[:30])
            kind, truncated = "text", len(pages) > 30
        elif suffix == ".docx":
            from docx import Document
            content = "\n".join(p.text for p in Document(path).paragraphs)
            kind, truncated = "text", False
        elif suffix == ".xlsx":
            from openpyxl import load_workbook
            book = load_workbook(path, read_only=True, data_only=True)
            try:
                rows = list(book.active.iter_rows(min_row=1, max_row=51, values_only=True))
                content = "\n".join("\t".join(str(v) if v is not None else "" for v in r) for r in rows)
            finally:
                book.close()
            kind, truncated = "text", len(rows) >= 51
        else:
            raw = path.open("rb").read(200_001)
            if b"\0" in raw:
                return {"kind": "binary", "content": "此格式支持下载查看", "size": size, "truncated": False}
            content = raw.decode("utf-8", errors="replace")
            kind = "markdown" if suffix in {".md", ".markdown"} else "html" if suffix in {".html", ".htm"} else "text"
            truncated = size > 200_000
        result = {"kind": kind, "content": content[:200_000], "size": size, "truncated": truncated or len(content) > 200_000}
        if suffix == ".csv":
            result.update(kind="csv", rows=list(csv.reader(io.StringIO(content[:200_000])))[:51])
        return result
    except Exception as exc:
        raise HTTPException(422, "无法预览该文件，请下载查看") from exc

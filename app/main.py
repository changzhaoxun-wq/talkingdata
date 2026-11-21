import asyncio
import json
import os
import re
import shutil
import tempfile
import time
import zipfile
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .config import Config
from .db import DB, uid
from .files import MAX_UPLOAD, inventory, preview, safe_path, workspace
from .runner import RunManager

ACTIVE = ("queued", "running")


class SessionInput(BaseModel):
    title: str = Field(default="新会话", min_length=1, max_length=100)


class MessageInput(BaseModel):
    content: str = Field(min_length=1, max_length=40000)
    attachments: list[str] = Field(default_factory=list, max_length=30)


class SettingsInput(BaseModel):
    base_url: str = Field(min_length=1, max_length=500)
    model: str = Field(default="", max_length=200)
    api_key: str | None = Field(default=None, max_length=500)


def create_app(config=None):
    config = config or Config()
    db = DB(config.data_dir / "workbench.sqlite3")
    locks = {}
    manager = RunManager(db, config)

    @asynccontextmanager
    async def lifespan(app):
        config.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        db.init()
        db.recover()
        yield
        await manager.shutdown()

    app = FastAPI(title="Orbit Agent Workbench", version="1.0.0", lifespan=lifespan)
    app.state.db, app.state.manager, app.state.config = db, manager, config
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1", "[::1]", "testserver"])

    @app.middleware("http")
    async def browser_security(request, call_next):
        origin = request.headers.get("origin")
        if request.url.path.startswith("/api/") and request.method not in ("GET", "HEAD", "OPTIONS"):
            if request.headers.get("sec-fetch-site") == "cross-site":
                return JSONResponse({"detail": "拒绝跨站请求"}, status_code=403)
            if origin and (urlsplit(origin).netloc != request.headers.get("host") or urlsplit(origin).scheme != request.url.scheme):
                return JSONResponse({"detail": "拒绝跨域请求"}, status_code=403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        if not request.url.path.endswith("/files/preview"):
            response.headers["X-Frame-Options"] = "DENY"
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        else:
            response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; frame-src 'self' about:; connect-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'"
        return response

    @app.exception_handler(Exception)
    async def unexpected(request, exc):
        return JSONResponse({"detail": "服务处理失败，请重试；已有工作已保留。"}, status_code=500)

    def session(sid):
        if not re.fullmatch(r"[a-f0-9]{32}", sid):
            raise HTTPException(404, "会话不存在")
        row = db.one("SELECT * FROM sessions WHERE id=?", (sid,))
        if not row:
            raise HTTPException(404, "会话不存在")
        return row

    def lock(sid):
        return locks.setdefault(sid, asyncio.Lock())

    def idle(sid):
        if db.one("SELECT id FROM runs WHERE session_id=? AND status IN ('queued','running')", (sid,)):
            raise HTTPException(409, "当前会话正在执行，请先停止任务或等待完成")

    @app.get("/api/health")
    async def health():
        db.one("SELECT 1")
        return {"ok": True}

    @app.get("/api/settings")
    async def settings():
        return config.public()

    @app.put("/api/settings")
    async def save_settings(body: SettingsInput):
        if db.one("SELECT id FROM runs WHERE status IN ('queued','running')"):
            raise HTTPException(409, "请先等待所有任务结束，再修改模型连接")
        try:
            config.save(body.base_url, body.model, body.api_key)
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        return config.public()

    @app.get("/api/models")
    async def models():
        values = config.connection()
        if not values["api_key"]:
            raise HTTPException(503, "请先配置 API Key")
        try:
            async with httpx.AsyncClient(timeout=25, follow_redirects=False) as client:
                response = await client.get(values["base_url"] + "/models", headers={"Authorization": "Bearer " + values["api_key"]})
                response.raise_for_status()
                names = [str(m["id"]) for m in response.json().get("data", [])]
            if not names:
                raise ValueError("平台没有返回可用模型")
            if not values["model"]:
                config.save(values["base_url"], names[0])
            return {"models": names, "selected": config.connection()["model"]}
        except httpx.HTTPStatusError as exc:
            raise HTTPException(502, f"模型平台返回 HTTP {exc.response.status_code}，请检查临时凭据和地址")
        except (httpx.RequestError, ValueError, KeyError) as exc:
            raise HTTPException(502, config.redact(str(exc))[:500])

    @app.get("/api/sessions")
    async def sessions():
        return db.sessions()

    @app.post("/api/sessions", status_code=201)
    async def create_session(body: SessionInput):
        sid, now = uid(), time.time()
        title = body.title.strip()
        if not title:
            raise HTTPException(400, "请输入会话名称")
        workspace(config, sid).mkdir(parents=True, mode=0o700)
        db.execute("INSERT INTO sessions(id,title,created_at,updated_at) VALUES(?,?,?,?)", (sid, title, now, now))
        return session(sid)

    @app.get("/api/sessions/{sid}")
    async def get_session(sid: str):
        session(sid)
        return db.snapshot(sid)

    @app.patch("/api/sessions/{sid}")
    async def rename_session(sid: str, body: SessionInput):
        session(sid)
        if not body.title.strip():
            raise HTTPException(400, "请输入会话名称")
        db.execute("UPDATE sessions SET title=?,updated_at=? WHERE id=?", (body.title.strip(), time.time(), sid))
        return session(sid)

    @app.delete("/api/sessions/{sid}", status_code=204)
    async def delete_session(sid: str):
        async with lock(sid):
            session(sid)
            idle(sid)
            root = config.data_dir / "sessions" / sid
            if root.exists():
                shutil.rmtree(root)
            db.execute("DELETE FROM sessions WHERE id=?", (sid,))

    @app.post("/api/sessions/{sid}/messages", status_code=202)
    async def send(sid: str, body: MessageInput):
        async with lock(sid):
            row = session(sid)
            idle(sid)
            if not config.public()["configured"]:
                raise HTTPException(503, "请先在模型连接中配置 API Key 和模型")
            if not body.content.strip():
                raise HTTPException(400, "请输入任务内容")
            for name in body.attachments:
                if not safe_path(workspace(config, sid), name).is_file():
                    raise HTTPException(404, "材料文件不存在：" + name)
            rid, now = uid(), time.time()
            with db.connect() as c:
                c.execute("INSERT INTO runs(id,session_id,status,created_at,model) VALUES(?,?,?,?,?)",
                          (rid, sid, "queued", now, config.connection()["model"]))
                c.execute("INSERT INTO messages(id,session_id,run_id,role,content,attachments,created_at) VALUES(?,?,?,?,?,?,?)",
                          (uid(), sid, rid, "user", body.content.strip(), json.dumps(body.attachments), now))
                title = body.content.strip().replace("\n", " ")[:28] if row["title"] == "新会话" else row["title"]
                c.execute("UPDATE sessions SET title=?,updated_at=? WHERE id=?", (title, now, sid))
            db.event(sid, rid, "status", {"status": "queued"})
            manager.launch(sid, rid, body.content.strip(), body.attachments)
            return {"id": rid, "session_id": sid, "status": "queued"}

    @app.post("/api/sessions/{sid}/runs/{rid}/cancel")
    async def cancel(sid: str, rid: str):
        session(sid)
        run = db.one("SELECT * FROM runs WHERE id=? AND session_id=?", (rid, sid))
        if not run:
            raise HTTPException(404, "执行记录不存在")
        await manager.cancel(rid)
        return db.one("SELECT * FROM runs WHERE id=?", (rid,))

    @app.get("/api/sessions/{sid}/events")
    async def events(sid: str, request: Request, after: int = 0):
        session(sid)
        try:
            cursor = max(0, after, int(request.headers.get("last-event-id", "0")))
        except ValueError:
            raise HTTPException(400, "无效的事件游标")

        async def stream():
            nonlocal cursor
            ticks = 0
            while not await request.is_disconnected():
                rows = db.all("SELECT * FROM events WHERE session_id=? AND id>? ORDER BY id LIMIT 100", (sid, cursor))
                for event in rows:
                    cursor = event["id"]
                    event["data"] = json.loads(event["data"])
                    yield f"id: {cursor}\nevent: update\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
                ticks += 1
                if ticks % 20 == 0:
                    yield ": heartbeat\n\n"
                await asyncio.sleep(0.25)

        return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.get("/api/sessions/{sid}/files")
    async def files(sid: str):
        session(sid)
        items = await asyncio.to_thread(inventory, workspace(config, sid))
        return {"files": items, "limit": 1000, "truncated": len(items) >= 1000}

    @app.post("/api/sessions/{sid}/files", status_code=201)
    async def upload(sid: str, files: list[UploadFile] = File(...)):
        async with lock(sid):
            session(sid)
            idle(sid)
            if len(files) > 20:
                raise HTTPException(400, "每次最多上传 20 个文件")
            root = workspace(config, sid)
            target = root / "uploads"
            safe_path(root, "uploads")
            target.mkdir(exist_ok=True)
            stage = Path(tempfile.mkdtemp(prefix="upload-", dir=config.data_dir))
            names, total = [], 0
            try:
                for i, file in enumerate(files):
                    name = file.filename or "material.txt"
                    if name.startswith(".") or "/" in name or "\\" in name or len(name) > 180 or any(ord(c) < 32 for c in name):
                        raise HTTPException(400, "请使用不含路径的普通文件名")
                    path = safe_path(root, "uploads/" + name)
                    counter = 1
                    while path.exists() or path.name in names:
                        counter += 1
                        path = safe_path(root, f"uploads/{Path(name).stem}-{counter}{Path(name).suffix}")
                    names.append(path.name)
                    length = 0
                    with (stage / str(i)).open("wb") as out:
                        while chunk := await file.read(1024 * 1024):
                            length += len(chunk)
                            total += len(chunk)
                            if length > MAX_UPLOAD or total > 100 * 1024 * 1024:
                                raise HTTPException(413, "单个文件最大 25 MB，每次合计最大 100 MB")
                            out.write(chunk)
                for i, name in enumerate(names):
                    (stage / str(i)).replace(target / name)
            finally:
                shutil.rmtree(stage, ignore_errors=True)
                for file in files:
                    await file.close()
            db.execute("UPDATE sessions SET updated_at=? WHERE id=?", (time.time(), sid))
            paths = ["uploads/" + name for name in names]
            db.event(sid, None, "files", {"uploaded": paths})
            return {"paths": paths}

    @app.get("/api/sessions/{sid}/files/content")
    async def content(sid: str, path: str):
        session(sid)
        target = safe_path(workspace(config, sid), path)
        return await asyncio.to_thread(preview, target)

    @app.get("/api/sessions/{sid}/files/download")
    async def download(sid: str, path: str):
        session(sid)
        target = safe_path(workspace(config, sid), path)
        if not target.is_file():
            raise HTTPException(404, "文件不存在")
        return FileResponse(target, filename=target.name, headers={"Content-Security-Policy": "sandbox; default-src 'none'"})

    @app.get("/api/sessions/{sid}/files/preview")
    async def html_preview(sid: str, path: str):
        session(sid)
        target = safe_path(workspace(config, sid), path)
        if not target.is_file():
            raise HTTPException(404, "文件不存在")
        if target.suffix.lower() not in (".html", ".htm") or target.stat().st_size > 1024 * 1024:
            raise HTTPException(415, "仅支持预览 1 MB 以下的 HTML 文件")
        policy = "sandbox allow-scripts allow-downloads; default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data:; connect-src 'none'; form-action 'none'; base-uri 'none'; frame-ancestors 'self'"
        return HTMLResponse(target.read_text(encoding="utf-8", errors="replace"), headers={"Content-Security-Policy": policy})

    @app.get("/api/sessions/{sid}/archive")
    async def archive(sid: str):
        session(sid)
        root = workspace(config, sid)
        items = await asyncio.to_thread(inventory, root)
        if len(items) >= 1000 or sum(f["size"] for f in items) > 150 * 1024 * 1024:
            raise HTTPException(413, "工作区过大，请分别下载文件")
        fd, name = tempfile.mkstemp(suffix=".zip", dir=config.data_dir)
        os.close(fd)
        def pack():
            with zipfile.ZipFile(name, "w", zipfile.ZIP_DEFLATED) as out:
                for item in items:
                    p = safe_path(root, item["path"])
                    if p.is_file():
                        out.write(p, item["path"])
        try:
            await asyncio.to_thread(pack)
        except Exception:
            Path(name).unlink(missing_ok=True)
            raise
        return FileResponse(name, filename="orbit-workspace.zip", background=BackgroundTask(Path(name).unlink), media_type="application/zip")

    web = Path(__file__).resolve().parent.parent / "web"
    if web.exists():
        app.mount("/", StaticFiles(directory=web, html=True), name="web")
    return app


app = create_app()

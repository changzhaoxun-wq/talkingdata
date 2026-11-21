import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

DEFAULT_BASE = "https://showmevibecode.up.railway.app/api/model/v1"


@dataclass
class Config:
    data_dir: Path = field(default_factory=lambda: Path(os.getenv("DATA_DIR", "/data")))
    agent_bin: str = field(default_factory=lambda: os.getenv("AGENT_BIN", "opencode"))
    concurrency: int = field(default_factory=lambda: max(1, min(8, int(os.getenv("MAX_CONCURRENT_RUNS", "2")))))
    timeout: int = field(default_factory=lambda: max(5, int(os.getenv("RUN_TIMEOUT_SECONDS", "900"))))

    def connection(self):
        values = {
            "base_url": os.getenv("AGENT_BASE_URL", DEFAULT_BASE).rstrip("/"),
            "api_key": os.getenv("AGENT_API_KEY", ""),
            "model": os.getenv("AGENT_MODEL", ""),
        }
        path = self.data_dir / "settings.json"
        if path.exists():
            values.update(json.loads(path.read_text()))
        return values

    def public(self):
        values = self.connection()
        return {
            "base_url": values["base_url"], "model": values["model"],
            "has_key": bool(values["api_key"]),
            "configured": bool(values["api_key"] and values["model"]),
            "agent": "OpenCode", "agent_version": "1.2.27",
            "max_concurrent_runs": self.concurrency, "timeout_seconds": self.timeout,
            "max_upload_bytes": 25 * 1024 * 1024,
        }

    def save(self, base_url, model, api_key=None):
        parsed = urlsplit(base_url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.query or parsed.fragment:
            raise ValueError("请输入有效的模型 API 地址，不含凭据、查询参数或片段")
        values = self.connection()
        values.update(base_url=base_url.rstrip("/"), model=model.strip())
        if api_key is not None:
            values["api_key"] = api_key.strip()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        tmp = self.data_dir / "settings.tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(values, f)
        tmp.replace(self.data_dir / "settings.json")

    def redact(self, text, key=""):
        for secret in {key, self.connection()["api_key"], os.getenv("AGENT_API_KEY", "")}:
            if secret:
                text = text.replace(secret, "[REDACTED]")
        return re.sub(r"\b(?:exam_|sk-)[A-Za-z0-9_-]{12,}", "[REDACTED]", text)

#!/usr/local/bin/python
"""Deterministic CLI double for lifecycle tests; never used in the application."""
import json
import os
import pathlib
import subprocess
import sys
import time

prompt = sys.stdin.read()
sid = "ses_" + pathlib.Path.cwd().parent.name


def emit(kind, **data):
    print(json.dumps({"type": kind, "sessionID": sid, **data}), flush=True)


emit("step_start", part={})
if prompt.startswith("TRUNCATE"):
    emit("text", part={"id": "partial", "text": "这是尚未完成的过程说明"})
    emit("step_finish", part={"reason": "length", "tokens": {"output": 8192}})
elif prompt.startswith("WAIT"):
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(90)"])
    pathlib.Path("child.pid").write_text(str(child.pid))
    time.sleep(90)
elif prompt.startswith("FAIL"):
    config = json.loads(os.environ["OPENCODE_CONFIG_CONTENT"])
    key = config["provider"]["workbench"]["options"]["apiKey"]
    emit("error", error={"data": {"message": "upstream failure " + key}})
else:
    pathlib.Path("result.md").write_text("# Result\n" + prompt)
    emit("tool_use", part={"tool": "write", "state": {"status": "completed", "input": {"filePath": "result.md"}, "output": "ok"}})
    emit("text", part={"id": "text_1", "text": "已完成：[结果](result.md)"})
    emit("step_finish", part={"reason": "stop", "tokens": {"input": 10, "output": 5}})

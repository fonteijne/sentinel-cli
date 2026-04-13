"""
Sentinel Command Center — FastAPI Backend

Provides REST API and WebSocket endpoints for the dashboard.
Wraps sentinel CLI commands via subprocess and reads/writes
config.yaml / config.local.yaml from the config directory.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import yaml
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# ── Logging ────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
log = logging.getLogger("sentinel.dashboard")

# ── Paths ───────────────────────────────────────────────────────────────────
APP_DIR = Path(__file__).parent.parent.parent  # sentinel-cli root
CONFIG_DIR = APP_DIR / "config"
CONFIG_YAML = CONFIG_DIR / "config.yaml"
CONFIG_LOCAL_YAML = CONFIG_DIR / "config.local.yaml"
SENTINEL_CMD = shutil.which("sentinel") or "sentinel"

# Static files directory (built frontend)
STATIC_DIR = Path(__file__).parent.parent / "frontend" / "dist"

# ── App setup ───────────────────────────────────────────────────────────────
app = FastAPI(
    title="Sentinel Command Center",
    version="1.0.0",
    docs_url="/api/docs",
    redoc_url="/api/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── WebSocket connection manager ────────────────────────────────────────────
class LogBroadcaster:
    def __init__(self) -> None:
        self.clients: list[WebSocket] = []

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self.clients.append(ws)
        log.info("WebSocket client connected (%d total)", len(self.clients))

    def disconnect(self, ws: WebSocket) -> None:
        self.clients.remove(ws)
        log.info("WebSocket client disconnected (%d remaining)", len(self.clients))

    async def broadcast(self, message: dict) -> None:
        dead: list[WebSocket] = []
        for ws in self.clients:
            try:
                await ws.send_json(message)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.clients.remove(ws)


broadcaster = LogBroadcaster()


# ── Config helpers ───────────────────────────────────────────────────────────
def _load_config() -> dict:
    """Load config.yaml (and optionally config.local.yaml) and deep-merge them."""
    config: dict = {}
    if CONFIG_YAML.exists():
        with CONFIG_YAML.open() as f:
            base = yaml.safe_load(f) or {}
        config.update(base)
    if CONFIG_LOCAL_YAML.exists():
        with CONFIG_LOCAL_YAML.open() as f:
            local = yaml.safe_load(f) or {}
        _deep_merge(config, local)
    return config


def _deep_merge(base: dict, override: dict) -> dict:
    for k, v in override.items():
        if k in base and isinstance(base[k], dict) and isinstance(v, dict):
            _deep_merge(base[k], v)
        else:
            base[k] = v
    return base


def _config_raw() -> str:
    if CONFIG_YAML.exists():
        return CONFIG_YAML.read_text()
    return ""


# ── Subprocess helper ────────────────────────────────────────────────────────
async def _run_sentinel(args: list[str], timeout: int = 300) -> dict:
    """Run a sentinel CLI command and stream output to WebSocket clients."""
    cmd = [SENTINEL_CMD] + args
    log.info("Running: %s", " ".join(cmd))

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        cwd=str(APP_DIR),
    )

    output_lines: list[str] = []
    assert proc.stdout is not None

    async def read_output() -> None:
        async for raw_line in proc.stdout:
            line = raw_line.decode(errors="replace").rstrip()
            output_lines.append(line)
            now = datetime.now()
            await broadcaster.broadcast(
                {
                    "id": id(line),
                    "timestamp": now.strftime("%H:%M:%S"),
                    "level": _classify_level(line),
                    "name": f"sentinel.{args[0] if args else 'cli'}",
                    "message": line,
                }
            )

    try:
        await asyncio.wait_for(read_output(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        return {"success": False, "output": output_lines, "error": "Timeout"}

    returncode = await proc.wait()
    return {
        "success": returncode == 0,
        "output": output_lines,
        "returncode": returncode,
    }


def _classify_level(line: str) -> str:
    u = line.upper()
    if "ERROR" in u or "EXCEPTION" in u or "TRACEBACK" in u:
        return "ERROR"
    if "WARNING" in u or "WARN" in u:
        return "WARNING"
    if "DEBUG" in u:
        return "DEBUG"
    return "INFO"


# ── Pydantic models ──────────────────────────────────────────────────────────
class ProjectCreate(BaseModel):
    key: str
    name: Optional[str] = None
    git_url: str
    default_branch: str = "main"
    stack_type: str = "unknown"


class ProjectUpdate(ProjectCreate):
    pass


class ConfigUpdate(BaseModel):
    raw: str


# ── Routes ───────────────────────────────────────────────────────────────────

# Health ──────────────────────────────────────────────────────────────────────
@app.get("/api/health")
async def health_check():
    return {
        "status": "ok",
        "timestamp": datetime.utcnow().isoformat(),
        "version": "1.0.0",
        "sentinel_available": shutil.which(SENTINEL_CMD) is not None,
    }


# Status ──────────────────────────────────────────────────────────────────────
@app.get("/api/status")
async def get_status():
    """Return connection status derived from config + quick sentinel status."""
    config = _load_config()

    # Derive URLs from env (set in .env / .env.local)
    jira_url = os.getenv("JIRA_URL", "")
    gitlab_url = os.getenv("GITLAB_URL", "")
    llm_mode = os.getenv("CLAUDE_MODE", config.get("llm", {}).get("mode", ""))

    # Status: "ok" | "error" | "warning" | "unconfigured"
    def _presence(val: str) -> str:
        return "ok" if val else "unconfigured"

    return {
        "jira": _presence(jira_url),
        "jira_url": jira_url,
        "gitlab": _presence(gitlab_url),
        "gitlab_url": gitlab_url,
        "llm": _presence(os.getenv("ANTHROPIC_API_KEY", "") or llm_mode),
        "llm_mode": llm_mode or "not configured",
        "ssh": "ok" if Path.home().joinpath(".ssh").exists() else "unconfigured",
        "beads": "unconfigured",
        "stats": {
            "active_projects": len(config.get("projects", {})),
            "active_tickets": 0,
            "security_score": 94,
            "agent_runs_today": 0,
        },
    }


@app.get("/api/status/validate")
async def validate_connections():
    """Run sentinel validate and parse output."""
    result = await _run_sentinel(["validate"])
    output_text = "\n".join(result.get("output", []))

    def _parse(keyword: str) -> str:
        for line in result.get("output", []):
            if keyword.lower() in line.lower():
                if "✓" in line or "ok" in line.lower() or "success" in line.lower():
                    return "ok"
                if "✗" in line or "fail" in line.lower() or "error" in line.lower():
                    return "error"
        return "unknown"

    return {
        "jira": _parse("jira"),
        "gitlab": _parse("gitlab"),
        "llm": _parse("llm") or _parse("claude"),
        "ssh": _parse("ssh"),
        "beads": _parse("beads"),
        "raw_output": result.get("output", []),
        "success": result.get("success", False),
    }


# Projects ────────────────────────────────────────────────────────────────────
@app.get("/api/projects")
async def list_projects():
    config = _load_config()
    projects_cfg = config.get("projects", {})
    projects = []
    for key, proj in projects_cfg.items():
        projects.append(
            {
                "key": key,
                "name": proj.get("name", key),
                "git_url": proj.get("git_url", ""),
                "default_branch": proj.get("default_branch", "main"),
                "stack_type": proj.get("stack_type", "unknown"),
                "worktree_count": _count_worktrees(key),
            }
        )
    return projects


@app.post("/api/projects", status_code=201)
async def create_project(project: ProjectCreate):
    config = _load_config()
    if "projects" not in config:
        config["projects"] = {}
    if project.key in config.get("projects", {}):
        raise HTTPException(status_code=409, detail=f"Project {project.key} already exists")

    config["projects"][project.key] = {
        "name": project.name or project.key,
        "git_url": project.git_url,
        "default_branch": project.default_branch,
        "stack_type": project.stack_type,
    }
    _save_config(config)
    return {**project.model_dump(), "worktree_count": 0}


@app.put("/api/projects/{key}")
async def update_project(key: str, project: ProjectUpdate):
    config = _load_config()
    if key not in config.get("projects", {}):
        raise HTTPException(status_code=404, detail=f"Project {key} not found")
    config["projects"][key] = {
        "name": project.name or key,
        "git_url": project.git_url,
        "default_branch": project.default_branch,
        "stack_type": project.stack_type,
    }
    _save_config(config)
    return {**project.model_dump(), "worktree_count": _count_worktrees(key)}


@app.delete("/api/projects/{key}")
async def delete_project(key: str):
    config = _load_config()
    if key not in config.get("projects", {}):
        raise HTTPException(status_code=404, detail=f"Project {key} not found")
    del config["projects"][key]
    _save_config(config)
    return {"deleted": key}


@app.post("/api/projects/{key}/profile")
async def generate_profile(key: str, refresh: bool = False):
    args = ["projects", "profile", key]
    if refresh:
        args.append("--refresh")
    result = await _run_sentinel(args, timeout=120)
    return {"key": key, "success": result.get("success"), "output": result.get("output", [])}


# Tickets ─────────────────────────────────────────────────────────────────────
@app.get("/api/tickets/{ticket_id}/info")
async def ticket_info(ticket_id: str):
    result = await _run_sentinel(["info", ticket_id])
    return {"ticket_id": ticket_id, "output": result.get("output", []), "success": result.get("success")}


@app.post("/api/tickets/{ticket_id}/plan")
async def plan_ticket(ticket_id: str):
    result = await _run_sentinel(["plan", ticket_id], timeout=300)
    return {"ticket_id": ticket_id, "output": result.get("output", []), "success": result.get("success")}


@app.post("/api/tickets/{ticket_id}/execute")
async def execute_ticket(ticket_id: str, revise: bool = False):
    args = ["execute", ticket_id]
    if revise:
        args.append("--revise")
    result = await _run_sentinel(args, timeout=600)
    return {"ticket_id": ticket_id, "output": result.get("output", []), "success": result.get("success")}


@app.post("/api/tickets/{ticket_id}/debrief")
async def debrief_ticket(ticket_id: str):
    result = await _run_sentinel(["debrief", ticket_id], timeout=300)
    return {"ticket_id": ticket_id, "output": result.get("output", []), "success": result.get("success")}


# Config ──────────────────────────────────────────────────────────────────────
@app.get("/api/config")
async def get_config():
    config = _load_config()
    return {
        "config": config,
        "raw": _config_raw(),
        "path": str(CONFIG_YAML),
    }


@app.put("/api/config")
async def update_config(update: ConfigUpdate):
    # Validate YAML before saving
    try:
        parsed = yaml.safe_load(update.raw)
    except yaml.YAMLError as e:
        raise HTTPException(status_code=422, detail=f"Invalid YAML: {e}")
    if not CONFIG_YAML.exists():
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_YAML.write_text(update.raw)
    log.info("Config saved to %s", CONFIG_YAML)
    return {"saved": True, "path": str(CONFIG_YAML)}


# Agents ──────────────────────────────────────────────────────────────────────
@app.get("/api/agents")
async def get_agents():
    config = _load_config()
    agents_cfg = config.get("agents", {})
    return agents_cfg


# WebSocket — log streaming ───────────────────────────────────────────────────
@app.websocket("/ws/logs")
async def websocket_logs(ws: WebSocket):
    await broadcaster.connect(ws)
    try:
        # Keep alive — receive pings from client
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        broadcaster.disconnect(ws)
    except Exception:
        try:
            broadcaster.disconnect(ws)
        except Exception:
            pass


# ── Static file serving ──────────────────────────────────────────────────────
if STATIC_DIR.exists():
    # Serve React SPA
    app.mount("/assets", StaticFiles(directory=str(STATIC_DIR / "assets")), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa_fallback(full_path: str):
        # API routes are handled above — everything else serves the SPA
        index = STATIC_DIR / "index.html"
        if index.exists():
            return FileResponse(str(index))
        return JSONResponse({"error": "Frontend not built yet"}, status_code=503)


# ── Utility functions ────────────────────────────────────────────────────────
def _count_worktrees(project_key: str) -> int:
    """Count active git worktrees for a project."""
    config = _load_config()
    workspace_root = Path(
        os.path.expanduser(config.get("workspace", {}).get("root_dir", "~/sentinel-workspaces"))
    )
    project_dir = workspace_root / project_key.lower()
    if not project_dir.exists():
        return 0
    try:
        result = subprocess.run(
            ["git", "worktree", "list", "--porcelain"],
            cwd=str(project_dir),
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            return max(0, result.stdout.count("worktree") - 1)  # -1 for main
    except Exception:
        pass
    return 0


def _save_config(config: dict) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with CONFIG_YAML.open("w") as f:
        yaml.dump(config, f, default_flow_style=False, allow_unicode=True, sort_keys=False)


# ── Entry point ──────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True, log_level="info")

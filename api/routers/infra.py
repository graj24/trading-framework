"""Infra status endpoint — returns live data about EC2 instances and services."""
from __future__ import annotations

import os
import subprocess
from datetime import datetime, timezone

from fastapi import APIRouter

router = APIRouter(prefix="/api/infra", tags=["infra"])


def _imds(path: str) -> str:
    """Fetch EC2 instance metadata using IMDSv2."""
    try:
        token = subprocess.check_output(
            'curl -sf --max-time 2 -X PUT "http://169.254.169.254/latest/api/token" '
            '-H "X-aws-ec2-metadata-token-ttl-seconds: 21600"',
            shell=True, text=True, timeout=5,
        ).strip()
        return subprocess.check_output(
            f'curl -sf --max-time 2 -H "X-aws-ec2-metadata-token: {token}" '
            f'http://169.254.169.254/latest/meta-data/{path}',
            shell=True, text=True, timeout=5,
        ).strip()
    except Exception:
        return "unknown"


def _service_status(name: str) -> str:
    out = _run(f"systemctl is-active {name} 2>/dev/null")
    return out if out else "unknown"


@router.get("")
def get_infra():
    """Return live infra status — runs on the trading EC2 itself."""
    # Disk
    disk_raw = _run("df -h / | tail -1")
    disk_parts = disk_raw.split()
    disk = {
        "total": disk_parts[1] if len(disk_parts) > 1 else "?",
        "used": disk_parts[2] if len(disk_parts) > 2 else "?",
        "free": disk_parts[3] if len(disk_parts) > 3 else "?",
        "pct": disk_parts[4] if len(disk_parts) > 4 else "?",
    }

    # Memory (MB)
    mem_raw = _run("free -m | grep Mem")
    mem_parts = mem_raw.split()
    memory = {
        "total_mb": int(mem_parts[1]) if len(mem_parts) > 1 else 0,
        "used_mb": int(mem_parts[2]) if len(mem_parts) > 2 else 0,
        "free_mb": int(mem_parts[3]) if len(mem_parts) > 3 else 0,
    }

    # Uptime
    uptime = _run("uptime -p")

    # Load average
    load = _run("cat /proc/loadavg").split()[:3]

    # Services
    services = {
        "trading-daemon": _service_status("trading-daemon"),
        "trading-api": _service_status("trading-api"),
        "nginx": _service_status("nginx"),
    }

    # Multica daemon
    multica_raw = _run("multica daemon status 2>/dev/null | head -3")
    multica_lines = multica_raw.splitlines()
    multica = {
        "status": "running" if "running" in multica_raw else "stopped",
        "agents": "",
        "workspaces": "",
    }
    for line in multica_lines:
        if line.startswith("Agents:"):
            multica["agents"] = line.split(":", 1)[1].strip()
        elif line.startswith("Workspaces:"):
            multica["workspaces"] = line.split(":", 1)[1].strip()

    # Git info
    git_commit = _run("cd /app && git log --oneline -1 2>/dev/null")
    git_branch = _run("cd /app && git rev-parse --abbrev-ref HEAD 2>/dev/null")

    # Instance metadata (IMDSv2)
    instance_id   = _imds("instance-id")
    instance_type = _imds("instance-type")
    public_ip     = _imds("public-ipv4")
    region        = _imds("placement/region")

    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "instance": {
            "id": instance_id,
            "type": instance_type,
            "public_ip": public_ip,
            "region": region,
        },
        "system": {
            "uptime": uptime,
            "load": load,
            "disk": disk,
            "memory": memory,
        },
        "services": services,
        "multica": multica,
        "deploy": {
            "branch": git_branch,
            "commit": git_commit,
        },
    }

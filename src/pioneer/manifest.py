"""[INDEPENDENT] What a run needs to be reproduced, written next to its outputs.

Code version (and whether the checkout had uncommitted changes), the config text, the
world model and tau2 versions, the command line, and what each model endpoint reports
about the model it serves. The API key is never recorded.
"""
import hashlib
import json
import os
import platform
import subprocess
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from pioneer.paths import REPO_ROOT


def _git(*args, cwd=REPO_ROOT):
    try:
        return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=30).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None


def _sha256(path: Path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def _get(url, timeout=5):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return json.loads(response.read())
    except Exception as exc:  # an unreachable endpoint is recorded, not fatal
        return dict(error=f"{type(exc).__name__}: {exc}")


def served_models(api_base):
    """For each comma-separated base: its /models listing (id, max_model_len) and vLLM /version."""
    out = []
    for base in [b.strip() for b in (api_base or "").split(",") if b.strip()]:
        root = base[:-3] if base.rstrip("/").endswith("/v1") else base
        out.append(dict(api_base=base, models=_get(base.rstrip("/") + "/models"), version=_get(root.rstrip("/") + "/version")))
    return out


def build_manifest(config_path: Path, argv=None, api_base=None):
    config_path = Path(config_path)
    config_file = config_path if config_path.is_absolute() else REPO_ROOT / config_path
    status = _git("status", "--porcelain", "--untracked-files=no")
    from importlib.metadata import PackageNotFoundError, version
    packages = {}
    for name in ("litellm", "tau2", "pioneer"):
        try:
            packages[name] = version(name)
        except PackageNotFoundError:
            packages[name] = None
    return dict(
        utc=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        argv=list(argv if argv is not None else sys.argv), cwd=os.getcwd(), host=platform.node(),
        python=sys.version.split()[0], packages=packages,
        git=dict(commit=_git("rev-parse", "HEAD"), branch=_git("rev-parse", "--abbrev-ref", "HEAD"),
                 uncommitted_changes=bool(status) if status is not None else None, changed=status.splitlines() if status else []),
        tau2_commit=_git("rev-parse", "HEAD", cwd=REPO_ROOT / "third_party/tau2-bench"),
        config=dict(path=str(config_path), sha256=_sha256(config_file),
                    text=config_file.read_text(encoding="utf-8") if config_file.exists() else None),
        world_model_sha256=_sha256(REPO_ROOT / "data/knowledge/retail_world_model.json"),
        holdout_sha256=_sha256(REPO_ROOT / "data/holdout/users.json"),
        served_models=served_models(api_base if api_base is not None else os.environ.get("LITELLM_API_BASE")),
    )


def write_manifest(out: Path, config_path: Path, **kwargs) -> dict:
    """Append this invocation to <out>/manifest.json (a resumed run adds a second entry)."""
    path = Path(out) / "manifest.json"
    runs = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
    entry = build_manifest(config_path, **kwargs)
    runs.append(entry)
    path.write_text(json.dumps(runs, ensure_ascii=False, indent=2), encoding="utf-8")
    return entry

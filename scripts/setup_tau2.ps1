# Fetch tau2-bench v1.0.1 (pinned) into third_party/tau2-bench using a blobless,
# sparse checkout. A full clone is impractically large because of voice data.
# Usage (from repo root):  powershell -ExecutionPolicy Bypass -File scripts/setup_tau2.ps1
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Dest = Join-Path $Root "third_party\tau2-bench"
$Tag = "v1.0.1"
$Commit = "fc0055dc4e0a316c3f83133267fbd6faaa770992"

if (-not (Test-Path (Join-Path $Dest ".git"))) {
    New-Item -ItemType Directory -Force (Split-Path $Dest) | Out-Null
    git clone --filter=blob:none --no-checkout --depth 1 --branch $Tag https://github.com/sierra-research/tau2-bench.git $Dest
}
git -C $Dest sparse-checkout set --no-cone /pyproject.toml /README.md /LICENSE /src/ /data/tau2/domains/ /data/tau2/user_simulator/
git -C $Dest checkout --quiet
$Head = (git -C $Dest rev-parse HEAD).Trim()
if ($Head -ne $Commit) { throw "tau2-bench HEAD $Head != pinned $Commit" }
Write-Host "tau2-bench $Tag checked out at $Head -> $Dest"

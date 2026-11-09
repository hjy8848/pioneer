#!/usr/bin/env bash
# Fetch tau2-bench v1.0.1 (pinned) into third_party/tau2-bench using a blobless,
# sparse checkout. A full clone is impractically large because of voice data.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DEST="$ROOT/third_party/tau2-bench"
TAG="v1.0.1"
COMMIT="fc0055dc4e0a316c3f83133267fbd6faaa770992"

if [ ! -d "$DEST/.git" ]; then
  mkdir -p "$(dirname "$DEST")"
  git clone --filter=blob:none --no-checkout --depth 1 --branch "$TAG" \
    https://github.com/sierra-research/tau2-bench.git "$DEST"
fi
git -C "$DEST" sparse-checkout set --no-cone /pyproject.toml /README.md /LICENSE /src/ /data/tau2/domains/
git -C "$DEST" checkout --quiet
HEAD="$(git -C "$DEST" rev-parse HEAD)"
[ "$HEAD" = "$COMMIT" ] || { echo "tau2-bench HEAD $HEAD != pinned $COMMIT" >&2; exit 1; }
echo "tau2-bench $TAG checked out at $HEAD -> $DEST"

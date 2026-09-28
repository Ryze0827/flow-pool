#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
if ! command -v python3 >/dev/null 2>&1; then
  echo '请先安装 Python 3.10 或更高版本。'
  exit 1
fi
python3 -c 'import sys; assert sys.version_info >= (3, 10), "需要 Python 3.10+"'
if ! command -v node >/dev/null 2>&1 || ! node -e 'process.exit(Number(process.versions.node.split(".")[0]) >= 18 ? 0 : 1)'; then
  # 兼容已经安装 nvm、但当前 shell 仍指向旧 Node 的环境。
  for node_dir in "$HOME"/.nvm/versions/node/v22*/bin "$HOME"/.nvm/versions/node/v20*/bin; do
    if [[ -x "$node_dir/node" ]]; then
      export PATH="$node_dir:$PATH"
      break
    fi
  done
fi
node -e 'if (Number(process.versions.node.split(".")[0]) < 18) { console.error("需要 Node.js 18+，建议 22 LTS"); process.exit(1) }'
[[ -x .venv/bin/python ]] || python3 -m venv .venv
if [[ ! -f data/requirements.installed ]] || ! cmp -s requirements.txt data/requirements.installed; then
  .venv/bin/python -m pip install -r requirements.txt
  mkdir -p data
  cp requirements.txt data/requirements.installed
fi
cd frontend
if [[ ! -d node_modules ]] || [[ ! -f ../data/package.installed ]] || ! cmp -s package.json ../data/package.installed; then
  if [[ -f package-lock.json ]]; then npm ci --no-audit --no-fund; else npm install --no-audit --no-fund; fi
  cp package.json ../data/package.installed
fi
npm run build

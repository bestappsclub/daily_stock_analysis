#!/usr/bin/env bash
# 本地一键启动 DSA Web 工作台（含美股选股）。
# 用法：在项目目录执行  ./run-local.sh
set -e
cd "$(dirname "$0")"

PORT="${PORT:-8000}"

if [ ! -x ".venv/bin/python" ]; then
  echo "未找到 .venv，请先创建虚拟环境并安装依赖："
  echo "  python3.11 -m venv .venv && .venv/bin/pip install -r requirements.txt"
  exit 1
fi

echo "=========================================="
echo " DSA 本地服务启动中..."
echo " 浏览器打开: http://127.0.0.1:${PORT}"
echo " 停止: 按 Ctrl+C"
echo "=========================================="
exec .venv/bin/python main.py --serve-only --port "${PORT}"

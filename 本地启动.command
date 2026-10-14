#!/bin/sh
task_root=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
if [ ! -x "$task_root/.venv/bin/python" ]; then
    printf '%s\n' '本地 Python 环境不存在，请按 README.md 准备运行环境。'
    exit 1
fi
cd "$task_root" || exit 1
exec "$task_root/.venv/bin/python" "$task_root/launch.py"

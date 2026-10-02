#!/bin/bash
cd "$(dirname "$0")" || exit 1
# Prefer normal macOS/Homebrew/Python.org interpreters over an activated Conda shell,
# but any CPython 3.10+ works because the production runtime has no pip dependencies.
export PATH="/opt/homebrew/bin:/usr/local/bin:/Library/Frameworks/Python.framework/Versions/3.14/bin:/Library/Frameworks/Python.framework/Versions/3.13/bin:/Library/Frameworks/Python.framework/Versions/3.12/bin:/Library/Frameworks/Python.framework/Versions/3.11/bin:$PATH"
unset PYTHONHOME
for py in python3.15 python3.14 python3.13 python3.12 python3.11 python3.10 python3; do
  if command -v "$py" >/dev/null 2>&1 && "$py" -c 'import sys; assert sys.version_info >= (3,10)' >/dev/null 2>&1; then
    printf '正在启动核查工作台……\n'
    "$py" -I -S start.py
    exit $?
  fi
done
printf '\n没有找到 Python 3.10 或更新版本。\n请先安装 Python 3.12、3.13 或 3.14，再双击本文件。\n程序本身不需要 pip 安装依赖，也不会联网下载组件。\n'
read -r -p '按回车关闭。'

#!/bin/bash
# Maintenance helper, only relevant if you previously ran an older build of this
# workbench that installed a WPS add-in. Current builds install nothing into Office:
# exported DOCX files use ordinary Word/WPS tracked changes. Safe to ignore otherwise.
set -e
cd "$(dirname "$0")/.."
PY="$(command -v python3 || true)"
if [ -z "$PY" ]; then
  echo "未找到 Python 3。"
  read -r -p "按回车关闭。" _
  exit 1
fi
"$PY" - <<'PY'
from start import cleanup_legacy_wps_addin
changed,warn=cleanup_legacy_wps_addin()
if changed: print('已清除本项目此前安装的全部 WPS 联动插件、注册项及本项目备份文件。')
elif warn: print('部分 WPS 插件目录无法确认，请完全退出 WPS 后再运行一次。')
else: print('未发现本项目遗留 WPS 插件。')
print('其他第三方 WPS 插件未删除。')
PY
echo "如果 WPS 正在运行，请按 ⌘Q 完全退出后重新打开。"
read -r -p "按回车关闭。" _

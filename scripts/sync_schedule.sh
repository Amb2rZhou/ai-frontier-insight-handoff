#!/bin/bash
# 读取 settings.yaml 中的 send_hour，自动更新 x-monitor 的 launchd 时间
# 用法: bash scripts/sync_schedule.sh

set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
PLIST="$HOME/Library/LaunchAgents/com.x-monitor.pipeline.plist"
SETTINGS="$REPO/config/settings.yaml"

# 从 settings.yaml 解析 send_hour 和 send_minute
SEND_HOUR=$(/usr/bin/python3 -c "
import yaml
with open('$SETTINGS') as f:
    s = yaml.safe_load(f)
print(s['schedule']['daily']['send_hour'])
")
SEND_MINUTE=$(/usr/bin/python3 -c "
import yaml
with open('$SETTINGS') as f:
    s = yaml.safe_load(f)
print(s['schedule']['daily'].get('send_minute', 0))
")

# x-monitor = send_time - 45 分钟
TOTAL_MIN=$(( SEND_HOUR * 60 + SEND_MINUTE - 45 ))
if [ $TOTAL_MIN -lt 0 ]; then
    TOTAL_MIN=$(( TOTAL_MIN + 1440 ))
fi
XMON_HOUR=$(( TOTAL_MIN / 60 ))
XMON_MINUTE=$(( TOTAL_MIN % 60 ))

echo "send_time:    ${SEND_HOUR}:$(printf '%02d' $SEND_MINUTE)"
echo "x-monitor:    ${XMON_HOUR}:$(printf '%02d' $XMON_MINUTE)"

# 检查 plist 是否存在
if [ ! -f "$PLIST" ]; then
    echo "[!] plist not found: $PLIST"
    exit 1
fi

# 更新 plist 中的 Hour 和 Minute
/usr/bin/python3 -c "
import plistlib

with open('$PLIST', 'rb') as f:
    plist = plistlib.load(f)

plist['StartCalendarInterval']['Hour'] = $XMON_HOUR
plist['StartCalendarInterval']['Minute'] = $XMON_MINUTE

with open('$PLIST', 'wb') as f:
    plistlib.dump(plist, f)

print('plist updated')
"

# 重新加载
launchctl unload "$PLIST" 2>/dev/null || true
launchctl load "$PLIST"
echo "launchd reloaded"

# 显示完整时间线
FETCH_OFFSET=$(/usr/bin/python3 -c "
import yaml
with open('$SETTINGS') as f:
    s = yaml.safe_load(f)
print(s['schedule']['daily'].get('fetch_offset_minutes', 30))
")
FETCH_TOTAL=$(( SEND_HOUR * 60 + SEND_MINUTE - FETCH_OFFSET ))
if [ $FETCH_TOTAL -lt 0 ]; then
    FETCH_TOTAL=$(( FETCH_TOTAL + 1440 ))
fi
FETCH_HOUR=$(( FETCH_TOTAL / 60 ))
FETCH_MINUTE=$(( FETCH_TOTAL % 60 ))

echo ""
echo "=== Daily Timeline ==="
echo "  ${XMON_HOUR}:$(printf '%02d' $XMON_MINUTE)  x-monitor 抓取 X 推文"
echo "  ${FETCH_HOUR}:$(printf '%02d' $FETCH_MINUTE)  ai-frontier-insight 采集"
echo "  ${SEND_HOUR}:$(printf '%02d' $SEND_MINUTE)  发送日报"

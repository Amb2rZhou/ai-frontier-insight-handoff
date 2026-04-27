#!/bin/bash
# X Monitor 流水线：抓取推文 → 推送到 ai-frontier-insight 仓库
# 由 launchd 每 1 小时触发一次，脚本自行管理运行时机：
#   - 随机间隔 2~5 小时
#   - 日报前（9:00-9:25）强制运行，保证 9:30 采集前有新数据
#   - 失败自动重试（最多 2 次）
#   - Cookie 过期检测 + 告警

set -uo pipefail

DIR="$(cd "$(dirname "$0")/.." && pwd)"
INSIGHT_REPO="$HOME/ai-frontier-insight"
STATE_FILE="$DIR/data/.next_run"

# 加载环境变量
if [ -f "$DIR/.env" ]; then
    set -a; source "$DIR/.env"; set +a
fi
if [ -f "$INSIGHT_REPO/.env" ]; then
    set -a; source "$INSIGHT_REPO/.env"; set +a
fi
LOG="$DIR/logs/pipeline-$(date +%Y-%m-%d).log"
DATE=$(date +%Y-%m-%d)

mkdir -p "$DIR/logs" "$DIR/data"

log() { echo "[$(date '+%H:%M:%S')] $*" | tee -a "$LOG"; }

alert() {
    local msg="$1"
    if [ -n "${ALERT_WEBHOOK_URL:-}" ]; then
        /usr/bin/curl -s -X POST "$ALERT_WEBHOOK_URL" \
            -H "Content-Type: application/json" \
            -d "{\"msgtype\":\"markdown\",\"markdown\":{\"content\":\"${msg}\"}}" >/dev/null 2>&1 || true
    fi
}

# ── 调度决策 ─────────────────────────────────────────────
NOW=$(date +%s)
NEXT_RUN=$(cat "$STATE_FILE" 2>/dev/null || echo "0")
HOUR=$(date +%-H)
MINUTE=$(date +%-M)

# 日报前窗口：9:00-9:25（紧贴 9:30 采集，尽量抓到最新推文）
FORCE=false
if [ "$HOUR" -eq 9 ] && [ "$MINUTE" -le 25 ]; then
    LAST_RUN_FILE="$DIR/data/.last_run"
    LAST_RUN=$(cat "$LAST_RUN_FILE" 2>/dev/null || echo "0")
    ELAPSED=$(( NOW - LAST_RUN ))
    if [ "$ELAPSED" -gt 3600 ]; then  # 距上次运行超过 1 小时
        FORCE=true
        log "========== 日报前强制运行 =========="
    fi
fi

# 非强制模式：检查是否到了计划运行时间
if [ "$FORCE" = "false" ]; then
    if [ "$NOW" -lt "$NEXT_RUN" ]; then
        WAIT_MIN=$(( (NEXT_RUN - NOW) / 60 ))
        # 静默跳过，不写日志（每小时触发但大部分时间不运行）
        exit 0
    fi
    log "========== 苏醒 =========="
fi

# 随机抖动：日报前窗口内不等待，其他时间 0~5 分钟
if [ "$FORCE" = "true" ]; then
    JITTER=0
else
    JITTER=$((RANDOM % 300))
fi
if [ "$JITTER" -gt 30 ]; then
    log "随机等待 ${JITTER} 秒（$(( JITTER / 60 ))分钟）..."
    sleep "$JITTER"
fi

# ── 抓取（带重试） ───────────────────────────────────────
OUTFILE="$INSIGHT_REPO/data/x-monitor/$DATE.json"
mkdir -p "$(dirname "$OUTFILE")"

MAX_RETRIES=2
RETRY_WAIT=600  # 10 分钟
SUCCESS=false
AUTH_FAILED=false

for attempt in $(seq 0 "$MAX_RETRIES"); do
    if [ "$attempt" -gt 0 ]; then
        log "第 ${attempt} 次重试（等待 $(( RETRY_WAIT / 60 )) 分钟）..."
        sleep "$RETRY_WAIT"
    fi

    log "正在抓取推文..."
    EXIT_CODE=0
    /usr/bin/python3 "$DIR/monitor.py" --pipeline --pipeline-output "$OUTFILE" >> "$LOG" 2>&1 || EXIT_CODE=$?

    if [ "$EXIT_CODE" -eq 0 ]; then
        log "抓取完成"
        SUCCESS=true
        break
    elif [ "$EXIT_CODE" -eq 2 ]; then
        log "Cookie 已过期"
        AUTH_FAILED=true
        break
    else
        log "抓取失败 (exit=$EXIT_CODE)"
    fi
done

# ── 失败处理 ─────────────────────────────────────────────
if [ "$AUTH_FAILED" = "true" ]; then
    alert "**🔑 \[AI Frontier Insight\] x-monitor Cookie 已过期**\n\n需要手动更新 Cookie：\n1. 浏览器打开 x.com → DevTools → Cookies\n2. 复制 \`auth_token\` 和 \`ct0\`\n3. 运行：\`python3 monitor.py --login --auth-token <值> --ct0 <值>\`"
    # Cookie 过期后暂停调度 12 小时，避免反复告警
    echo "$(( NOW + 43200 ))" > "$STATE_FILE"
    exit 2
fi

if [ "$SUCCESS" = "false" ]; then
    alert "**⚠️ \[AI Frontier Insight\] x-monitor 连续 $(( MAX_RETRIES + 1 )) 次抓取失败**\n\n时间：$(date '+%Y-%m-%d %H:%M')\n\n\`$(tail -3 "$LOG" 2>/dev/null)\`"
    # 失败后 1 小时后再试
    echo "$(( NOW + 3600 ))" > "$STATE_FILE"
    exit 1
fi

# ── 推送到 GitHub ────────────────────────────────────────
if [ -f "$OUTFILE" ]; then
    TWEET_COUNT=$(/usr/bin/python3 -c "import json; d=json.load(open('$OUTFILE')); print(d['tweet_count'])")
    log "当日累计 $TWEET_COUNT 条推文，推送到 GitHub..."

    cd "$INSIGHT_REPO"
    /usr/bin/git add -f "data/x-monitor/$DATE.json"
    /usr/bin/git diff --cached --quiet && { log "无变更，跳过推送"; } || {
        /usr/bin/git commit -m "x-monitor: $DATE ($TWEET_COUNT tweets, $(date +%H:%M))"
        /usr/bin/git push
        log "推送完成"
    }
else
    log "未生成 pipeline 文件，跳过"
fi

# ── 调度下一次运行 ───────────────────────────────────────
# 随机间隔 2~5 小时
INTERVAL=$(( 7200 + RANDOM % 10800 ))
NEXT=$(( $(date +%s) + INTERVAL ))
echo "$NEXT" > "$STATE_FILE"

# 记录最后成功运行时间（用于日报前窗口判断）
date +%s > "$DIR/data/.last_run"

NEXT_TIME=$(date -r "$NEXT" '+%H:%M' 2>/dev/null || date -d "@$NEXT" '+%H:%M' 2>/dev/null || echo "?")
log "下次运行: ~${NEXT_TIME}（$(( INTERVAL / 3600 ))h$(( (INTERVAL % 3600) / 60 ))m 后）"

# 清理 7 天前的日志
find "$DIR/logs" -name "pipeline-*.log" -mtime +7 -delete 2>/dev/null || true

log "========== 休眠 =========="

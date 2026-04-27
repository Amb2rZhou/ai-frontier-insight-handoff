#!/bin/bash
# AI Frontier Insight Bot — 每日流水线
# 由 launchd 定时调用：先采集+分析，等到发送时间后推送
#
# 时间线：
#   09:30  launchd 触发本脚本
#   09:30  采集 + AI 分析（约 3-5 分钟）
#   09:35  发送草稿到测试频道供审核
#   09:35  git push 日报 markdown 到 GitHub（供 Red Lobi 拉取）

set -euo pipefail

DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$DIR"
LOG_DIR="$DIR/logs"
mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/daily-$(date +%Y-%m-%d).log"

log() { echo "[$(date '+%H:%M:%S')] $*" | tee -a "$LOG"; }

# 加载环境变量（API keys）
ENV_FILE="$DIR/.env"
if [ -f "$ENV_FILE" ]; then
    set -a
    source "$ENV_FILE"
    set +a
else
    echo "[!] 未找到 .env 文件" >> "$LOG"
    exit 1
fi

# 运维通知（仅关键故障调用）
alert() {
    local msg="$1"
    if [ -n "${ALERT_WEBHOOK_URL:-}" ]; then
        /usr/bin/curl -s -X POST "$ALERT_WEBHOOK_URL" \
            -H "Content-Type: application/json" \
            -d "{\"msgtype\":\"markdown\",\"markdown\":{\"content\":\"${msg}\"}}" >/dev/null 2>&1 || true
    fi
}

log "========== Daily Pipeline 开始 =========="
log "已加载 .env"

# Step 1: 采集 + 分析
log "Step 1: 采集 + 分析..."
if /usr/bin/python3 -m src.main daily >> "$LOG" 2>&1; then
    log "采集分析完成"
else
    log "[!] 采集分析失败"
    alert "**⚠️ \[AI Frontier Insight\] 日报采集/分析失败**\n\n时间：$(date '+%Y-%m-%d %H:%M')\n\n\`$(tail -3 "$LOG" 2>/dev/null)\`"
    exit 1
fi

# Step 2: 发送草稿到测试频道供审核
log "Step 2: 发送草稿到测试频道..."
if /usr/bin/python3 -m src.main send-daily --alert-only >> "$LOG" 2>&1; then
    log "测试频道发送完成，等待审核"
else
    log "[!] 测试频道发送失败"
fi

# Webhook 正式频道发送已停用，改为 Red Lobi 拉取 GitHub markdown
# 保留 send-daily 命令用于手动发送（如需临时恢复）

# Step 3: 提交 draft 和 memory 到 git
log "Step 3: 提交到 Git..."
cd "$DIR"
/usr/bin/git add config/drafts/ memory/ data/daily/ data/weekly/ 2>/dev/null || true
if ! /usr/bin/git diff --cached --quiet 2>/dev/null; then
    /usr/bin/git commit -m "daily: $(date +%Y-%m-%d) brief"
    /usr/bin/git push
    log "Git 提交推送完成"
else
    log "无变更需要提交"
fi

# 清理 7 天前的日志
find "$LOG_DIR" -name "daily-*.log" -mtime +7 -delete 2>/dev/null || true

log "========== Daily Pipeline 结束 =========="

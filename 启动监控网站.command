#!/bin/zsh
cd "$(dirname "$0")"
echo "正在启动日本大学募集要项监控网站..."
echo "关闭这个窗口会停止网站。"
python3 website_app.py &
SERVER_PID=$!
sleep 2
open "http://127.0.0.1:8765"
wait $SERVER_PID

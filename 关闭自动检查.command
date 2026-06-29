#!/bin/zsh
cd "$(dirname "$0")"
/usr/bin/python3 uninstall_auto_check.py
echo ""
echo "按回车键关闭这个窗口。"
read

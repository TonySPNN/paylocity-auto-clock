#!/bin/bash
set -e

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" >/dev/null 2>&1 && pwd )"
cd "$DIR"

echo "================================================="
echo "   🕒 เริ่มต้นระบบ Paylocity Auto Clock System"
echo "================================================="

# Create venv if it doesn't exist
if [ ! -d "venv" ]; then
    echo "📦 กำลังสร้าง Virtual Environment..."
    python3 -m venv venv
    source venv/bin/activate
    echo "⬇️ กำลังติดตั้งไลบรารีที่จำเป็น..."
    pip install --upgrade pip
    pip install -r requirements.txt
    echo "🌐 ติดตั้ง Playwright Chromium..."
    playwright install chromium
else
    source venv/bin/activate
fi

echo ""
echo "🚀 เซิร์ฟเวอร์กำลังเริ่มต้นทำงานที่: http://localhost:8000"
echo "👉 กด Ctrl+C เพื่อหยุดการทำงาน"
echo "================================================="
echo ""

python3 app.py

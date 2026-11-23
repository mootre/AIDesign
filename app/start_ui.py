"""Open an already-running current server, or explain a stale server before starting."""
import json
from pathlib import Path
import runpy
import sys
import urllib.request
import webbrowser

EXPECTED_BUILD = "2026.11.23-training-studio"
URL = "http://127.0.0.1:8768"


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    try:
        with urllib.request.urlopen(URL+"/api/status", timeout=2) as response:
            existing = json.load(response)
    except Exception:
        existing = None
    if existing:
        webbrowser.open(URL + "/training")
        if existing.get("build") == EXPECTED_BUILD:
            print("เว็บรุ่นล่าสุดเปิดอยู่แล้ว: " + URL)
            return 0
        message = "ยังมีโปรแกรมเว็บรุ่นเก่าทำงานอยู่ที่พอร์ต 8768\nกรุณาปิดหน้าต่างโปรแกรมเดิมก่อน แล้วเปิด start.bat จากโฟลเดอร์รุ่นล่าสุดอีกครั้ง\nการรีเฟรชเบราว์เซอร์อย่างเดียวไม่เปลี่ยนโปรแกรมที่กำลังรัน"
        print(message)
        if sys.platform == "win32":
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, message, "ต้องปิดโปรแกรมเว็บรุ่นเก่าก่อน", 0x30)
        return 2
    runpy.run_path(str(Path(__file__).resolve().parent/"server.py"), run_name="__main__")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

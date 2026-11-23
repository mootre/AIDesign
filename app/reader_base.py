"""Loopback-only PDF reader. Documents and results remain in process memory."""
import io
import json
import secrets
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pypdfium2 as pdfium

ROOT = Path(__file__).resolve().parent
PORT = 8765
JOBS = {}
LOCK = threading.Lock()
ENGINE_LOCK = threading.Lock()
ENGINE = None
TOKEN = secrets.token_urlsafe(32)


def engine():
    global ENGINE
    if ENGINE is None:
        sys.path.insert(0, str(ROOT / "model"))
        from runtime.line_engine import LineOCR
        ENGINE = LineOCR(str(ROOT / "model"), threads=2)
    return ENGINE


def process(job, raw, first, last, mode):
    doc = None
    try:
        with ENGINE_LOCK:
            if job["cancel"]:
                job["status"] = "cancelled"
                return
            doc = pdfium.PdfDocument(raw)
            if not len(doc):
                raise ValueError("PDF ไม่มีหน้าเอกสาร")
            end = last or len(doc)
            if first < 1 or end < first or end > len(doc):
                raise ValueError(f"เลือกช่วงหน้าระหว่าง 1 ถึง {len(doc)}")
            job.update(total=end-first+1, document_pages=len(doc), status="running")
            for index in range(first-1, end):
                if job["cancel"]:
                    job["status"] = "cancelled"
                    return
                started = time.perf_counter()
                page = doc[index]
                bitmap = None
                textpage = None
                image = None
                try:
                    text = ""
                    source = "ocr"
                    if mode == "auto":
                        textpage = page.get_textpage()
                        text = textpage.get_text_range().strip()
                        if text:
                            source = "pdf"
                    if not text:
                        width, height = page.get_size()
                        scale = min(200/72, 3500/max(width, height))
                        bitmap = page.render(scale=scale)
                        image = bitmap.to_pil().convert("RGB")
                        text = engine().read(image)["text"]
                    result = {"page": index+1, "text": text, "source": source,
                              "seconds": round(time.perf_counter()-started, 2)}
                    with LOCK:
                        job["pages"].append(result)
                        job["done"] += 1
                finally:
                    if image is not None:
                        image.close()
                    if bitmap is not None:
                        bitmap.close()
                    if textpage is not None:
                        textpage.close()
                    page.close()
            job["status"] = "done"
    except Exception as exc:
        job["status"] = "error"
        job["error"] = "อ่าน PDF ไม่สำเร็จ: " + str(exc)[:400]
    finally:
        if doc is not None:
            doc.close()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def reply(self, data, status=200, mime="application/json; charset=utf-8"):
        body = json.dumps(data, ensure_ascii=False).encode() if isinstance(data, dict) else data
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' blob:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(body)

    def valid_host(self):
        return self.headers.get("Host") in (f"127.0.0.1:{PORT}", f"localhost:{PORT}")

    def do_GET(self):
        if not self.valid_host():
            return self.reply({"error": "Invalid host"}, 403)
        path = urlparse(self.path).path
        if path == "/api/status":
            return self.reply({"ready": all((ROOT/"model"/p).exists() for p in ["det.onnx", "rec.onnx", "runtime/line_engine.py"]), "token": TOKEN})
        if path.startswith("/api/jobs/"):
            with LOCK:
                job = JOBS.get(path.rsplit("/", 1)[-1])
                return self.reply({k: v for k, v in job.items() if k not in ("created", "cancel")} if job else {"error": "ไม่พบงาน กรุณาเลือกไฟล์ใหม่"}, 200 if job else 404)
        files = {"/": ("index.html", "text/html; charset=utf-8"), "/app.js": ("app.js", "text/javascript; charset=utf-8"), "/style.css": ("style.css", "text/css; charset=utf-8")}
        if path in files:
            name, mime = files[path]
            return self.reply((ROOT/name).read_bytes(), mime=mime)
        self.reply({"error": "Not found"}, 404)

    def do_POST(self):
        if not self.valid_host() or self.headers.get("X-App-Token") != TOKEN:
            return self.reply({"error": "กรุณาเปิดหน้าเว็บใหม่"}, 403)
        parsed = urlparse(self.path)
        if parsed.path.startswith("/api/cancel/"):
            with LOCK:
                job = JOBS.get(parsed.path.rsplit("/", 1)[-1])
                if job:
                    job["cancel"] = True
            return self.reply({"ok": True})
        if parsed.path not in ("/api/read", "/api/preview"):
            return self.reply({"error": "Not found"}, 404)
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 100*1024*1024:
                return self.reply({"error": "รองรับ PDF ขนาดไม่เกิน 100 MB"}, 413)
            q = parse_qs(parsed.query)
            first, last = int(q.get("first", [1])[0]), int(q.get("last", [0])[0])
            mode = q.get("mode", ["ocr"])[0]
            if mode not in ("ocr", "auto"):
                raise ValueError("โหมดไม่ถูกต้อง")
            if parsed.path == "/api/preview":
                raw = self.rfile.read(length)
                with ENGINE_LOCK:
                    doc = pdfium.PdfDocument(raw)
                    try:
                        index = int(q.get("page", [1])[0])-1
                        if not 0 <= index < len(doc):
                            raise ValueError("ไม่มีหน้าที่เลือก")
                        page = doc[index]
                        bitmap = page.render(scale=min(120/72, 2000/max(page.get_size())))
                        image = bitmap.to_pil().convert("RGB")
                        try:
                            output = io.BytesIO()
                            image.save(output, format="JPEG", quality=85)
                            self.send_response(200)
                            self.send_header("Content-Type", "image/jpeg")
                            self.send_header("Content-Length", str(output.tell()))
                            self.send_header("X-Page-Count", str(len(doc)))
                            self.send_header("Cache-Control", "no-store")
                            self.end_headers()
                            self.wfile.write(output.getvalue())
                        finally:
                            image.close()
                            bitmap.close()
                            page.close()
                    finally:
                        doc.close()
                return
            with LOCK:
                for key in list(JOBS):
                    if JOBS[key]["status"] not in ("queued", "running") and time.time()-JOBS[key]["created"] > 3600:
                        del JOBS[key]
                if sum(j["status"] in ("queued", "running") for j in JOBS.values()) >= 2:
                    return self.reply({"error": "กำลังอ่านเอกสารอยู่ กรุณารอให้งานเดิมเสร็จ"}, 429)
            raw = self.rfile.read(length)
            if not raw.lstrip()[:1024].startswith(b"%PDF-"):
                raise ValueError("ไฟล์นี้ไม่ใช่ PDF ที่รองรับ")
            key = secrets.token_urlsafe(18)
            job = {"id": key, "status": "queued", "total": 0, "done": 0, "pages": [], "error": "", "cancel": False, "created": time.time()}
            with LOCK:
                JOBS[key] = job
            threading.Thread(target=process, args=(job, raw, first, last, mode), daemon=True).start()
            self.reply({"id": key}, 202)
        except Exception as exc:
            self.reply({"error": str(exc)}, 400)


if __name__ == "__main__":
    try:
        server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    except OSError:
        print(f"Port {PORT} is busy. Close the previous reader before starting again.")
        sys.exit(1)
    print(f"Thai PDF Reader: http://127.0.0.1:{PORT}", flush=True)
    if "--no-browser" not in sys.argv:
        webbrowser.open(f"http://127.0.0.1:{PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()

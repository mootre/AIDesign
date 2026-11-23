"""Local web UI. Only selected PDF page images/text are sent through Codex CLI."""
import atexit
import hashlib
import copy
import training_api
import json
import secrets
import shutil
import subprocess
import os
import sys
import tempfile
import threading
import time
import webbrowser
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import reader_base as base
import estimator
import drawing
import pdf_vector
import measured_views

ROOT = Path(__file__).resolve().parent
PORT = 8768
BUILD = "2026.11.23-training-studio"
base.PORT = PORT
AI_JOBS = {}
AI_LOCK = threading.RLock()
WORK = tempfile.TemporaryDirectory(prefix="thai-furniture-")
atexit.register(WORK.cleanup)


def public(job):
    result = {k: v for k, v in job.items() if k not in {"directory", "drawing_directory", "images", "pdf_text", "cancel", "created", "inflight"}}
    spec = result.get("drawing_spec")
    if spec and spec.get("mode") == "pdf_vector":
        result["drawing_spec"] = {k: v for k,v in spec.items() if k != "views"}
        result["drawing_spec"]["views"] = [{"name":v["name"],"source_pages":v["source_pages"]} for v in spec["views"]]
    return result


def prepare_pdf(raw, first, last, directory):
    numbers, images, texts = [], [], []
    with base.ENGINE_LOCK:
        doc = base.pdfium.PdfDocument(raw)
        try:
            end = last or len(doc)
            if first < 1 or end < first or end > len(doc):
                raise ValueError(f"ช่วงหน้าต้องอยู่ระหว่าง 1 ถึง {len(doc)}")
            if end-first+1 > estimator.MAX_PAGES:
                raise ValueError(f"วิเคราะห์ได้ครั้งละไม่เกิน {estimator.MAX_PAGES} หน้า กรุณาเลือกช่วงหน้า")
            for index in range(first-1, end):
                page = doc[index]
                try:
                    textpage = page.get_textpage()
                    try:
                        text = textpage.get_text_range()
                    finally:
                        textpage.close()
                    bitmap = page.render(scale=min(200/72, 2600/max(page.get_size())))
                    try:
                        image = bitmap.to_pil().convert("RGB")
                        try:
                            path = directory / f"page-{index+1}.png"
                            image.save(path)
                        finally:
                            image.close()
                    finally:
                        bitmap.close()
                    images.append(path)
                    numbers.append(index+1)
                    texts.append(f"PDF PAGE {index+1}:\n{text}")
                finally:
                    page.close()
            return numbers, images, "\n\n".join(texts), len(doc)
        finally:
            doc.close()


def run_analysis(job, raw, first, last, waste):
    try:
        job.update(status="running", stage="กำลังเตรียมภาพหน้าแบบ")
        numbers, images, text, total = prepare_pdf(raw, first, last, job["directory"])
        if job["cancel"]:
            raise ValueError("ยกเลิกการวิเคราะห์แล้ว")
        job.update(page_numbers=numbers, images=images, document_pages=total, pdf_text=text, stage="Codex กำลังอ่านแบบและตั้งสมมติฐาน")
        report = estimator.call_codex(job["directory"], images, estimator.analysis_prompt(numbers, text, waste), estimator.SCHEMA, lambda: job["cancel"])
        # Page citations and assumption references are checked locally as well as by schema.
        audit_report(report, numbers)
        calculation = estimator.calculate(report, waste)
        job.update(report=report, calculation=calculation, status="done", stage="วิเคราะห์เสร็จแล้ว กรุณาตรวจสมมติฐานก่อนนำไปใช้")
    except Exception as exc:
        job.update(status="cancelled" if job["cancel"] else "error", error=str(exc)[:700], stage="หยุดวิเคราะห์")
    finally:
        job["inflight"] = False


def audit_report(report, numbers):
    ids = [a["id"] for a in report["assumptions"]]
    if len(set(ids)) != len(ids) or any(not i for i in ids):
        raise ValueError("รหัสสมมติฐานจาก AI ซ้ำหรือว่าง กรุณาวิเคราะห์ใหม่")
    for section in ("assumptions", "parts", "hardware", "checks"):
        for row in report[section]:
            if any(p not in numbers for p in row["source_pages"]):
                raise ValueError("AI อ้างหน้าเอกสารนอกช่วงที่ส่ง กรุณาวิเคราะห์ใหม่")
            if any(i not in ids for i in row.get("assumption_ids", [])):
                raise ValueError("AI อ้างสมมติฐานที่ไม่มีอยู่ กรุณาวิเคราะห์ใหม่")


def run_chat(job, question):
    try:
        answer = estimator.call_codex(job["directory"], job["images"], estimator.chat_prompt(job, job["chat"], question), estimator.CHAT_SCHEMA, lambda: job["cancel"])
        if any(p not in job["page_numbers"] for p in answer["source_pages"]):
            raise ValueError("คำตอบอ้างหน้าเอกสารนอกช่วงที่วิเคราะห์")
        job["chat"].extend([{"role": "user", "text": question}, {"role": "assistant", **answer}])
        job["chat_error"] = ""
    except Exception as exc:
        job["chat_error"] = str(exc)[:700]
    finally:
        job["chat_busy"] = False
        job["inflight"] = False


def run_drawing(job):
    try:
        folder = job["directory"] / ("cad-" + secrets.token_hex(5))
        folder.mkdir()
        job["drawing_stage"] = "Codex กำลังอ่านภาพเพื่อสร้างมุมมอง Drawing"
        spec = measured_views.generate(folder,job["images"],job["page_numbers"],job.get("pdf_text",""),lambda:job["cancel"])
        if job["cancel"]:
            raise ValueError("ยกเลิกการสร้าง Drawing แล้ว")
        job["drawing_stage"] = "กำลังสร้าง DWG และแปลงกลับเพื่อตรวจมิติ"
        result = drawing.export(spec, folder)
        if job["cancel"]:
            raise ValueError("ยกเลิกการสร้าง Drawing แล้ว")
        job.update(drawing_status="done", drawing_stage="สร้าง DWG และตรวจไฟล์สำเร็จ", drawing_info=result,
                   drawing_spec=spec, drawing_directory=folder)
    except Exception as exc:
        job.update(drawing_status="error", drawing_error=str(exc)[:700], drawing_stage="สร้าง Drawing ไม่สำเร็จ")
    finally:
        job["inflight"] = False


def run_vector(job, raw, first, last):
    try:
        job.update(status="running", stage="กำลังอ่านเส้นต้นฉบับ PDF — ไม่เรียก AI", drawing_status="running")
        numbers, images, text, total = prepare_pdf(raw, first, last, job["directory"])
        job.update(page_numbers=numbers, images=images, document_pages=total)
        with base.ENGINE_LOCK:
            spec = pdf_vector.extract(raw, first, last)
        if job["cancel"]:raise ValueError("ยกเลิกแล้ว")
        folder = job["directory"] / "pdf-vector"
        folder.mkdir()
        job.update(stage="กำลังสร้าง DWG และตรวจไฟล์กลับ", drawing_stage="กำลังแปลงเส้น PDF เป็น DWG")
        result = drawing.export(spec, folder)
        if job["cancel"]:raise ValueError("ยกเลิกแล้ว")
        report = {"summary": "นำเข้าเส้นต้นฉบับ PDF เป็น CAD 2D สำเร็จ ใช้พิกัดมิลลิเมตรบนหน้ากระดาษ ไม่ใช่การยืนยันขนาดชิ้นงานจริง และยังไม่ได้วิเคราะห์รายการวัสดุด้วย AI",
                  "document_status": "นำเข้าเส้น PDF โดยตรง / สเกลกระดาษ", "assumptions": [], "parts": [], "hardware": [],
                  "checks": [{"issue": n, "severity": "ตรวจทาน", "source_pages": numbers} for n in spec["notes"]]}
        job.update(status="done", stage="สร้าง DWG จากเส้น PDF สำเร็จ", report=report,
                   calculation=estimator.calculate(report), drawing_status="done", drawing_stage="สร้าง DWG จากเส้น PDF และตรวจไฟล์สำเร็จ",
                   drawing_info=result, drawing_spec=spec, drawing_directory=folder)
    except Exception as exc:
        job.update(status="cancelled" if job["cancel"] else "error", error=str(exc)[:700],
                   drawing_status="error", drawing_error=str(exc)[:700], stage="สร้าง DWG ไม่สำเร็จ")
    finally:job["inflight"] = False


def run_isometric(job, raw, first, last):
    try:
        job.update(status='running',stage='กำลังเตรียมภาพและข้อความมิติจาก PDF',drawing_status='running')
        numbers,images,text,total=prepare_pdf(raw,first,last,job['directory'])
        job.update(page_numbers=numbers,images=images,document_pages=total,pdf_text=text,stage='Codex กำลังตรวจกลุ่ม TOP / ELEVATION / INSIDE และมิติจริง')
        folder=job['directory']/'isometric-basic';folder.mkdir()
        job['stage']='กำลังตรวจเส้นที่ฝังใน PDF เพื่อค้นหามุมมองที่ซ่อนอยู่'
        supplemental=[]
        supplemental_pages=[]
        for number in numbers:
            if job['cancel']:raise ValueError('ยกเลิกแล้ว')
            reference=folder/('embedded-page-'+str(number));reference.mkdir()
            try:
                with base.ENGINE_LOCK:vector=pdf_vector.extract(raw,number,number)
            except ValueError as error:
                if 'ไม่พบเส้นเวกเตอร์' in str(error):continue
                raise
            drawing.export(vector,reference)
            config=drawing.settings();image=reference/'embedded.png'
            result=subprocess.run([config['python'],str(ROOT/'render_reference.py'),str(reference/'drawing.svg'),str(image)],
                                  capture_output=True,timeout=60,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
            if result.returncode:raise ValueError('สร้างภาพอ้างอิงเส้น PDF ไม่สำเร็จ')
            supplemental.append(image)
            supplemental_pages.append(number)
        job['stage']='Codex กำลังตรวจกลุ่มรูปฉายและพิกัดมิลลิเมตรจาก PDF'
        spec=measured_views.generate(folder,images+supplemental,numbers,text+'\nSupplemental embedded-vector images correspond to PDF pages: '+str(supplemental_pages),lambda:job['cancel'])
        if job['cancel']:raise ValueError('ยกเลิกแล้ว')
        job['stage']='กำลังสร้างรูปฉาย 1:1 และวัดระยะใน DWG กลับมาตรวจ'
        result=drawing.export(spec,folder)
        if job['cancel']:raise ValueError('ยกเลิกแล้ว')
        report={'summary':'สร้างรูปฉายตามกลุ่มที่พบใน PDF ใช้พิกัดมิลลิเมตรจริงและตรวจระยะอ้างอิงใน DWG แล้ว ไม่มีข้อความหรือรายละเอียดจิ๋วบน Drawing',
                'document_status':'รูปฉาย 1:1 มม. / ตรวจขนาดอ้างอิงแล้ว','assumptions':spec['assumptions'],
                'parts':[],'hardware':[],'checks':[{'issue':n,'severity':'ตรวจทาน','source_pages':numbers} for n in spec['notes']]}
        job.update(status='done',stage='สร้าง DWG รูปฉายที่วัดขนาดได้สำเร็จ',report=report,calculation=estimator.calculate(report),
                   drawing_status='done',drawing_stage='สร้างรูปฉาย 1:1 และตรวจระยะจริงใน DWG สำเร็จ',drawing_info=result,drawing_spec=spec,drawing_directory=folder)
    except Exception as exc:
        job.update(status='cancelled' if job['cancel'] else 'error',error=str(exc)[:700],drawing_status='error',drawing_error=str(exc)[:700],stage='สร้างรูปฉายที่วัดขนาดได้ไม่สำเร็จ')
    finally:job['inflight']=False


class Handler(base.Handler):
    def do_GET(self):
        if not self.valid_host():
            return self.reply({"error": "Invalid host"}, 403)
        path = urlparse(self.path).path
        if training_api.handle_get(self, path):
            return
        if path == "/api/status":
            return self.reply({"ready": all((ROOT/"model"/p).exists() for p in ["det.onnx", "rec.onnx", "runtime/line_engine.py"]),
                               "token": base.TOKEN, "build": BUILD, "training": True, "vector_dwg": True, "isometric_basic": True})
        if path == "/analysis.js":
            return self.reply((ROOT / "analysis.js").read_bytes(), mime="text/javascript; charset=utf-8")
        if path == "/cad.js":
            return self.reply((ROOT / "cad.js").read_bytes(), mime="text/javascript; charset=utf-8")
        if path == "/api/ai-status":
            return self.reply({**estimator.cli_status(), "max_pages": estimator.MAX_PAGES, "token": base.TOKEN})
        if path == "/api/cad-status":
            return self.reply(drawing.status())
        if path.startswith("/api/drawing/"):
            pieces = path.split("/")
            if len(pieces) != 5:
                return self.reply({"error": "Not found"}, 404)
            key, kind = pieces[3], pieces[4]
            allowed = {"dwg": ("drawing.dwg", "application/octet-stream"), "dxf": ("drawing.dxf", "application/dxf"),
                       "svg": ("drawing.svg", "image/svg+xml"), "json": ("drawing-spec.json", "application/json; charset=utf-8")}
            with AI_LOCK:
                job = AI_JOBS.get(key)
                if kind not in allowed or not job or job.get("drawing_status") != "done":
                    return self.reply({"error": "ยังไม่มี Drawing กรุณาสร้างใหม่"}, 404)
                name, mime = allowed[kind]
                body = (job["drawing_directory"] / name).read_bytes()
            return self.reply(body, mime=mime)
        if path.startswith("/api/analysis/"):
            with AI_LOCK:
                job = AI_JOBS.get(path.rsplit("/", 1)[-1])
                return self.reply(public(job) if job else {"error": "ไม่พบงาน กรุณาวิเคราะห์ใหม่"}, 200 if job else 404)
        return super().do_GET()

    def read_json(self):
        length = int(self.headers.get("Content-Length", "0"))
        if not 0 < length <= 2*1024*1024:
            raise ValueError("คำขอใหญ่เกินกำหนด")
        result = json.loads(self.rfile.read(length))
        if not isinstance(result, dict):
            raise ValueError("รูปแบบคำขอไม่ถูกต้อง")
        return result

    def do_POST(self):
        if not self.valid_host() or self.headers.get("X-App-Token") != base.TOKEN:
            return self.reply({"error": "กรุณาเปิดหน้าเว็บใหม่"}, 403)
        parsed = urlparse(self.path)
        origin = self.headers.get("Origin")
        if (origin and origin not in (f"http://127.0.0.1:{PORT}", f"http://localhost:{PORT}")) or self.headers.get("Sec-Fetch-Site") == "cross-site":
            return self.reply({"error": "ไม่อนุญาตคำขอจากเว็บอื่น"}, 403)
        if training_api.handle_post(self, parsed.path):
            return
        if parsed.path not in ("/api/analyze", "/api/vector-drawing", "/api/isometric-drawing", "/api/measured-drawing", "/api/chat", "/api/recalculate", "/api/ai-cancel", "/api/forget", "/api/create-drawing"):
            return super().do_POST()
        try:
            if parsed.path in ("/api/analyze", "/api/vector-drawing", "/api/isometric-drawing", "/api/measured-drawing"):
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 100*1024*1024:
                    raise ValueError("รองรับ PDF ไม่เกิน 100 MB")
                q = parse_qs(parsed.query)
                first, last = int(q.get("first", [1])[0]), int(q.get("last", [0])[0])
                waste = float(q.get("waste", [12])[0])
                estimator.calculate({"parts": []}, waste)
                if first < 1 or last < 0 or (last and (last < first or last-first+1 > estimator.MAX_PAGES)):
                    raise ValueError("ช่วงหน้าไม่ถูกต้อง หรือเกิน 8 หน้า")
                with AI_LOCK:
                    # Expire inactive jobs; never delete images while a CLI is reading them.
                    for key in list(AI_JOBS):
                        old = AI_JOBS[key]
                        if not old["inflight"] and time.time()-old["created"] > 3600:
                            shutil.rmtree(old["directory"], ignore_errors=True)
                            del AI_JOBS[key]
                    if any(j["inflight"] for j in AI_JOBS.values()):
                        return self.reply({"error": "กำลังวิเคราะห์/ตอบคำถาม กรุณารอหรือยกเลิกงานเดิม"}, 429)
                    if len(AI_JOBS) >= 5:
                        raise ValueError("มีเอกสารค้างอยู่ 5 ชุด กรุณาล้างเอกสารเก่าก่อน")
                    raw = self.rfile.read(length)
                    if not raw.lstrip().startswith(b"%PDF-"):
                        raise ValueError("ไฟล์นี้ไม่ใช่ PDF")
                    key = secrets.token_urlsafe(18)
                    filename = str(q.get("name", ["เอกสาร PDF"])[0])[:240]
                    directory = Path(WORK.name) / key
                    directory.mkdir()
                    job = {"id": key, "filename": filename, "status": "queued", "stage": "กำลังรอ", "error": "", "directory": directory,
                           "drawing_status": "idle", "drawing_error": "", "drawing_stage": "", "drawing_info": None, "drawing_spec": None,
                           "images": [], "page_numbers": [], "cancel": False, "created": time.time(), "inflight": True,
                           "report": None, "calculation": None, "chat": [], "chat_busy": False, "chat_error": ""}
                    AI_JOBS[key] = job
                if parsed.path in ("/api/isometric-drawing", "/api/measured-drawing"):
                    threading.Thread(target=run_isometric,args=(job,raw,first,last),daemon=True).start()
                elif parsed.path == "/api/vector-drawing":
                    threading.Thread(target=run_vector, args=(job, raw, first, last), daemon=True).start()
                else:
                    threading.Thread(target=run_analysis, args=(job, raw, first, last, waste), daemon=True).start()
                return self.reply({"id": key}, 202)
            body = self.read_json()
            with AI_LOCK:
                job = AI_JOBS.get(body.get("id"))
                if not job:
                    return self.reply({"error": "ไม่พบงาน"}, 404)
                if parsed.path == "/api/ai-cancel":
                    job["cancel"] = True
                    return self.reply({"ok": True})
                if parsed.path in ("/api/chat", "/api/create-drawing") and job.get("training_drawing_id"):
                    consent = training_api.store().get_drawing(job["training_drawing_id"])
                    if not consent["allow_ai"] or not consent["privacy_confirmed"]:
                        return self.reply({"error": "สิทธิ์ส่งแบบให้ AI ถูกถอนแล้ว กรุณาตรวจสิทธิ์ในคลังแบบ"}, 403)
                if job["inflight"]:
                    return self.reply({"error": "กรุณารอให้งานปัจจุบันเสร็จก่อน"}, 409)
                if parsed.path == "/api/forget":
                    shutil.rmtree(job["directory"], ignore_errors=True)
                    del AI_JOBS[job["id"]]
                    return self.reply({"ok": True})
                if job["status"] != "done":
                    raise ValueError("ต้องวิเคราะห์เอกสารให้สำเร็จก่อน")
                if parsed.path == "/api/create-drawing":
                    if any(j["inflight"] for j in AI_JOBS.values()):
                        return self.reply({"error": "กำลังประมวลผลงานอื่น กรุณารอ"}, 409)
                    if not drawing.status()["ready"]:
                        raise ValueError(drawing.status()["message"])
                    if job.get("drawing_directory"):
                        shutil.rmtree(job.pop("drawing_directory"), ignore_errors=True)
                    job.update(inflight=True, cancel=False, drawing_status="running", drawing_error="", drawing_info=None, drawing_spec=None)
                    threading.Thread(target=run_drawing, args=(job,), daemon=True).start()
                    return self.reply({"ok": True}, 202)
                if parsed.path == "/api/recalculate":
                    report = body.get("report", job["report"])
                    estimator.validate(report, estimator.SCHEMA)
                    audit_report(report, job["page_numbers"])
                    calc = estimator.calculate(report, body.get("waste", 12))
                    job.update(report=report, calculation=calc)
                    # Old answers could be based on replaced dimensions; do not carry them forward.
                    job["chat"] = []
                    job["chat_error"] = ""
                    if job.get("drawing_directory"):
                        shutil.rmtree(job.pop("drawing_directory"), ignore_errors=True)
                    job.update(drawing_status="idle", drawing_info=None, drawing_spec=None, drawing_stage="", drawing_error="")
                    return self.reply(public(job))
                question = body.get("question", "")
                if not isinstance(question, str) or not question.strip() or len(question) > 4000:
                    raise ValueError("กรอกคำถาม 1–4000 ตัวอักษร")
                if len(job["chat"]) >= 40:
                    raise ValueError("ครบ 20 คำถามแล้ว กรุณาดาวน์โหลดผลแล้วเริ่มวิเคราะห์ใหม่")
                job.update(chat_busy=True, inflight=True, chat_error="", cancel=False)
            threading.Thread(target=run_chat, args=(job, question.strip()), daemon=True).start()
            self.reply({"ok": True}, 202)
        except (ValueError, TypeError, KeyError) as exc:
            self.reply({"error": str(exc)[:500]}, 400)


training_api.configure(ROOT, base, AI_JOBS, AI_LOCK, WORK, prepare_pdf, audit_report, estimator)


if __name__ == "__main__":
    try:
        server = base.ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    except OSError:
        print(f"Port {PORT} is busy. Close the previous analyzer.")
        sys.exit(1)
    print(f"Thai Furniture Training Studio: http://127.0.0.1:{PORT}", flush=True)
    if "--no-browser" not in sys.argv:
        webbrowser.open(f"http://127.0.0.1:{PORT}/training")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        with AI_LOCK:
            for job in AI_JOBS.values():
                job["cancel"] = True
        server.server_close()

"""Persistent, opt-in training workflow for a trusted local desktop user."""
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import threading
import time
from urllib.parse import unquote, urlparse
import zipfile

from training_store import Store, StoreError

MAX_UPLOAD = 30 * 1024 * 1024
_context = {}
_store = None
_store_lock = threading.Lock()


def configure(root, base, jobs, lock, work, prepare, audit, estimator):
    _context.update(root=root, base=base, jobs=jobs, lock=lock, work=work,
                    prepare=prepare, audit=audit, estimator=estimator)


def store():
    global _store
    with _store_lock:
        if _store is None:
            # Runtime data is outside source, exports, and Docs Hub by default.
            local = Path(os.environ.get('LOCALAPPDATA', Path.home() / '.local' / 'share'))
            root = Path(os.environ.get('THAI_TRAINING_DATA', local / 'ThaiFurnitureTrainingStudio'))
            _store = Store(root)
        return _store


def pdf_info(raw):
    if not raw.lstrip().startswith(b'%PDF-'):
        raise StoreError('กรุณาใช้ไฟล์ PDF เท่านั้น')
    with _context['base'].ENGINE_LOCK:
        doc = _context['base'].pdfium.PdfDocument(raw)
        try:
            count = len(doc)
            if not 1 <= count <= 200:
                raise StoreError('รองรับ PDF ไม่เกิน 200 หน้า กรุณาแยกเอกสารก่อน')
            return count
        finally:
            doc.close()


def render_page(drawing_id, number):
    drawing = store().get_drawing(drawing_id)
    if not 1 <= number <= drawing['page_count']:
        raise StoreError('ไม่พบหน้าแบบที่เลือก', 404)
    with _context['base'].ENGINE_LOCK:
        doc = _context['base'].pdfium.PdfDocument(str(store().pdf_path(drawing_id)))
        try:
            page = doc[number - 1]
            try:
                size = page.get_size()
                bitmap = page.render(scale=min(2.0, 2400 / max(size)))
                try:
                    image = bitmap.to_pil().convert('RGB')
                    try:
                        output = io.BytesIO()
                        image.save(output, format='PNG')
                        return output.getvalue()
                    finally:
                        image.close()
                finally:
                    bitmap.close()
            finally:
                page.close()
        finally:
            doc.close()


def authenticated(handler):
    if handler.headers.get('X-App-Token') != _context['base'].TOKEN:
        handler.reply({'error': 'กรุณาเปิดหน้าเว็บใหม่'}, 403)
        return False
    return True


def handle_get(handler, path):
    assets = {'/training': ('training.html', 'text/html; charset=utf-8'),
              '/training/': ('training.html', 'text/html; charset=utf-8'),
              '/training.js': ('training.js', 'text/javascript; charset=utf-8'),
              '/training.css': ('training.css', 'text/css; charset=utf-8')}
    if path in assets:
        file, mime = assets[path]
        handler.reply((_context['root'] / file).read_bytes(), mime=mime)
        return True
    if not path.startswith('/api/training/'):
        return False
    if not authenticated(handler):
        return True
    try:
        if urlparse(handler.path).query:
            raise StoreError('ไม่รองรับ query string')
        if path == '/api/training/documents':
            handler.reply({'documents': store().list_drawings(), 'stats': store().stats()})
        elif path == '/api/training/status':
            handler.reply({'stats': store().stats(), 'mode': 'trusted-local-user',
                           'auto_training': False})
        elif match := re.fullmatch(r'/api/training/documents/([a-f0-9]{32})', path):
            handler.reply(store().get_drawing(match[1]))
        elif match := re.fullmatch(r'/api/training/documents/([a-f0-9]{32})/page/([0-9]+)', path):
            handler.reply(render_page(match[1], int(match[2])), mime='image/png')
        elif match := re.fullmatch(r'/api/training/annotations/([a-f0-9]{32})/history', path):
            handler.reply({'history': store().history(match[1])})
        else:
            handler.reply({'error': 'ไม่พบรายการ'}, 404)
    except StoreError as error:
        handler.reply({'error': str(error)}, error.status)
    except Exception:
        handler.reply({'error': 'อ่านข้อมูลไม่สำเร็จ กรุณาตรวจว่าไฟล์แบบและพื้นที่เก็บข้อมูลยังพร้อมใช้งาน'}, 400)
    return True


def prediction_version():
    estimator = _context['estimator']
    try:
        import shutil
        binary = shutil.which('codex')
        result = subprocess.run([binary, '--version'], capture_output=True, text=True,
            timeout=10, env=estimator.cli_environment(),
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        version = result.stdout.strip()[:120] if result.returncode == 0 else 'Codex CLI version unknown'
    except Exception:
        version = 'Codex CLI version unknown'
    instruction_files = sorted((_context['root'] / 'skills').rglob('*.md'))
    prompt_hash = hashlib.sha256((_context['root'] / 'estimator.py').read_bytes())
    for file in instruction_files:
        prompt_hash.update(file.relative_to(_context['root']).as_posix().encode())
        prompt_hash.update(file.read_bytes())
    prompt = prompt_hash.hexdigest()[:16]
    return version + '; model selected by CLI (not reported)', 'analysis-' + prompt


def run_prediction(job, drawing_id, first, last):
    try:
        drawing = store().get_drawing(drawing_id)
        raw = store().pdf_path(drawing_id).read_bytes()
        job.update(status='running', stage='กำลังเตรียมภาพที่ได้รับอนุญาต')
        numbers, images, text, total = _context['prepare'](raw, first, last, job['directory'])
        # Check again immediately before external processing in case consent changed during rendering.
        drawing = store().get_drawing(drawing_id)
        if not drawing['allow_ai'] or not drawing['privacy_confirmed']:
            raise StoreError('ยังไม่ได้อนุญาตส่งภาพให้ AI หรือยังไม่ได้ตรวจข้อมูลส่วนบุคคล')
        job.update(page_numbers=numbers, images=images, document_pages=total, pdf_text=text,
                   stage='Codex กำลังอ่านแบบ — ผลยังต้องให้ผู้เชี่ยวชาญตรวจ')
        estimator = _context['estimator']
        report = estimator.call_codex(job['directory'], images,
            estimator.analysis_prompt(numbers, text), estimator.SCHEMA,
            lambda: job['cancel'] or not all(store().get_drawing(drawing_id)[key] for key in ('allow_ai','privacy_confirmed')))
        _context['audit'](report, numbers)
        model, prompt = prediction_version()
        saved = store().add_prediction(drawing_id, copy.deepcopy(report), model, prompt)
        job.update(status='done', report=report, calculation=estimator.calculate(report),
                   prediction_id=saved['id'], stage='เก็บผล AI เดิมแล้ว กรุณาสร้างคำตอบผู้เชี่ยวชาญและตรวจรับ')
    except Exception:
        job.update(status='error', error='วิเคราะห์ไม่สำเร็จ กรุณาตรวจ Codex login, โควตา และสิทธิ์ส่งแบบให้ AI แล้วลองใหม่',
                   stage='หยุดวิเคราะห์')
    finally:
        job['inflight'] = False


def start_prediction(drawing_id, body):
    drawing = store().get_drawing(drawing_id)
    if not drawing['allow_ai'] or not drawing['privacy_confirmed']:
        raise StoreError('กรุณาอนุญาตส่งให้ AI และยืนยันว่าปกปิดข้อมูลที่ไม่จำเป็นแล้ว', 403)
    status = _context['estimator'].cli_status()
    if not status.get('logged_in'):
        raise StoreError(status['message'], 409)
    first, last = body.get('first', 1), body.get('last', 1)
    if type(first) is not int or type(last) is not int or not 1 <= first <= last <= drawing['page_count'] or last-first >= 8:
        raise StoreError('เลือกช่วงหน้า 1–8 หน้าที่อยู่ในเอกสาร')
    with _context['lock']:
        jobs = _context['jobs']
        for ident in list(jobs):
            if not jobs[ident]['inflight'] and time.time() - jobs[ident]['created'] > 3600:
                import shutil
                shutil.rmtree(jobs[ident]['directory'], ignore_errors=True)
                del jobs[ident]
        if any(job['inflight'] for job in jobs.values()) or len(jobs) >= 5:
            raise StoreError('มีงานกำลังประมวลผลหรือครบห้างาน กรุณารอหรือล้างงานวิเคราะห์ชั่วคราว', 409)
        ident = secrets.token_urlsafe(18)
        directory = Path(_context['work'].name) / ident
        directory.mkdir()
        job = {'id': ident, 'filename': drawing['code'], 'status': 'queued', 'stage': 'รอวิเคราะห์',
               'error': '', 'directory': directory, 'drawing_status': 'idle', 'drawing_error': '',
               'drawing_stage': '', 'drawing_info': None, 'drawing_spec': None, 'images': [],
               'page_numbers': [], 'cancel': False, 'created': time.time(), 'inflight': True,
               'report': None, 'calculation': None, 'chat': [], 'chat_busy': False, 'chat_error': '', 'training_drawing_id':drawing_id}
        jobs[ident] = job
        threading.Thread(target=run_prediction, args=(job, drawing_id, first, last), daemon=True).start()
        return {'id': ident}


def export_archive():
    exported = store().export_samples()
    samples = exported['samples']
    if not samples:
        raise StoreError('ยังไม่มีคำตอบที่ผ่านอนุมัติ มีหลักฐานครบ อนุญาตฝึก และกำหนดชุดข้อมูลแล้ว', 409)
    pages = set()
    for sample in samples:
        for region in sample['evidence']:
            pages.add((sample['drawing_id'], region['page']))
    if len(pages) > 75 or len(samples) > 2000:
        raise StoreError('ชุดข้อมูลใหญ่เกินการส่งออกครั้งเดียวในรุ่นนี้ กรุณาแบ่งชุดงานก่อน', 413)
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
        seen_assets = set()
        for drawing_id, page in sorted(pages):
            drawing = store().get_drawing(drawing_id)
            asset = f'images/{drawing["sha256"]}/page-{page}.png'
            if asset not in seen_assets:
                archive.writestr(asset, render_page(drawing_id, page))
                seen_assets.add(asset)
                if output.tell() > 128 * 1024 * 1024:
                    raise StoreError("ชุดส่งออกเกิน 128 MB กรุณาแบ่งกลุ่มข้อมูล", 413)
        # Snapshot eligibility checked again after rendering; do not export revoked/edited items.
        current = store().export_samples()['samples']
        if json.dumps(current, sort_keys=True) != json.dumps(samples, sort_keys=True):
            raise StoreError('ข้อมูลหรือสิทธิ์เปลี่ยนระหว่างส่งออก กรุณาสร้างชุดส่งออกใหม่', 409)
        for split in ('train', 'dev', 'test'):
            selected = []
            for sample in samples:
                if sample['split'] != split:
                    continue
                sample = copy.deepcopy(sample)
                for region in sample['evidence']:
                    region['image'] = f'images/{sample["source_hash"]}/page-{region["page"]}.png'
                selected.append(json.dumps(sample, ensure_ascii=False))
            archive.writestr(split + '.jsonl', '\n'.join(selected) + ('\n' if selected else ''))
        archive.writestr('manifest.json', json.dumps({
            'schema_version': 'drawing-review-1', 'created_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
            'counts': {split: sum(row['split'] == split for row in samples) for split in ('train','dev','test')},
            'excluded': exported['excluded'], 'image_coordinates': 'normalized x,y,width,height',
            'format': 'provider-neutral examples, not an automatic model training job',
            'privacy': 'Human-confirmed source pages; not automatically anonymized'}, ensure_ascii=False, indent=2))
        archive.writestr('README.txt', 'Human-reviewed drawing dataset. Original AI predictions and reviewer identities are excluded.\n'
            'The images contain full approved source pages: privacy review must cover the whole page.\n'
            'Keep test examples and related source drawings out of training and retrieval context.\n'
            'This archive is a dataset snapshot, not an automatic fine-tuning request.\n')
    return output.getvalue()


def handle_post(handler, path):
    if not path.startswith('/api/training/'):
        return False
    if not authenticated(handler):
        return True
    try:
        if urlparse(handler.path).query or handler.headers.get('Transfer-Encoding'):
            raise StoreError('รูปแบบคำขอไม่รองรับ')
        if path == '/api/training/documents':
            size = int(handler.headers.get('Content-Length', '0'))
            if not 0 < size <= MAX_UPLOAD:
                raise StoreError('รองรับ PDF ไม่เกิน 30 MB', 413)
            if handler.headers.get_content_type() != 'application/pdf':
                raise StoreError('กรุณาใช้ PDF', 415)
            header = handler.headers.get('X-Training-Metadata', '')
            if len(header) > 10000:
                raise StoreError('ข้อมูลทะเบียนยาวเกินไป')
            metadata = json.loads(unquote(header))
            if not isinstance(metadata, dict) or metadata.get('allow_storage') is not True:
                raise StoreError('ต้องอนุญาตเก็บแบบในเครื่องก่อน')
            raw = handler.rfile.read(size)
            if len(raw) != size:
                raise StoreError('รับไฟล์ไม่ครบ')
            handler.reply(store().register(raw, metadata, pdf_info(raw)), 201)
            return True
        if handler.headers.get_content_type() != 'application/json':
            raise StoreError('ต้องส่งข้อมูล JSON', 415)
        body = handler.read_json()
        if match := re.fullmatch(r'/api/training/documents/([a-f0-9]{32})/annotations', path):
            handler.reply(store().save_annotation(match[1], body))
        elif match := re.fullmatch(r'/api/training/annotations/([a-f0-9]{32})/review', path):
            handler.reply(store().review_annotation(match[1], body))
        elif match := re.fullmatch(r'/api/training/documents/([a-f0-9]{32})/consent', path):
            updated = store().set_consent(match[1], body)
            if not updated['allow_ai'] or not updated['privacy_confirmed']:
                with _context['lock']:
                    for job in _context['jobs'].values():
                        if job.get('training_drawing_id') == match[1]:
                            job['cancel'] = True
            handler.reply(updated)
        elif match := re.fullmatch(r'/api/training/groups/([A-Za-z0-9_-]{1,64})/split', path):
            handler.reply(store().assign_split(match[1], body.get('split')))
        elif match := re.fullmatch(r'/api/training/documents/([a-f0-9]{32})/predict', path):
            handler.reply(start_prediction(match[1], body), 202)
        elif path == '/api/training/export':
            if body.get('privacy_confirmation') is not True:
                raise StoreError('ต้องยืนยันว่าตรวจข้อมูลส่วนบุคคลทั้งหน้าแล้วก่อนส่งออก')
            handler.reply(export_archive(), mime='application/zip')
        else:
            handler.reply({'error': 'ไม่พบรายการ'}, 404)
    except StoreError as error:
        handler.reply({'error': str(error)}, error.status)
    except (ValueError, TypeError, KeyError):
        handler.reply({'error': 'ข้อมูลไม่ถูกต้อง กรุณาตรวจช่องที่กรอกและลองใหม่'}, 400)
    except Exception:
        handler.reply({'error': 'บันทึกไม่สำเร็จ กรุณาตรวจพื้นที่เก็บข้อมูลแล้วลองใหม่'}, 500)
    return True

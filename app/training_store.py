"""Local-only training annotations. Immutable sources, versioned human decisions, explicit consent."""
from __future__ import annotations

from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3
import threading
import uuid


class StoreError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


SLUG = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z", re.ASCII)
IDENT = re.compile(r"[a-f0-9]{32}\Z", re.ASCII)
SENSITIVE = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}|"
                       r"\b(?:sk-(?:proj-)?[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9]{30,})\b|"
                       r"eyJ[A-Za-z0-9_-]{15,}\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+|"
                       r"-----BEGIN [A-Z ]*PRIVATE KEY-----|"
                       r"\b(?:password|passwd|cookie|authorization|api[_-]?key|access[_-]?token)"
                       r"['\"]?\s*[:=]\s*\S+", re.I)
TASKS = {'question', 'dimension', 'material', 'quantity', 'part', 'hardware', 'edge'}
EVIDENCE_TYPES = {'direct', 'calculated', 'inferred', 'insufficient'}


def now():
    return datetime.now(timezone.utc).isoformat(timespec='milliseconds')


def pack(value):
    try:
        return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':'))
    except (TypeError, ValueError):
        raise StoreError('ข้อมูล JSON ไม่ถูกต้อง') from None


def text(value, name, limit=2000, required=False):
    if not isinstance(value, str):
        raise StoreError(f'{name} ต้องเป็นข้อความ')
    value = value.strip()
    if len(value) > limit or (required and not value):
        raise StoreError(f'{name} ว่างหรือยาวเกินกำหนด')
    if any(ord(char) < 32 and char not in '\n\r\t' for char in value):
        raise StoreError(f'{name} มีอักขระที่ไม่รองรับ')
    if SENSITIVE.search(value):
        raise StoreError(f'{name} อาจมีข้อมูลส่วนบุคคลหรือข้อมูลยืนยันตัวตน กรุณาปกปิดก่อน')
    return value


def slug(value, name):
    if not isinstance(value, str) or not SLUG.fullmatch(value):
        raise StoreError(f'{name} ใช้เฉพาะ A-Z a-z 0-9 ขีดและขีดล่าง ยาวไม่เกิน 64 ตัว')
    return value


def boolean(data, key, default=False):
    value = data.get(key, default)
    if type(value) is not bool:
        raise StoreError(f'{key} ต้องเป็น true หรือ false')
    return value


def version(value):
    if type(value) is not int or value < 1:
        raise StoreError('ต้องระบุ expected_version เป็นจำนวนเต็มบวก')
    return value


def identifier(value):
    if not isinstance(value, str) or not IDENT.fullmatch(value):
        raise StoreError('ไม่พบรายการ', 404)
    return value


class Store:
    MAX_SOURCE = 30 * 1024 * 1024

    def __init__(self, root):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.files = self.root / 'sources'
        if self.files.is_symlink() or (hasattr(self.files, 'is_junction') and self.files.is_junction()):
            raise StoreError('โฟลเดอร์จัดเก็บต้องไม่เป็นลิงก์', 500)
        self.files.mkdir(exist_ok=True)
        self.db_path = self.root / 'training.sqlite3'
        if self.db_path.is_symlink():
            raise StoreError('ฐานข้อมูลต้องไม่เป็นลิงก์', 500)
        self.lock = threading.RLock()
        with self._tx() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS groups (
                    id TEXT PRIMARY KEY, split TEXT NOT NULL DEFAULT 'unassigned', created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS drawings (
                    id TEXT PRIMARY KEY, code TEXT NOT NULL, revision TEXT NOT NULL,
                    group_id TEXT NOT NULL REFERENCES groups(id), sha256 TEXT NOT NULL,
                    source_rel TEXT NOT NULL, page_count INTEGER NOT NULL, body TEXT NOT NULL,
                    UNIQUE(code, revision));
                CREATE INDEX IF NOT EXISTS drawings_hash ON drawings(sha256);
                CREATE TABLE IF NOT EXISTS predictions (
                    id TEXT PRIMARY KEY, drawing_id TEXT NOT NULL REFERENCES drawings(id), body TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS annotations (
                    id TEXT PRIMARY KEY, drawing_id TEXT NOT NULL REFERENCES drawings(id), body TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS annotation_history (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT, annotation_id TEXT NOT NULL REFERENCES annotations(id),
                    body TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS consent_history (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT, drawing_id TEXT NOT NULL REFERENCES drawings(id),
                    body TEXT NOT NULL);
            ''')

    @contextmanager
    def _tx(self):
        with self.lock:
            db = sqlite3.connect(str(self.db_path), timeout=10, isolation_level=None)
            db.row_factory = sqlite3.Row
            db.execute('PRAGMA foreign_keys=ON')
            try:
                db.execute('BEGIN IMMEDIATE')
                yield db
                db.commit()
            except Exception:
                db.rollback()
                raise
            finally:
                db.close()

    @staticmethod
    def _drawing(db, drawing_id):
        identifier(drawing_id)
        row = db.execute('SELECT * FROM drawings WHERE id=?', (drawing_id,)).fetchone()
        if not row:
            raise StoreError('ไม่พบแบบ', 404)
        body = json.loads(row['body'])
        group = db.execute('SELECT split FROM groups WHERE id=?', (body['group_id'],)).fetchone()
        body['split'] = group['split']
        return body

    @staticmethod
    def _annotation(db, annotation_id):
        identifier(annotation_id)
        row = db.execute('SELECT body FROM annotations WHERE id=?', (annotation_id,)).fetchone()
        if not row:
            raise StoreError('ไม่พบรายการตรวจ', 404)
        return json.loads(row['body'])

    @staticmethod
    def _record(db, annotation, event, actor, comment=''):
        payload = {'event': event, 'version': annotation['version'], 'actor_ref': actor,
                   'comment': comment, 'created_at': annotation['updated_at'], 'snapshot': annotation}
        db.execute('INSERT INTO annotation_history(annotation_id,body) VALUES (?,?)',
                   (annotation['id'], pack(payload)))

    def register(self, raw: bytes, metadata: dict, page_count: int):
        if not isinstance(metadata, dict):
            raise StoreError('metadata ต้องเป็น object')
        if not isinstance(raw, bytes) or not 1 <= len(raw) <= self.MAX_SOURCE:
            raise StoreError('ไฟล์ต้องมีขนาดไม่เกิน 30 MB')
        if type(page_count) is not int or not 1 <= page_count <= 200:
            raise StoreError('รองรับแบบ 1–200 หน้า')
        if not boolean(metadata, 'allow_storage'):
            raise StoreError('ต้องยินยอมจัดเก็บแบบก่อน', 403)
        code = slug(metadata.get('code'), 'code')
        revision = slug(metadata.get('revision'), 'revision')
        group_id = slug(metadata.get('group_id'), 'group_id')
        title = text(metadata.get('title', code), 'title', 200, True)
        category = text(metadata.get('category', 'other'), 'category', 80, True)
        allowed_ai = boolean(metadata, 'allow_ai')
        allowed_training = boolean(metadata, 'allow_training')
        privacy = boolean(metadata, 'privacy_confirmed')
        # Storing original drawings requires the operator to attest redaction before persistence.
        if not privacy:
            raise StoreError('ต้องตรวจและยืนยันว่าปกปิดข้อมูลส่วนบุคคลในแบบแล้ว', 403)
        ident = uuid.uuid4().hex
        digest = hashlib.sha256(raw).hexdigest()
        stamp = now()
        body = {'id': ident, 'code': code, 'title': title, 'revision': revision, 'group_id': group_id,
                'category': category, 'source_hash': digest, 'sha256': digest, 'page_count': page_count,
                'allow_storage': True, 'allow_ai': allowed_ai, 'allow_training': allowed_training,
                'privacy_confirmed': privacy, 'consent_version': 1, 'created_at': stamp, 'updated_at': stamp}
        target = self.files / (ident + '.pdf')
        created = False
        try:
            with self._tx() as db:
                if db.execute('SELECT 1 FROM drawings WHERE code=? AND revision=?', (code, revision)).fetchone():
                    raise StoreError('รหัสแบบและ revision นี้มีอยู่แล้ว กรุณาใช้ revision ใหม่', 409)
                previous_code = db.execute('SELECT group_id FROM drawings WHERE code=?', (code,)).fetchone()
                if previous_code and previous_code['group_id'] != group_id:
                    raise StoreError('revision ของรหัสแบบเดียวกันต้องอยู่ group เดียวกัน', 409)
                duplicate = db.execute('SELECT group_id FROM drawings WHERE sha256=?', (digest,)).fetchone()
                if duplicate and duplicate['group_id'] != group_id:
                    raise StoreError('ไฟล์ซ้ำต้องอยู่ group เดียวกันเพื่อป้องกันข้อมูลรั่วข้ามชุดทดสอบ', 409)
                if db.execute('SELECT COUNT(*) FROM drawings').fetchone()[0] >= 10000:
                    raise StoreError('จำนวนแบบเกินขีดจำกัดของรุ่นทดลอง', 413)
                db.execute('INSERT OR IGNORE INTO groups(id,created_at) VALUES (?,?)', (group_id, stamp))
                with target.open('xb') as output:
                    output.write(raw)
                created = True
                db.execute('INSERT INTO drawings VALUES (?,?,?,?,?,?,?,?)',
                           (ident, code, revision, group_id, digest, target.name, page_count, pack(body)))
                db.execute('INSERT INTO consent_history(drawing_id,body) VALUES (?,?)',
                           (ident, pack({'version': 1, 'created_at': stamp, 'allow_ai': allowed_ai,
                                         'allow_training': allowed_training, 'privacy_confirmed': privacy})))
        except Exception:
            if created:
                target.unlink(missing_ok=True)
            raise
        with self._tx() as db:
            body['split'] = db.execute('SELECT split FROM groups WHERE id=?', (group_id,)).fetchone()[0]
        return body

    def list_drawings(self):
        with self._tx() as db:
            out = []
            for row in db.execute('SELECT id FROM drawings ORDER BY rowid DESC'):
                body = self._drawing(db, row['id'])
                annotations = [json.loads(item['body']) for item in db.execute(
                    'SELECT body FROM annotations WHERE drawing_id=?', (body['id'],))]
                body.update(annotation_count=len(annotations), approved_count=sum(
                    item['status'] == 'approved' for item in annotations), prediction_count=db.execute(
                    'SELECT COUNT(*) FROM predictions WHERE drawing_id=?', (body['id'],)).fetchone()[0])
                out.append(body)
            return out

    def get_drawing(self, drawing_id):
        with self._tx() as db:
            body = self._drawing(db, drawing_id)
            body['annotations'] = [json.loads(row['body']) for row in db.execute(
                'SELECT body FROM annotations WHERE drawing_id=? ORDER BY rowid', (drawing_id,))]
            body['predictions'] = [json.loads(row['body']) for row in db.execute(
                'SELECT body FROM predictions WHERE drawing_id=? ORDER BY rowid', (drawing_id,))]
            return body

    def pdf_path(self, drawing_id):
        with self._tx() as db:
            drawing = self._drawing(db, drawing_id)
            relative = db.execute('SELECT source_rel FROM drawings WHERE id=?', (drawing_id,)).fetchone()[0]
        path = self.files / relative
        if (self.files.is_symlink() or (hasattr(self.files, 'is_junction') and self.files.is_junction())
                or not path.resolve().is_relative_to(self.root) or not path.resolve().is_relative_to(self.files.resolve())
                or path.is_symlink() or not path.is_file()):
            raise StoreError('ไม่พบต้นฉบับที่ปลอดภัย', 404)
        if hashlib.sha256(path.read_bytes()).hexdigest() != drawing['sha256']:
            raise StoreError('ต้นฉบับในคลังเปลี่ยนไปจาก revision ที่ลงทะเบียน หยุดเพื่อป้องกันอ้างหลักฐานผิดรุ่น', 409)
        return path

    def add_prediction(self, drawing_id, report, model_version, prompt_version):
        if not isinstance(report, dict):
            raise StoreError('ผล AI ต้องเป็น object')
        encoded = pack(report)
        if len(encoded.encode('utf-8')) > 250000 or SENSITIVE.search(encoded):
            raise StoreError('ผล AI มีขนาดเกินกำหนดหรืออาจมีข้อมูลส่วนบุคคล/ข้อมูลลับ')
        body = {'id': uuid.uuid4().hex, 'drawing_id': drawing_id, 'report': report,
                'model_version': text(model_version, 'model_version', 120, True),
                'prompt_version': text(prompt_version, 'prompt_version', 120, True), 'created_at': now()}
        with self._tx() as db:
            drawing = self._drawing(db, drawing_id)
            if not drawing['allow_ai'] or not drawing['privacy_confirmed']:
                raise StoreError('ยังไม่ได้รับอนุญาตให้ส่งแบบไปยัง AI', 403)
            if db.execute('SELECT COUNT(*) FROM predictions WHERE drawing_id=?', (drawing_id,)).fetchone()[0] >= 100:
                raise StoreError('ผล AI ของแบบนี้เกินขีดจำกัด', 413)
            db.execute('INSERT INTO predictions VALUES (?,?,?)', (body['id'], drawing_id, pack(body)))
        return body

    @staticmethod
    def _evidence(evidence, page_count):
        if not isinstance(evidence, list) or len(evidence) > 20:
            raise StoreError('evidence ต้องเป็นรายการไม่เกิน 20 ตำแหน่ง')
        output = []
        for item in evidence:
            if not isinstance(item, dict) or set(item) - {'page', 'bbox', 'view'}:
                raise StoreError('หลักฐานไม่ถูกต้อง')
            page = item.get('page')
            bbox = item.get('bbox')
            if type(page) is not int or not 1 <= page <= page_count:
                raise StoreError('หน้าอ้างอิงอยู่นอกแบบ')
            if not isinstance(bbox, list) or len(bbox) != 4 or any(
                    type(value) not in (int, float) or not math.isfinite(value) for value in bbox):
                raise StoreError('bbox ต้องเป็นตัวเลขจำกัด 4 ค่า')
            x, y, width, height = bbox
            if min(x, y) < 0 or min(width, height) <= 0 or x+width > 1.000000001 or y+height > 1.000000001:
                raise StoreError('bbox ต้องอยู่ภายในภาพในหน่วย normalized 0–1')
            output.append({'page': page, 'bbox': [float(value) for value in bbox],
                           'view': text(item.get('view', ''), 'view', 120)})
        return output

    def save_annotation(self, drawing_id, payload):
        if not isinstance(payload, dict):
            raise StoreError('คำตอบต้องเป็น object')
        with self._tx() as db:
            drawing = self._drawing(db, drawing_id)
            original = None
            annotation_id = payload.get('id')
            if annotation_id:
                original = self._annotation(db, annotation_id)
                if original['drawing_id'] != drawing_id:
                    raise StoreError('รายการตรวจไม่อยู่ในแบบนี้', 404)
                if version(payload.get('expected_version')) != original['version']:
                    raise StoreError('ข้อมูลเปลี่ยนแล้ว กรุณาโหลดรุ่นล่าสุด', 409)
            elif db.execute('SELECT COUNT(*) FROM annotations WHERE drawing_id=?', (drawing_id,)).fetchone()[0] >= 5000:
                raise StoreError('จำนวนรายการตรวจเกินขีดจำกัด', 413)
            task_type = payload.get('task_type', 'question')
            evidence_type = payload.get('evidence_type')
            if not isinstance(task_type, str) or not isinstance(evidence_type, str) or task_type not in TASKS or evidence_type not in EVIDENCE_TYPES:
                raise StoreError('ประเภทงานหรือประเภทหลักฐานไม่ถูกต้อง')
            prediction_id = payload.get('ai_prediction_id') or None
            if prediction_id:
                identifier(prediction_id)
                prediction = db.execute('SELECT drawing_id FROM predictions WHERE id=?', (prediction_id,)).fetchone()
                if not prediction or prediction['drawing_id'] != drawing_id:
                    raise StoreError('ผล AI ไม่อยู่ในแบบนี้')
            evidence = self._evidence(payload.get('evidence', []), drawing['page_count'])
            stamp = now()
            body = {'id': annotation_id or uuid.uuid4().hex, 'drawing_id': drawing_id,
                    'version': original['version']+1 if original else 1, 'status': 'draft',
                    'task_type': task_type, 'question': text(payload.get('question', ''), 'question', 2000, True),
                    'part_ref': text(payload.get('part_ref', ''), 'part_ref', 120),
                    'ai_prediction_id': prediction_id, 'ai_value': text(payload.get('ai_value', ''), 'ai_value', 4000),
                    'corrected_value': text(payload.get('corrected_value', ''), 'corrected_value', 4000),
                    'unit': text(payload.get('unit', ''), 'unit', 40), 'evidence_type': evidence_type,
                    'formula': text(payload.get('formula', ''), 'formula', 2000),
                    'explanation': text(payload.get('explanation', ''), 'explanation', 4000), 'evidence': evidence,
                    'author_ref': slug(payload.get('author_ref'), 'author_ref'), 'privacy_confirmed': False,
                    'created_at': original['created_at'] if original else stamp, 'updated_at': stamp}
            if original:
                db.execute('UPDATE annotations SET body=? WHERE id=?', (pack(body), annotation_id))
            else:
                db.execute('INSERT INTO annotations VALUES (?,?,?)', (body['id'], drawing_id, pack(body)))
            self._record(db, body, 'edit' if original else 'create', body['author_ref'])
        return body

    @staticmethod
    def _ready(annotation):
        kind = annotation['evidence_type']
        if not annotation['evidence']:
            raise StoreError('ต้องระบุหน้าหลักฐานอย่างน้อยหนึ่งหน้า ใช้กรอบเต็มหน้าสำหรับข้อมูลไม่พอได้')
        if kind == 'insufficient':
            if not annotation['explanation']:
                raise StoreError('ข้อมูลไม่พอต้องระบุเหตุผล')
            if annotation['corrected_value']:
                raise StoreError('ข้อมูลไม่พอให้เว้นค่าคำตอบ ไม่กรอกค่าที่เดา')
        else:
            if not annotation['corrected_value']:
                raise StoreError('ต้องระบุคำตอบที่ตรวจแล้ว')
            if not annotation['evidence']:
                raise StoreError('ต้องระบุหน้าและกรอบหลักฐาน')
            if kind == 'calculated' and (not annotation['formula'] or not annotation['explanation']):
                raise StoreError('คำตอบคำนวณต้องมีสูตรและคำอธิบายค่าต้นทาง')
            if kind == 'inferred' and not annotation['explanation']:
                raise StoreError('ข้ออนุมานต้องมีเหตุผล')

    def review_annotation(self, annotation_id, payload):
        if not isinstance(payload, dict):
            raise StoreError('คำขอตรวจรับต้องเป็น object')
        decision = payload.get('decision')
        if not isinstance(decision, str) or decision not in {'submit', 'approve', 'reject', 'changes_requested'}:
            raise StoreError('การตัดสินไม่ถูกต้อง')
        actor = slug(payload.get('reviewer_ref'), 'reviewer_ref')
        comment = text(payload.get('comment', ''), 'comment', 2000)
        privacy = boolean(payload, 'privacy_confirmed')
        with self._tx() as db:
            body = self._annotation(db, annotation_id)
            if version(payload.get('expected_version')) != body['version']:
                raise StoreError('ข้อมูลเปลี่ยนแล้ว กรุณาโหลดรุ่นล่าสุด', 409)
            if decision == 'submit':
                if body['status'] != 'draft':
                    raise StoreError('ส่งตรวจได้เฉพาะ draft', 409)
                self._ready(body)
                body['status'] = 'submitted'
            else:
                if body['status'] != 'submitted':
                    raise StoreError('ต้องส่งตรวจรุ่นปัจจุบันก่อน', 409)
                if decision == 'approve':
                    if actor == body['author_ref']:
                        raise StoreError('ผู้ทบทวนต้องใช้รหัสต่างจากผู้จัดทำ (เป็น workflow local ไม่ใช่การยืนยันตัวตน)', 403)
                    if not privacy:
                        raise StoreError('ต้องยืนยันว่าคำตอบและหลักฐานปกปิดข้อมูลส่วนบุคคลแล้ว', 403)
                    self._ready(body)
                    body['privacy_confirmed'] = True
                    body['status'] = 'approved'
                else:
                    if not comment:
                        raise StoreError('ต้องระบุเหตุผลที่ส่งกลับหรือปฏิเสธ')
                    body['status'] = decision
                    body['privacy_confirmed'] = False
            body['version'] += 1
            body['updated_at'] = now()
            db.execute('UPDATE annotations SET body=? WHERE id=?', (pack(body), annotation_id))
            self._record(db, body, decision, actor, comment)
        return body

    def history(self, annotation_id):
        with self._tx() as db:
            self._annotation(db, annotation_id)
            return [json.loads(row['body']) for row in db.execute(
                'SELECT body FROM annotation_history WHERE annotation_id=? ORDER BY sequence', (annotation_id,))]

    def set_consent(self, drawing_id, payload):
        if not isinstance(payload, dict) or set(payload) - {'allow_ai', 'allow_training', 'privacy_confirmed', 'expected_version'}:
            raise StoreError('ข้อมูล consent ไม่ถูกต้อง')
        with self._tx() as db:
            body = self._drawing(db, drawing_id)
            if 'expected_version' in payload and version(payload['expected_version']) != body['consent_version']:
                raise StoreError('consent เปลี่ยนแล้ว กรุณาโหลดใหม่', 409)
            for key in ('allow_ai', 'allow_training', 'privacy_confirmed'):
                if key in payload:
                    body[key] = boolean(payload, key)
            body['consent_version'] += 1
            body['updated_at'] = now()
            db.execute('UPDATE drawings SET body=? WHERE id=?', (pack(body), drawing_id))
            event = {key: body[key] for key in ('allow_ai', 'allow_training', 'privacy_confirmed', 'consent_version', 'updated_at')}
            db.execute('INSERT INTO consent_history(drawing_id,body) VALUES (?,?)', (drawing_id, pack(event)))
        return body

    def assign_split(self, group_id, split):
        group_id = slug(group_id, 'group_id')
        if not isinstance(split, str) or split not in {'train', 'dev', 'test'}:
            raise StoreError('split ต้องเป็น train, dev หรือ test')
        with self._tx() as db:
            row = db.execute('SELECT * FROM groups WHERE id=?', (group_id,)).fetchone()
            if not row:
                raise StoreError('ไม่พบ group', 404)
            if row['split'] not in {'unassigned', split}:
                raise StoreError('group นี้กำหนดชุดแล้ว เปลี่ยนไม่ได้เพื่อป้องกันข้อมูลรั่วข้ามชุด', 409)
            db.execute('UPDATE groups SET split=? WHERE id=?', (split, group_id))
        return {'group_id': group_id, 'split': split}

    def export_samples(self):
        samples, excluded = [], Counter()
        with self._tx() as db:
            for row in db.execute('SELECT id,drawing_id,body FROM annotations ORDER BY rowid'):
                annotation = json.loads(row['body'])
                drawing = self._drawing(db, row['drawing_id'])
                reason = None
                if annotation['status'] != 'approved': reason = 'not_approved'
                elif not drawing['allow_training']: reason = 'training_not_allowed'
                elif not drawing['privacy_confirmed'] or not annotation['privacy_confirmed']: reason = 'privacy_not_confirmed'
                elif annotation['evidence_type'] == 'inferred': reason = 'inferred'
                elif drawing['split'] == 'unassigned': reason = 'split_unassigned'
                if reason:
                    excluded[reason] += 1
                    continue
                sample = {'schema_version': 1, 'example_id': annotation['id'], 'annotation_version': annotation['version'],
                          'drawing_id': drawing['id'], 'source_hash': drawing['source_hash'], 'source_revision':drawing['revision'],
                          'group_id': drawing['group_id'], 'split': drawing['split'],
                          'task_type': annotation['task_type'], 'question': annotation['question'],
                          'target': {'value': annotation['corrected_value'], 'unit': annotation['unit'],
                                     'evidence_type': annotation['evidence_type'], 'formula': annotation['formula'],
                                     'explanation': annotation['explanation']}, 'evidence': annotation['evidence']}
                if SENSITIVE.search(pack(sample)):
                    excluded['unsafe_text'] += 1
                else:
                    samples.append(sample)
        return {'schema_version': 1, 'samples': samples, 'excluded': dict(excluded)}

    def stats(self):
        with self._tx() as db:
            states = Counter(json.loads(row['body'])['status'] for row in db.execute('SELECT body FROM annotations'))
            result = {'drawings': db.execute('SELECT COUNT(*) FROM drawings').fetchone()[0],
                      'groups': db.execute('SELECT COUNT(*) FROM groups').fetchone()[0],
                      'predictions': db.execute('SELECT COUNT(*) FROM predictions').fetchone()[0],
                      'annotations': sum(states.values()), 'approved': states['approved'],
                      'submitted': states['submitted'], 'draft': states['draft'], 'states': dict(states)}
        result['training_ready'] = len(self.export_samples()['samples'])
        return result

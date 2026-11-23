'use strict';

const byId = id => document.getElementById(id);
const canvas = byId('evidence');
const context = canvas.getContext('2d');
const width = byId('width');
const split = byId('split');
const status = byId('status');
const approve = byId('approve');
const exportButton = byId('export');
const evidenceStatus = byId('evidence-status');
let region = null;
let drag = null;
let submitted = null;
let approved = null;

function draw(box = region) {
  context.clearRect(0, 0, canvas.width, canvas.height);
  if (!box) return;
  const [x, y, w, h] = box;
  context.fillStyle = '#16876828';
  context.strokeStyle = '#186249';
  context.lineWidth = 2;
  context.fillRect(x * canvas.width, y * canvas.height, w * canvas.width, h * canvas.height);
  context.strokeRect(x * canvas.width, y * canvas.height, w * canvas.width, h * canvas.height);
}

new ResizeObserver(() => {
  canvas.width = canvas.clientWidth;
  canvas.height = canvas.clientHeight;
  draw();
}).observe(canvas);

function invalidate() {
  submitted = null;
  approved = null;
  approve.disabled = true;
  exportButton.disabled = true;
  status.textContent = 'บันทึกการเปลี่ยนแปลงแล้วในหน้านี้ กรุณาส่งตรวจ';
}

function applyRegion(box) {
  region = box;
  evidenceStatus.textContent = box ? 'เลือกหลักฐานแล้ว · หน้า 1' : 'ลากกรอบรอบตัวเลขความกว้างบนแบบ หรือใช้ปุ่มด้านล่าง';
  invalidate();
  draw();
}

function position(event) {
  const rect = canvas.getBoundingClientRect();
  const clamp = value => Math.max(0, Math.min(1, value));
  return [clamp((event.clientX - rect.left) / rect.width), clamp((event.clientY - rect.top) / rect.height)];
}

function bounds([x, y], [lastX, lastY]) {
  return [Math.min(x, lastX), Math.min(y, lastY), Math.abs(x - lastX), Math.abs(y - lastY)];
}

canvas.addEventListener('pointerdown', event => {
  drag = position(event);
  canvas.setPointerCapture(event.pointerId);
});
canvas.addEventListener('pointermove', event => {
  if (drag) draw(bounds(drag, position(event)));
});
canvas.addEventListener('pointerup', event => {
  if (!drag) return;
  const box = bounds(drag, position(event));
  drag = null;
  if (box[2] > .01 && box[3] > .01) applyRegion(box);
  else draw();
});
canvas.addEventListener('pointercancel', () => { drag = null; draw(); });
byId('use-region').onclick = () => applyRegion([.18347, .21988, .43882, .07604]);
byId('clear-region').onclick = () => applyRegion(null);
width.addEventListener('input', invalidate);
split.addEventListener('change', invalidate);

function record(message) {
  status.textContent = message;
  const item = document.createElement('li');
  item.textContent = message;
  byId('history').append(item);
}

byId('review').addEventListener('submit', event => {
  event.preventDefault();
  if (!region) {
    status.textContent = 'กรุณาชี้หลักฐานก่อนส่งตรวจ';
    return;
  }
  if (width.valueAsNumber !== 900) {
    status.textContent = 'ลองอ่านตัวเลขบนแบบอีกครั้ง แล้วแก้ขนาดให้ตรงกับหลักฐาน';
    return;
  }
  submitted = {
    example_id: 'cabinet-synthetic-1',
    source_revision: 'B',
    task_type: 'dimension',
    split: split.value,
    question: 'ตู้กว้างเท่าไร?',
    target: {value: width.value, unit: 'มม.', evidence_type: 'direct'},
    evidence: [{page: 1, bbox: region.map(value => Number(value.toFixed(5))), image: 'cabinet-demo.png'}]
  };
  approved = null;
  approve.disabled = false;
  exportButton.disabled = true;
  record('ส่งตรวจความกว้าง 900 มม.');
});

approve.onclick = () => {
  if (!submitted) return;
  approved = submitted;
  approve.disabled = true;
  exportButton.disabled = false;
  record('อนุมัติคำตอบและหลักฐานแล้ว');
};

exportButton.onclick = () => {
  if (!approved) return;
  const url = URL.createObjectURL(new Blob([JSON.stringify(approved) + '\n'], {type: 'application/jsonl'}));
  const download = (href, name) => {
    const link = document.createElement('a');
    link.href = href;
    link.download = name;
    link.click();
  };
  download(url, approved.split + '-cabinet-demo.jsonl');
  setTimeout(() => URL.revokeObjectURL(url), 5000);
  download('cabinet-demo.png', 'cabinet-demo.png');
  record('ส่งออกตัวอย่างและภาพอ้างอิงแล้ว');
};

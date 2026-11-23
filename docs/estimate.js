import {estimateMaterials} from './material-estimator.js';

const element = id => document.getElementById(id);
const tbody = element('parts');
const waste = element('waste');
const output = element('estimate-output');
const download = element('estimate-download');
const context = element('report-context');
const importStatus = element('import-status');
let sourceReport = null;
let result = null;
let rowSequence = 0;

const fields = [
  ['name', 'ชิ้นส่วน'], ['material', 'วัสดุ'], ['thickness_mm', 'ความหนา (มม.)', 'any'],
  ['width_mm', 'ด้านสั้น (มม.)', 'any'], ['length_mm', 'ด้านยาว (มม.)', 'any'],
  ['quantity', 'จำนวน', '1'], ['long_edges', 'ขอบยาว', '1'], ['short_edges', 'ขอบสั้น', '1'],
  ['evidence', 'หลักฐาน']
];

function invalidate() {
  result = null;
  download.disabled = true;
  output.replaceChildren();
}

function addPart(part = {}) {
  if (tbody.rows.length >= 300) {
    importStatus.textContent = 'รองรับสูงสุด 300 ชิ้นส่วนต่อรายงาน';
    return;
  }
  const row = tbody.insertRow();
  const rowNumber = ++rowSequence;
  row.sourcePart = part;
  for (const [key, label, step] of fields) {
    const input = document.createElement('input');
    input.setAttribute('aria-label', `${label} รายการ ${rowNumber}`);
    input.name = key;
    input.type = step ? 'number' : 'text';
    if (step) {
      input.step = step;
      input.min = '0';
      input.max = key.endsWith('_edges') ? '2' : '10000000';
    }
    input.value = part[key] ?? '';
    const cell = row.insertCell();
    cell.append(input);
    if (key === 'evidence' && Array.isArray(part.source_pages) && part.source_pages.length) {
      const pages = document.createElement('small');
      pages.textContent = 'หน้า ' + part.source_pages.join(', ');
      cell.append(pages);
    }
  }
  const remove = document.createElement('button');
  remove.type = 'button';
  remove.className = 'secondary';
  remove.textContent = 'ลบ';
  remove.setAttribute('aria-label', `ลบรายการ ${rowNumber}`);
  remove.onclick = () => { row.remove(); invalidate(); };
  row.insertCell().append(remove);
}

function loadReport(report, wastePercent = 12) {
  if (!Array.isArray(report?.parts) || report.parts.length > 300 || report.parts.some(part => !part || typeof part !== 'object' || Array.isArray(part))) {
    throw new Error('รายงานต้องมีรายการ parts จาก ThaiFurnitureAnalyzer ไม่เกิน 300 รายการ');
  }
  sourceReport = report;
  rowSequence = 0;
  tbody.replaceChildren();
  report.parts.forEach(addPart);
  if (!report.parts.length) addPart();
  waste.value = Number.isFinite(wastePercent) && wastePercent >= 0 && wastePercent <= 50 ? wastePercent : 12;
  const reportDetails = element('report-details');
  reportDetails.replaceChildren();
  for (const [key, title] of [['assumptions', 'สมมติฐาน'], ['hardware', 'อุปกรณ์'], ['checks', 'รายการตรวจ']]) {
    if (!Array.isArray(report[key]) || !report[key].length) continue;
    const heading = document.createElement('h3');
    heading.textContent = title;
    const list = document.createElement('ul');
    for (const item of report[key].slice(0, 300)) {
      if (!item || typeof item !== 'object') continue;
      const line = document.createElement('li');
      line.textContent = [item.id, item.item, item.name, item.value, item.reason, item.issue, item.evidence,
        item.quantity, item.unit, Array.isArray(item.source_pages) ? 'หน้า ' + item.source_pages.join(', ') : '']
        .filter(value => typeof value === 'number' || typeof value === 'string' && value).join(' · ');
      list.append(line);
    }
    reportDetails.append(heading, list);
  }
  context.hidden = !reportDetails.children.length;
  invalidate();
}

element('report-file').onchange = async event => {
  const input = event.currentTarget;
  const file = input.files[0];
  if (!file) return;
  input.value = '';
  try {
    if (file.size > 5 * 1024 * 1024) throw new Error('รองรับ JSON ไม่เกิน 5 MB');
    const data = JSON.parse((await file.text()).replace(/^\uFEFF/, ''));
    loadReport(data.report ?? data, data.calculation?.waste_percent ?? data.waste_percent);
    importStatus.textContent = 'นำเข้ารายงานแล้ว ตรวจชิ้นส่วนและกดคำนวณวัสดุ';
  } catch (error) {
    importStatus.textContent = error instanceof SyntaxError ? 'ไฟล์ไม่ใช่ JSON ที่อ่านได้' : error.message;
  }
};

element('estimate-sample').onclick = () => {
  loadReport({
    parts: [{name: 'แผ่นกั้นตัวอย่าง', material: 'MDF', thickness_mm: 18, width_mm: 400,
      length_mm: 900, quantity: 2, long_edges: 1, short_edges: 2, source_pages: [1],
      evidence: '900 มม. จากแบบสังเคราะห์ ส่วนขนาดอื่นและจำนวนใช้สาธิตสูตร', assumption_ids: ['DEMO-1']}],
    assumptions: [{id: 'DEMO-1', item: 'ความลึก ความหนา จำนวน และจำนวนขอบ', reason: 'กำหนดเพื่อสาธิตการประมาณวัสดุ'}]
  });
  importStatus.textContent = 'โหลดชิ้นส่วนสังเคราะห์แล้ว กดคำนวณวัสดุเพื่อดูผล';
};
element('add-part').onclick = () => { addPart(); invalidate(); };
element('estimate-form').addEventListener('input', invalidate);
element('estimate-form').onsubmit = event => {
  event.preventDefault();
  const parts = [...tbody.rows].map(row => {
    const input = Object.fromEntries([...row.querySelectorAll('input')].map(field => [
      field.name, field.type === 'number' ? (Number.isFinite(field.valueAsNumber) ? field.valueAsNumber : null) : field.value.trim()
    ]));
    return {...row.sourcePart, ...input};
  });
  const calculation = estimateMaterials(parts, waste.valueAsNumber);
  result = {report: {...sourceReport, parts}, calculation};
  output.replaceChildren();
  const explanation = document.createElement('p');
  explanation.textContent = 'จำนวนแผ่น 1220 × 2440 มม. ประมาณจากพื้นที่รวมเผื่อสูญเสีย ยังต้องตรวจการจัดวาง แนวลายและรอยตัดก่อนสั่งผลิต';
  const breakdown = document.createElement('ul');
  const list = document.createElement('ul');
  const fmt = number => number.toLocaleString('th-TH', {maximumFractionDigits: 4});
  for (const part of calculation.parts) {
    const item = document.createElement('li');
    const area = part.area_m2 === null ? 'พื้นที่ยังคำนวณไม่ได้' : `พื้นที่ ${fmt(part.area_m2)} ตร.ม.`;
    const edge = part.edge_m === null ? 'ขอบยังคำนวณไม่ได้' : `ขอบ ${fmt(part.edge_m)} ม.`;
    item.textContent = `${part.name || 'ชิ้นส่วนไม่ระบุชื่อ'} · ${area} · ${edge}`;
    breakdown.append(item);
  }
  for (const group of calculation.materials) {
    const item = document.createElement('li');
    item.textContent = `${group.material} ${group.thickness_mm} มม. · สุทธิ ${fmt(group.net_area_m2)} ตร.ม. · เผื่อแล้ว ${fmt(group.gross_area_m2)} ตร.ม. · ประมาณ ${group.area_based_sheets} แผ่น · ขอบ ${fmt(group.edge_m)} ม.` + (group.edge_unknown ? ' (ยังมีรายการไม่ครบ)' : '');
    list.append(item);
  }
  if (!list.children.length) {
    const item = document.createElement('li');
    item.textContent = 'ยังไม่มีรายการไม้แผ่นที่ข้อมูลครบสำหรับประมาณจำนวนแผ่น';
    list.append(item);
  }
  for (const warning of calculation.warnings) {
    const item = document.createElement('li');
    item.textContent = warning;
    list.append(item);
  }
  const partsTitle = document.createElement('h3');
  partsTitle.textContent = 'พื้นที่และขอบรายชิ้น';
  const totalTitle = document.createElement('h3');
  totalTitle.textContent = 'สรุปวัสดุและข้อที่ต้องตรวจ';
  if (breakdown.children.length) output.append(partsTitle, breakdown);
  output.append(totalTitle, explanation, list);
  download.disabled = false;
};

download.onclick = () => {
  if (!result) return;
  const url = URL.createObjectURL(new Blob([JSON.stringify(result, null, 2)], {type: 'application/json'}));
  const link = document.createElement('a');
  link.href = url;
  link.download = 'furniture-material-estimate.json';
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 5000);
};
addPart();

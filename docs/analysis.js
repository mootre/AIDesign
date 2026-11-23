const find = id => document.getElementById(id);
const file = find('pdf');
const sample = find('sample');
const status = find('file-status');
const results = find('results');
const preview = find('preview');
const previous = find('previous');
const next = find('next');
const download = find('download-text');
let pdf;
let currentPage = 1;
let pageText = '';
let library;

function setBusy(busy) {
  file.disabled = sample.disabled = busy;
  previous.disabled = busy || currentPage === 1;
  next.disabled = busy || !pdf || currentPage === pdf.numPages;
  download.disabled = busy || !pageText;
}

async function showPage(number) {
  setBusy(true);
  try {
    const page = await pdf.getPage(number);
    const original = page.getViewport({scale: 1});
    const viewport = page.getViewport({scale: Math.min(2, 1800 / Math.max(original.width, original.height))});
    preview.width = Math.ceil(viewport.width);
    preview.height = Math.ceil(viewport.height);
    await page.render({canvas: preview, viewport}).promise;
    const text = await page.getTextContent();
    pageText = text.items.map(item => item.str === undefined ? '' : item.str + (item.hasEOL ? '\n' : ' ')).join('').trim();
    currentPage = number;
    find('page-number').textContent = `หน้า ${number} / ${pdf.numPages}`;
    find('extracted').textContent = pageText;
    const candidates = pageText.match(/(?<![\d.,])\d+(?:[.,]\d+)*(?:\s*[×xX]\s*\d+(?:[.,]\d+)*)*\s*(?:มิลลิเมตร|เซนติเมตร|มม\.?|ซม\.?|นิ้ว|mm\b|cm\b|inch(?:es)?\b)/giu) || [];
    find('dimensions').replaceChildren(...Array.from(new Set(candidates.map(value => value.replace(/\s+/g, ' '))), value => {
      const row = document.createElement('li');
      row.textContent = value;
      return row;
    }));
    find('text-note').textContent = !pageText
      ? 'หน้านี้ไม่มีข้อความฝังอยู่ หากเป็นภาพสแกนให้ใช้ OCR ในโปรแกรมบนเครื่อง'
      : candidates.length ? 'ตรวจความหมายและตำแหน่งของตัวเลขกับต้นฉบับก่อนใช้งาน'
        : 'พบข้อความ แต่ไม่พบตัวเลขที่ระบุหน่วยขนาดในรูปแบบที่รองรับ';
    status.textContent = `เปิดแบบแล้ว · ${pdf.numPages} หน้า`;
  } catch {
    pageText = '';
    results.hidden = true;
    status.textContent = 'แสดงหน้านี้ไม่สำเร็จ กรุณาเลือกไฟล์อีกครั้ง';
  } finally {
    setBusy(false);
  }
}

async function openPdf(readBytes) {
  results.hidden = true;
  pageText = '';
  setBusy(true);
  status.textContent = 'กำลังเปิด PDF…';
  try {
    if (pdf) await pdf.destroy();
    pdf = undefined;
    library ??= await import('./vendor/pdfjs/pdf.min.js');
    library.GlobalWorkerOptions.workerSrc = new URL('./vendor/pdfjs/pdf.worker.min.js', import.meta.url).href;
    const bytes = new Uint8Array(await readBytes());
    pdf = await library.getDocument({data: bytes, isEvalSupported: false, enableXfa: false}).promise;
    currentPage = 1;
    results.hidden = false;
    await showPage(1);
  } catch {
    status.textContent = 'เปิด PDF ไม่สำเร็จ กรุณาตรวจว่าไฟล์เปิดได้และไม่มีรหัสผ่าน';
  } finally {
    setBusy(false);
  }
}

file.addEventListener('change', () => {
  const selected = file.files[0];
  if (!selected) return;
  if (selected.size > 50 * 1024 * 1024) {
    status.textContent = 'กรุณาเลือก PDF ขนาดไม่เกิน 50 MB';
    return;
  }
  void openPdf(() => selected.arrayBuffer());
  file.value = '';
});

sample.onclick = () => openPdf(async () => {
  const response = await fetch('cabinet-demo.pdf');
  if (!response.ok) throw new Error('Sample unavailable');
  return response.arrayBuffer();
});
previous.onclick = () => showPage(currentPage - 1);
next.onclick = () => showPage(currentPage + 1);
download.onclick = () => {
  const url = URL.createObjectURL(new Blob([pageText], {type: 'text/plain;charset=utf-8'}));
  const link = document.createElement('a');
  link.href = url;
  link.download = `drawing-page-${currentPage}.txt`;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 5000);
};

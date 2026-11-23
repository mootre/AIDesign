'use strict';
let drawingReady=false,drawingBusy=false,drawingPolling=false;
const baseAiControls=aiControls;
aiControls=function(){
 baseAiControls();
 const create=document.getElementById('createDrawing');
 create.disabled=!drawingReady||!aiReady||!currentAnalysis||aiBusy||chatPolling||drawingBusy;
 document.getElementById('isometricDrawing').disabled=!drawingReady||!aiReady||!selected||busy||aiBusy||chatPolling||drawingBusy;
 document.getElementById('vectorDrawing').disabled=!drawingReady||!selected||busy||aiBusy||chatPolling||drawingBusy;
 if(drawingBusy){
  ['analyze','read','ask','forget','file','recalc','vectorDrawing','isometricDrawing'].forEach(id=>{const n=document.getElementById(id);if(n)n.disabled=true});
  document.getElementById('aiCancel').hidden=false;
 }
};
const originalRenderAnalysis=renderAnalysis;
renderAnalysis=function(j){originalRenderAnalysis(j);renderDrawing(j)};
const originalAnalyzeClick=document.getElementById('analyze').onclick;
document.getElementById('analyze').onclick=async()=>{
 const previous=analysisId;await originalAnalyzeClick();
 if(analysisId!==previous){document.getElementById('cadPanel').hidden=true;document.getElementById('cadDownloads').replaceChildren();document.getElementById('cadPreview').removeAttribute('src')}
};
const originalForgetClick=document.getElementById('forget').onclick;
document.getElementById('forget').onclick=async()=>{
 await originalForgetClick();if(!currentAnalysis){document.getElementById('cadPanel').hidden=true;document.getElementById('cadDownloads').replaceChildren();document.getElementById('cadPreview').removeAttribute('src')}
};
function renderDrawing(j){
 const panel=document.getElementById('cadPanel'),state=j.drawing_status||'idle';panel.hidden=false;
 drawingBusy=state==='running';
 document.getElementById('cadStatus').textContent=j.drawing_error||j.drawing_stage||(drawingReady?'พร้อมสร้างแบบร่างจากหน้า PDF ที่วิเคราะห์':'ยังไม่พบเครื่องมือ CAD กรุณาตรวจ cad-settings.json');
 const links=document.getElementById('cadDownloads');links.replaceChildren();
 const preview=document.getElementById('cadPreview');preview.hidden=state!=='done';preview.classList.toggle('isometric-preview',j.drawing_info?.mode==='isometric_basic');
 const details=document.getElementById('cadNotes');details.replaceChildren();
 if(state==='done'){
  for(const [kind,label] of [['dwg','ดาวน์โหลด DWG'],['dxf','ดาวน์โหลด DXF'],['json','ข้อมูลและสมมติฐาน Drawing']]){
   const a=document.createElement('a');a.href='/api/drawing/'+j.id+'/'+kind;a.download='furniture-drawing.'+kind;a.textContent=label;links.append(a);
  }
  const imageUrl='/api/drawing/'+j.id+'/svg?v='+j.drawing_info.dwg_sha256;if(preview.getAttribute('src')!==imageUrl)preview.src=imageUrl;
  details.append(node('p',j.drawing_info.mode==='measured_views'?'รูปฉาย 1:1 มม. · วัดระยะอ้างอิงจาก DWG กลับมาตรวจแล้ว':j.drawing_info.mode==='isometric_basic'?'Isometric โครงสร้างหลัก · ไม่มี Text/มิติ · สัดส่วนภาพโดยประมาณ':j.drawing_info.mode==='pdf_vector'?'นำเข้าเส้น PDF โดยตรง · หน่วย mm บนกระดาษ · ไม่ใช่ขนาดชิ้นงานจริง 1:1':'หน่วย mm · '+j.drawing_info.views+' มุมมอง · DWG '+j.drawing_info.version+' · ผ่านการแปลงกลับและตรวจมิติในไฟล์','notice'));
  if(j.drawing_info.mode==='measured_views'){
   details.append(node('h3','กลุ่มที่พบใน PDF'));
   details.append(tableView(['กลุ่ม','ประเภท','หน้า'],j.drawing_info.detected_groups.map(g=>[g.name,g.kind,g.source_pages.join(', ')])));
   details.append(tableView(['มุมมองใน DWG','เลเยอร์','วัดขนาดจริง'],j.drawing_info.view_layers.map(v=>[v.name,v.layer,v.measurable?'ได้ — รูปฉาย 1:1':'ภาพอ้างอิงเท่านั้น'])));
   details.append(node('p','วัดขนาดจริงจาก TOP / ELEVATION / INSIDE VIEW เท่านั้น ส่วน ISOMETRIC เป็นภาพอ้างอิง ระยะฉายบนภาพไม่ใช่ความยาวชิ้นงานจริง','notice'));
   details.append(node('h3','ระยะที่ตรวจวัดจาก DWG'));
   details.append(tableView(['มุมมอง','แกน','PDF มม.','วัดใน DWG มม.'],j.drawing_info.measurement_checks.map(c=>[c.view,c.axis,number(c.expected_mm),number(c.dwg_actual_mm)])));
   if(j.drawing_info.omitted_groups.length)details.append(tableView(['ยังไม่สร้าง','สาเหตุ'],j.drawing_info.omitted_groups.map(g=>[g.name,g.reason])));
  }
  (j.drawing_spec.notes||[]).forEach(n=>details.append(node('p',n)));
  if(j.drawing_spec.assumptions.length){
   details.append(node('h3','สมมติฐานของ Drawing'));
   details.append(tableView(['รหัส / รายการ','ค่า','เหตุผล'],j.drawing_spec.assumptions.map(a=>[a.id+' / '+a.item,a.value,a.reason])));
  }
 }
 aiControls();
 if(drawingBusy&&!drawingPolling){drawingPolling=true;setTimeout(pollDrawing,1500)}
}
async function submitPdfDrawing(isometric=false){
 if(!selected||busy||aiBusy||drawingBusy||chatPolling)return;
 const first=Number(document.getElementById('first').value),last=document.getElementById('last').value?Number(document.getElementById('last').value):0;
 if(!Number.isInteger(first)||first<1||!Number.isInteger(last)||last<0||(last&&(last<first||last-first+1>8))){aiMessage('เลือกช่วงหน้าให้ถูกต้อง ไม่เกิน 8 หน้า',true);return}
 aiBusy=true;aiControls();analysisTab();aiMessage(isometric?'กำลังตรวจกลุ่มรูปฉายและมิติอ้างอิง…':'กำลังอ่านเส้น PDF โดยตรง…');
 try{
  const j=await request(`/api/${isometric?'measured-drawing':'vector-drawing'}?first=${first}&last=${last}&name=${encodeURIComponent(selected.name)}`,{method:'POST',headers:{'Content-Type':'application/pdf'},body:selected});
  analysisId=j.id;history.replaceState(null,'','#analysis='+analysisId);analysisFile=selected;currentAnalysis=null;
  document.getElementById('chatSection').hidden=true;document.getElementById('cadPanel').hidden=true;
  document.getElementById('analysisResult').replaceChildren(node('p',isometric?'Codex กำลังตรวจ TOP / ELEVATION / INSIDE และสร้างรูปฉาย 1:1…':'กำลังดึงเส้น PDF และสร้าง DWG ในเครื่อง…'));
  pollAnalysis();
 }catch(e){aiBusy=false;aiMessage('สร้าง DWG ไม่สำเร็จ: '+e.message,true);runtimeError('สร้าง DWG ไม่สำเร็จ: '+e.message);aiControls()}
};
document.getElementById('vectorDrawing').onclick=()=>submitPdfDrawing(false);
document.getElementById('isometricDrawing').onclick=()=>submitPdfDrawing(true);
document.getElementById('createDrawing').onclick=async()=>{
 if(!currentAnalysis||drawingBusy)return;
 drawingBusy=true;aiControls();document.getElementById('cadStatus').textContent='กำลังส่งภาพแบบให้ Codex สร้างมุมมอง…';
 try{
  await request('/api/create-drawing',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id:analysisId})});
  drawingPolling=true;pollDrawing();
 }catch(e){drawingBusy=false;drawingPolling=false;document.getElementById('cadStatus').textContent=e.message;aiControls()}
};
async function pollDrawing(){
 try{
  const j=await request('/api/analysis/'+analysisId);currentAnalysis=j;
  document.getElementById('cadStatus').textContent=j.drawing_stage||'กำลังสร้างแบบ…';
  if(j.drawing_status==='running'){setTimeout(pollDrawing,1500);return}
  drawingPolling=false;drawingBusy=false;renderDrawing(j);
 }catch(e){drawingPolling=false;drawingBusy=false;document.getElementById('cadStatus').textContent=e.message;aiControls()}
}
fetch('/api/cad-status').then(r=>r.json()).then(j=>{drawingReady=j.ready;document.getElementById('cadStatus').textContent=j.message;document.getElementById('cadSetupStatus').textContent=j.message||'ตรวจเครื่องมือ DWG ไม่สำเร็จ';aiControls()}).catch(()=>{document.getElementById('cadStatus').textContent='ตรวจเครื่องมือ CAD ไม่สำเร็จ';document.getElementById('cadSetupStatus').textContent='ตรวจเครื่องมือ DWG ไม่สำเร็จ กรุณาปิดโปรแกรมรุ่นเก่าแล้วเปิดใหม่';aiControls()});

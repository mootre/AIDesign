'use strict';
let aiReady=false,aiBusy=false,analysisId=null,currentAnalysis=null,analysisFile=null,chatPolling=false;
const aiNode=id=>document.getElementById(id);
function aiMessage(text,error=false){aiNode('aiStatus').textContent=text;aiNode('aiStatus').classList.toggle('error',error)}
function aiControls(){
 aiNode('analyze').disabled=!aiReady||!selected||busy||aiBusy||chatPolling;
 aiNode('aiCancel').hidden=!aiBusy;
 aiNode('read').disabled=aiBusy||!selected||!ready||busy;
 aiNode('file').disabled=aiBusy||busy||chatPolling;
 aiNode('ask').disabled=aiBusy||chatPolling||!currentAnalysis;
 aiNode('forget').disabled=aiBusy||chatPolling||!analysisId;
 ['saveReport','saveJSON','saveCSV'].forEach(id=>aiNode(id).disabled=!currentAnalysis);
}
function analysisTab(){tab('analysis');aiNode('previewView').hidden=true;aiNode('previewTab').classList.remove('active');aiNode('previewTab').setAttribute('aria-selected','false');aiNode('analysisView').hidden=false;aiNode('analysisTab').classList.add('active');aiNode('analysisTab').setAttribute('aria-selected','true')}
aiNode('analysisTab').onclick=analysisTab;
// Keep controls synchronized with the existing OCR controls without timer polling.
const oldControls=controls;controls=function(){oldControls();aiControls()};
const oldSelectFile=selectFile;selectFile=function(file){
 if(aiBusy||chatPolling)return;
 oldSelectFile(file);
 aiControls();
 if(currentAnalysis&&file!==analysisFile)aiMessage('เลือกเอกสารใหม่แล้ว · ผลเดิมยังอยู่จนกดวิเคราะห์ใหม่');
};
function node(tag,text,cls){const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(cls)n.className=cls;return n}
function number(n){return n===null||n===undefined?'ไม่ระบุ':new Intl.NumberFormat('th-TH',{maximumFractionDigits:4}).format(n)}
function source(r){return (r.source_pages?.length?'หน้า '+r.source_pages.join(', '):'ไม่มีหน้าอ้างอิง')+(r.assumption_ids?.length?' · สมมติฐาน '+r.assumption_ids.join(', '):'')}
function tableView(headers,rows){
 const box=node('div',undefined,'table-scroll'),table=node('table'),head=node('thead'),tr=node('tr');
 headers.forEach(h=>tr.append(node('th',h)));head.append(tr);table.append(head);
 const body=node('tbody');
 rows.forEach(row=>{const tr=node('tr');row.forEach(value=>{const td=node('td');if(value instanceof Node)td.append(value);else td.textContent=value??'';tr.append(td)});body.append(tr)});
 if(!rows.length){const tr=node('tr'),td=node('td','ไม่มีรายการที่ระบุได้');td.colSpan=headers.length;tr.append(td);body.append(tr)}
 table.append(body);box.append(table);return box;
}
function section(root,title,content){root.append(node('h2',title));root.append(content)}
function editable(value,index,key){
 const input=node('input');input.type='number';input.min='0';input.step='any';input.value=value??'';
 if(key==='quantity'){input.step='1';input.max='100000'}
 if(key.endsWith('edges')){input.step='1';input.max='2'}
 input.dataset.part=index;input.dataset.field=key;input.setAttribute('aria-label',key+' รายการ '+(index+1));return input;
}
function renderAnalysis(j){
 currentAnalysis=j;const r=j.report,c=j.calculation,root=aiNode('analysisResult');c.total_parts??=c.parts.length;c.calculated_parts??=c.parts.filter(p=>p.area_m2!==null).length;root.replaceChildren();aiNode('pageCount').textContent='วิเคราะห์หน้า '+j.page_numbers.join(', ');if(!selected){aiNode('fileName').textContent=j.filename||'ผลวิเคราะห์ที่กู้คืน';aiNode('fileInfo').textContent='เลือก PDF อีกครั้งหากต้องการดูต้นฉบับ';analysisFile={name:j.filename||'document.pdf'}}
 root.append(node('span','ประมาณการเบื้องต้น · '+j.page_numbers.map(p=>'หน้า '+p).join(', '),'result-badge'));
 root.append(node('p',r.summary,'summary'));
 root.append(node('p','สถานะแบบ: '+r.document_status));
 if(j.drawing_info?.mode==='measured_views'){root.append(node('p','รูปฉายตามกลุ่มใน PDF · 1 หน่วย = 1 มม. · ตรวจขนาดอ้างอิงจาก DWG แล้ว ส่วนที่อนุมานยังต้องตรวจทานก่อนผลิต','notice'));aiNode('chatSection').hidden=true;aiControls();return;}
 if(j.drawing_info?.mode==='isometric_basic'){
  root.append(node('p','ไฟล์มีเฉพาะเส้นโครงสร้าง Isometric ไม่มีข้อความ เส้นบอกขนาด หรือรายละเอียดเล็ก ๆ · ไม่ใช่ขนาดผลิต 1:1','notice'));
  aiNode('chatSection').hidden=true;aiControls();return;
 }
 if(j.drawing_info?.mode==='pdf_vector'){
  root.append(node('p','นำเข้าเส้น '+j.drawing_info.source_stats.paths+' รายการ และข้อความ '+j.drawing_info.source_stats.text_objects+' รายการ · ยังไม่ได้วิเคราะห์วัสดุด้วย AI','notice'));
  root.append(node('p','ไฟล์นี้ใช้พิกัดหน้ากระดาษ PDF ต้องตรวจ/ปรับสเกลก่อนวัดเป็นขนาดชิ้นงานจริง และอาจมีเส้นซ่อนใน PDF รวมมาด้วย','notice'));
  aiNode('chatSection').hidden=true;aiControls();return;
 }
 root.append(node('p',c.notice,'notice'));
 section(root,'สมมติฐานที่ต้องยืนยัน',tableView(['รหัส / รายการ','ค่าที่ใช้','เหตุผล / ที่มา'],r.assumptions.map(a=>[a.id+' · '+a.item,a.value,a.reason+' · '+source(a)])));
 section(root,'รายการชิ้นส่วน / Cut List',node('p','แก้ตัวเลขเพื่อทดลองสมมติฐาน แล้วกดคำนวณใหม่ การแก้จะถูกบันทึกเป็นสมมติฐานผู้ใช้ และล้างคำถามเก่าที่อิงตัวเลขเดิม'));
 root.append(tableView(['โมดูล / ชิ้นส่วน','วัสดุ','หนา มม.','กว้าง มม.','ยาว มม.','จำนวน','ขอบยาว','ขอบสั้น','พื้นที่ ตร.ม.','ขอบ ม.','หลักฐาน'],c.parts.map((p,i)=>[
 p.module+' / '+p.name,p.material,editable(p.thickness_mm,i,'thickness_mm'),editable(p.width_mm,i,'width_mm'),editable(p.length_mm,i,'length_mm'),editable(p.quantity,i,'quantity'),editable(p.long_edges,i,'long_edges'),editable(p.short_edges,i,'short_edges'),number(p.area_m2),number(p.edge_m),p.evidence+' · '+source(p)])));
 const recalc=node('button','คำนวณใหม่จากตัวเลขที่แก้ไข','secondary');recalc.id='recalc';recalc.onclick=recalculate;root.append(recalc);
 root.append(node('p','คำนวณพื้นที่ได้ '+c.calculated_parts+' จาก '+c.total_parts+' รายการ · ยอดสรุปรวมเฉพาะชิ้นที่มีข้อมูลครบ ไม่ใช่ยอดวัสดุทั้งงาน','notice'));
 section(root,'สรุปไม้แผ่น เฉพาะรายการที่คำนวณได้',tableView(['วัสดุ / หนา','พื้นที่สุทธิ ตร.ม.','รวมเผื่อ '+c.waste_percent+'%','แผ่นตามพื้นที่*','ปิดขอบ ม.'],c.materials.map(m=>[
 m.material+' / '+m.thickness_mm+' มม.',number(m.net_area_m2),number(m.gross_area_m2),m.has_oversize?'มีชิ้นเกินแผ่น — ห้ามใช้ยอดสั่งซื้อ':number(m.area_based_sheets),number(m.edge_m)+(m.edge_unknown?' + มีส่วนไม่ทราบ':'')])));
 section(root,'อุปกรณ์และวัสดุประกอบ',tableView(['รายการ','จำนวน','หน่วย','ที่มา / สมมติฐาน'],r.hardware.map(h=>[h.name,number(h.quantity),h.unit,h.evidence+' · '+source(h)])));
 section(root,'รายการตรวจสอบก่อนผลิต',tableView(['ระดับ','ประเด็น','ที่มา'],r.checks.map(x=>[x.severity,x.issue,source(x)])));
 if(c.warnings.length)section(root,'ข้อมูลที่ยังคำนวณไม่ได้ / ข้อสังเกต',node('p',c.warnings.join('\n'),'notice'));
 root.append(node('p','จำนวนแผ่นไม่ได้แยกสีผิว/แนวลายอัตโนมัติ หากเป็นคนละผิวต้องแยกวัสดุ ตรวจ Nesting ร่อง/บังใบ ระยะติดตั้ง และขนาดจริงก่อนผลิต','notice'));
 aiNode('chatSection').hidden=false;renderChat(j);aiControls();
}
async function recalculate(){
 if(!currentAnalysis||aiBusy||chatPolling)return;
 try{
 const report=structuredClone(currentAnalysis.report);let changed=false;
 aiNode('analysisResult').querySelectorAll('input[data-part]').forEach(input=>{
  const index=Number(input.dataset.part),key=input.dataset.field,val=input.value===''?null:Number(input.value);
  if(val!==report.parts[index][key]){
   changed=true;const id='USER-'+(index+1)+'-'+key;
   report.assumptions=report.assumptions.filter(a=>a.id!==id);
   report.assumptions.push({id,item:report.parts[index].name+' / '+key,value:String(val??'ไม่ระบุ'),reason:'ผู้ใช้แก้ไขเพื่อทดสอบสมมติฐาน ยังต้องยืนยันกับแบบ',source_pages:[]});
   if(!report.parts[index].assumption_ids.includes(id))report.parts[index].assumption_ids.push(id);
   report.parts[index][key]=val;
  }
 });
 const j=await request('/api/recalculate',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id:analysisId,report,waste:Number(aiNode('waste').value)})});
 renderAnalysis(j);aiMessage('คำนวณใหม่แล้ว · '+(changed?'บันทึกสมมติฐานผู้ใช้แล้ว':'ใช้ตัวเลขเดิม'));
 }catch(e){aiMessage(e.message,true)}
}
aiNode('analyze').onclick=async()=>{
 if(aiBusy||!selected||busy)return;
 const first=Number(aiNode('first').value),last=aiNode('last').value?Number(aiNode('last').value):0,waste=Number(aiNode('waste').value);
 if(!Number.isInteger(first)||first<1||!Number.isInteger(last)||last<0||(last&&(last<first||last-first+1>8))||!Number.isFinite(waste)||waste<0||waste>50){aiMessage('เลือกช่วงไม่เกิน 8 หน้า และเผื่อสูญเสีย 0–50%',true);return}
 aiBusy=true;aiControls();analysisTab();aiMessage('กำลังเตรียมส่งหน้าแบบให้ Codex…');
 try{
  const j=await request(`/api/analyze?first=${first}&last=${last}&waste=${waste}&name=${encodeURIComponent(selected.name)}`,{method:'POST',headers:{'Content-Type':'application/pdf'},body:selected});
  analysisId=j.id;history.replaceState(null,'','#analysis='+analysisId);analysisFile=selected;currentAnalysis=null;aiNode('chatSection').hidden=true;
  aiNode('analysisResult').replaceChildren(node('p','กำลังวิเคราะห์ภาพแบบและตรวจสมมติฐาน อาจใช้เวลาหลายนาที…'));aiControls();pollAnalysis();
 }catch(e){aiBusy=false;aiMessage(e.message,true);aiControls()}
};
async function pollAnalysis(){
 try{
  const j=await request('/api/analysis/'+analysisId);aiMessage(j.stage);
  if(j.status==='done'){aiBusy=false;renderAnalysis(j);if(j.chat_busy&&!chatPolling){chatPolling=true;pollChat()}}
  else if(j.status==='error'||j.status==='cancelled'){aiBusy=false;aiMessage(j.error||'ยกเลิกแล้ว',true);aiNode('analysisResult').replaceChildren(node('p',j.error||'ยกเลิกแล้ว','notice'))}
  else setTimeout(pollAnalysis,1500);
  aiControls();
 }catch(e){aiBusy=false;aiMessage('ติดต่อโปรแกรมไม่ได้: '+e.message,true);aiControls()}
}
aiNode('aiCancel').onclick=async()=>{try{await request('/api/ai-cancel',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id:analysisId})});aiMessage('กำลังยกเลิก…')}catch(e){aiMessage(e.message,true)}};
function renderChat(j){
 const log=aiNode('chatLog');log.replaceChildren();
 j.chat.forEach(m=>{const card=node('div',undefined,'chat-card '+m.role);card.append(node('strong',m.role==='user'?'คุณ':'Codex'));card.append(node('p',m.text??m.answer));if(m.role==='assistant'){card.append(node('small',source(m)));if(m.assumptions?.length)card.append(node('p','สมมติฐาน: '+m.assumptions.join(' · ')))}log.append(card)});
}
aiNode('ask').onclick=async()=>{
 const question=aiNode('question').value.trim();if(!question||!currentAnalysis||chatPolling)return;
 chatPolling=true;aiControls();aiNode('chatStatus').textContent='Codex กำลังพิจารณาคำถามและหน้าแบบ…';
 try{await request('/api/chat',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id:analysisId,question})});pollChat()}catch(e){chatPolling=false;aiNode('chatStatus').textContent=e.message;aiControls()}
};
async function pollChat(){
 try{const j=await request('/api/analysis/'+analysisId);if(j.chat_busy){setTimeout(pollChat,1500);return}chatPolling=false;currentAnalysis=j;renderChat(j);aiNode('chatStatus').textContent=j.chat_error||'ตอบแล้ว · คำตอบนี้ไม่ได้เปลี่ยนตัวเลขใน Cut List';if(!j.chat_error)aiNode('question').value='';aiControls()}
 catch(e){chatPolling=false;aiNode('chatStatus').textContent=e.message;aiControls()}
}
function downloadAI(text,extension,type){
 const url=URL.createObjectURL(new Blob(['\ufeff'+text],{type})),a=document.createElement('a');a.href=url;a.download=(analysisFile?.name??'estimate').replace(/\.pdf$/i,'')+'-analysis.'+extension;a.click();setTimeout(()=>URL.revokeObjectURL(url),5000);
}
function reportMarkdown(j){
 const r=j.report,c=j.calculation;let out='# รายงานประมาณการเฟอร์นิเจอร์\n\n'+r.summary+'\n\nสถานะแบบ: '+r.document_status+'\n\nวิเคราะห์หน้า '+j.page_numbers.join(', ')+'\n\n'+c.notice+'\n\nคำนวณพื้นที่ได้ '+c.calculated_parts+' จาก '+c.total_parts+' รายการ ยอดสรุปรวมเฉพาะรายการที่มีข้อมูลครบ\n';
 function section(title,headers,rows){out+='\n## '+title+'\n\n| '+headers.join(' | ')+' |\n| '+headers.map(()=>'---').join(' | ')+' |\n';rows.forEach(row=>out+='| '+row.map(x=>String(x??'ไม่ระบุ').replaceAll('|','／').replaceAll('\n',' ')).join(' | ')+' |\n')}
 section('สมมติฐาน',['รหัส','รายการ','ค่า','เหตุผล'],r.assumptions.map(a=>[a.id,a.item,a.value,a.reason]));
 section('Cut List',['โมดูล','ชิ้นส่วน','วัสดุ','หนา','กว้าง','ยาว','จำนวน','ขอบยาว','ขอบสั้น','ตร.ม.','ปิดขอบ ม.','ที่มา'],c.parts.map(p=>[p.module,p.name,p.material,p.thickness_mm,p.width_mm,p.length_mm,p.quantity,p.long_edges,p.short_edges,p.area_m2,p.edge_m,p.evidence+' '+source(p)]));
 section('สรุปไม้แผ่น — เผื่อ '+c.waste_percent+'%',['วัสดุ','หนา','พื้นที่สุทธิ','รวมเผื่อ','แผ่นตามพื้นที่','ปิดขอบ ม.'],c.materials.map(m=>[m.material,m.thickness_mm,m.net_area_m2,m.gross_area_m2,m.has_oversize?'มีชิ้นเกินแผ่น':m.area_based_sheets,m.edge_m+(m.edge_unknown?' + ไม่ครบ':'')]));
 section('อุปกรณ์',['รายการ','จำนวน','หน่วย','หลักฐาน'],r.hardware.map(h=>[h.name,h.quantity,h.unit,h.evidence+' '+source(h)]));
 section('ตรวจสอบก่อนผลิต',['ระดับ','ประเด็น','หน้า'],r.checks.map(x=>[x.severity,x.issue,source(x)]));
 out+='\n'+c.warnings.join('\n')+'\n\n## คำถามต่อเนื่อง\n';j.chat.forEach(m=>out+='\n'+(m.role==='user'?'คุณ: ':'Codex: ')+(m.text??m.answer)+(m.role==='assistant'?'\n'+source(m)+'\nสมมติฐาน: '+(m.assumptions||[]).join(' / '):'')+'\n');return out;
}
aiNode('saveReport').onclick=()=>downloadAI(reportMarkdown(currentAnalysis),'md','text/markdown;charset=utf-8');
aiNode('saveJSON').onclick=()=>downloadAI(JSON.stringify(currentAnalysis,null,2),'json','application/json');
aiNode('saveCSV').onclick=()=>{
 const fields=['module','name','material','thickness_mm','width_mm','length_mm','quantity','long_edges','short_edges','area_m2','edge_m','evidence','source_pages','assumption_ids'];
 const cell=x=>{let s=Array.isArray(x)?x.join('; '):String(x??'');if(/^[\s]*[=+@-]/.test(s))s="'"+s;return '"'+s.replaceAll('"','""')+'"'};
 downloadAI([fields,...currentAnalysis.calculation.parts.map(p=>fields.map(f=>p[f]))].map(row=>row.map(cell).join(',')).join('\r\n'),'csv','text/csv;charset=utf-8');
};
aiNode('forget').onclick=async()=>{try{await request('/api/forget',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id:analysisId})});analysisId=null;history.replaceState(null,'',location.pathname);currentAnalysis=null;analysisFile=null;aiNode('analysisResult').replaceChildren(node('p','ล้างงานวิเคราะห์และภาพชั่วคราวแล้ว'));aiNode('chatSection').hidden=true;aiMessage('พร้อมวิเคราะห์เอกสารใหม่');aiControls()}catch(e){aiMessage(e.message,true)}};
fetch('/api/ai-status').then(r=>r.json()).then(async j=>{aiReady=j.logged_in;aiMessage(j.message,!aiReady);aiControls();const match=location.hash.match(/^#analysis=([A-Za-z0-9_-]{10,80})$/);if(match){analysisId=match[1];analysisTab();aiBusy=true;aiControls();await pollAnalysis()}}).catch(e=>aiMessage('ตรวจ Codex ไม่สำเร็จ: '+e.message,true));

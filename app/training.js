(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const state = {token:'', documents:[], doc:null, annotation:null, evidence:[], candidates:[],
    imageURL:null, imageSequence:0, imageReady:false, drag:null, predicting:false};
  const labels = {draft:'ร่าง',submitted:'รอตรวจรับ',approved:'อนุมัติแล้ว',rejected:'ไม่รับรายการ',
    changes_requested:'ส่งกลับแก้ไข',direct:'อ่านตรงจากแบบ',calculated:'คำนวณ',inferred:'อนุมาน',insufficient:'ข้อมูลไม่พอ',
    train:'ฝึก',dev:'ตรวจระหว่างพัฒนา',test:'ทดสอบที่กันไว้',unassigned:'ยังไม่กำหนด'};
  const text = (id, value) => { if ($(id)) $(id).textContent = value ?? ''; };
  function node(tag, content, cls) { const el = document.createElement(tag); if(content !== undefined) el.textContent=content; if(cls)el.className=cls; return el; }
  function message(value, id='trGlobalStatus') { text(id,value); }
  async function api(path, options={}) {
    const response = await fetch(path, {...options, cache:'no-store', redirect:'error',
      headers:{'X-App-Token':state.token,...options.headers}});
    if (!response.ok) {
      let error; try { error=await response.json(); } catch {}
      throw new Error(error?.error || 'บริการไม่พร้อม กรุณาตรวจหน้าต่างโปรแกรม');
    }
    if (options.binary) return response.blob();
    return response.json();
  }
  function post(path, body) { return api(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}); }
  function documentPath(suffix='') { if(!state.doc)throw Error('กรุณาเลือกแบบก่อน'); return '/api/training/documents/'+state.doc.id+suffix; }
  function setBusy(form, busy) { form.querySelectorAll('button[type="submit"]').forEach(button => {button.disabled=busy;}); }
  function summary(stats={}) {
    text('statDocs',stats.documents ?? stats.drawings ?? stats.document_count ?? state.documents.length);
    text('statSamples',stats.annotations ?? stats.annotation_count ?? 0);
    text('statApproved',stats.approved ?? stats.approved_count ?? 0);
    text('statExportable',stats.training_ready ?? stats.exportable ?? stats.exportable_count ?? 0);
  }
  function renderDocuments() {
    const root=$('trDocuments'); root.replaceChildren();
    const query=$('trSearch').value.trim().toLowerCase();
    for (const doc of state.documents) {
      if (query && ![doc.code,doc.title,doc.group_id,doc.revision].join(' ').toLowerCase().includes(query)) continue;
      const button=node('button',undefined,'tr-doc-card'+(state.doc?.id===doc.id?' selected':''));button.type='button';
      button.append(node('strong',doc.code+' / '+doc.revision),node('span',doc.title || doc.category),
        node('small',doc.page_count+' หน้า · '+(doc.annotation_count || 0)+' ตัวอย่าง · '+labels[doc.split || 'unassigned']));
      button.onclick=()=>openDocument(doc.id).catch(error=>message(error.message));root.append(button);
    }
    if (!root.children.length) root.append(node('p',query?'ไม่พบแบบที่ค้นหา':'ยังไม่มีแบบ ลงทะเบียน PDF เพื่อเริ่มตรวจได้โดยไม่ต้องใช้ AI'));
  }
  async function refresh() {
    const result=await api('/api/training/documents'); state.documents=result.documents;summary(result.stats);renderDocuments();
  }
  function updateDocumentHeader() {
    const doc=state.doc;
    text('trDocTitle',doc.code+' / '+doc.revision+(doc.title?' — '+doc.title:''));
    text('trDocMeta','กลุ่ม '+doc.group_id+' · '+doc.page_count+' หน้า · SHA-256 '+doc.sha256.slice(0,12)+' · '+labels[doc.split || 'unassigned']);
    text('trPageTotal',doc.page_count);$('trPage').max=String(doc.page_count);
    $('trSplit').value=doc.split || 'unassigned';$('trSplit').disabled=Boolean(doc.split && doc.split!=='unassigned');
    $('trAssignSplit').disabled=Boolean(doc.split && doc.split!=='unassigned');
    $('trConsentAI').checked=doc.allow_ai;$('trConsentTraining').checked=doc.allow_training;$('trConsentPrivacy').checked=doc.privacy_confirmed;
    $('trPredict').disabled=state.predicting || !doc.allow_ai || !doc.privacy_confirmed;
    renderCandidates(); renderAnnotations();
  }
  async function openDocument(id, selectedAnnotation=null) {
    state.doc=await api('/api/training/documents/'+id);state.annotation=null;state.evidence=[];
    $('trWorkspace').hidden=false;$('trPage').value='1';updateDocumentHeader();newAnnotation();renderDocuments();
    if(selectedAnnotation) {
      const annotation=state.doc.annotations.find(item=>item.id===selectedAnnotation);
      if(annotation)loadAnnotation(annotation);
    }
    await showPage();
  }
  async function reloadCurrent(annotationID=state.annotation?.id) {
    const page=Number($('trPage').value);
    state.doc=await api(documentPath());updateDocumentHeader();await refresh();
    const selected=state.doc.annotations.find(item=>item.id===annotationID);
    if(selected)loadAnnotation(selected);else newAnnotation();
    $('trPage').value=String(page);paint();
  }
  $('trSearch').addEventListener('input',renderDocuments);
  $('registerForm').addEventListener('submit',async event=>{
    event.preventDefault();const form=event.currentTarget;if(!form.reportValidity())return;
    const file=$('trFile').files[0];if(!file || !file.name.toLowerCase().endsWith('.pdf') || file.size>30*1024*1024){message('เลือก PDF ขนาดไม่เกิน 30 MB','trRegisterStatus');return;}
    const metadata={code:$('trCode').value.trim(),revision:$('trRevision').value.trim(),group_id:$('trGroup').value.trim(),
      title:$('trTitle').value.trim() || $('trCode').value.trim(),category:$('trCategory').value,allow_storage:$('trAllowStorage').checked,
      allow_training:$('trAllowTraining').checked,allow_ai:$('trAllowAI').checked,privacy_confirmed:$('trPrivacy').checked};
    setBusy(form,true);message('กำลังตรวจ PDF และลงทะเบียนสำเนาต้นฉบับ…','trRegisterStatus');
    try {
      const doc=await api('/api/training/documents',{method:'POST',headers:{'Content-Type':'application/pdf',
        'X-Training-Metadata':encodeURIComponent(JSON.stringify(metadata))},body:file});
      message('เก็บสำเนาต้นฉบับแล้ว · ยังไม่มีการส่งให้ AI','trRegisterStatus');
      $('trFile').value='';await refresh();await openDocument(doc.id);
      $('trWorkspace').scrollIntoView({behavior:'smooth',block:'start'});
    }catch(error){message(error.message,'trRegisterStatus');}finally{setBusy(form,false);}
  });
  async function showPage() {
    if(!state.doc)return;
    let page=Number($('trPage').value);
    if(!Number.isInteger(page) || page<1 || page>state.doc.page_count){$('trPage').value='1';page=1;}
    const sequence=++state.imageSequence;state.imageReady=false;
    $('trPrev').disabled=page<=1;$('trNext').disabled=page>=state.doc.page_count;
    text('trPageTotal',state.doc.page_count+' · กำลังโหลดภาพ');
    try {
      const image=await api(documentPath('/page/'+page),{binary:true});
      if(sequence!==state.imageSequence)return;
      if(state.imageURL)URL.revokeObjectURL(state.imageURL);
      state.imageURL=URL.createObjectURL(image);$('trImage').src=state.imageURL;
      text('trPageTotal',state.doc.page_count);
    }catch(error){message(error.message);text('trPageTotal','โหลดภาพไม่สำเร็จ');}
  }
  $('trImage').addEventListener('load',()=>{state.imageReady=true;resizeCanvas();});
  function resizeCanvas(){
    const image=$('trImage'),canvas=$('trCanvas');canvas.width=image.clientWidth;canvas.height=image.clientHeight;paint();
  }
  new ResizeObserver(resizeCanvas).observe($('trImage'));
  $('trZoom').addEventListener('change',()=>{$('trImageWrap').style.width=$('trZoom').value+'%';resizeCanvas();});
  $('trPage').addEventListener('change',showPage);$('trPrev').onclick=()=>{$('trPage').value=String(Number($('trPage').value)-1);showPage();};
  $('trNext').onclick=()=>{$('trPage').value=String(Number($('trPage').value)+1);showPage();};
  function point(event){const r=$('trCanvas').getBoundingClientRect();return [Math.max(0,Math.min(1,(event.clientX-r.left)/r.width)),Math.max(0,Math.min(1,(event.clientY-r.top)/r.height))];}
  function paint(){
    const canvas=$('trCanvas'),ctx=canvas.getContext('2d');ctx.clearRect(0,0,canvas.width,canvas.height);
    const boxes=state.evidence.filter(region=>region.page===Number($('trPage').value)).map(region=>region.bbox);
    if(state.drag){const [x,y]=state.drag.start,[a,b]=state.drag.end;boxes.push([Math.min(x,a),Math.min(y,b),Math.abs(a-x),Math.abs(b-y)]);}
    boxes.forEach((box,index)=>{const [x,y,w,h]=box;ctx.strokeStyle='#087f72';ctx.fillStyle='rgba(8,127,114,.12)';ctx.lineWidth=2;
      ctx.fillRect(x*canvas.width,y*canvas.height,w*canvas.width,h*canvas.height);ctx.strokeRect(x*canvas.width,y*canvas.height,w*canvas.width,h*canvas.height);
      ctx.fillStyle='#064e46';ctx.font='bold 15px sans-serif';ctx.fillText(String(index+1),x*canvas.width+4,y*canvas.height+18);});
  }
  $('trCanvas').addEventListener('pointerdown',event=>{if(!state.imageReady)return;event.preventDefault();state.drag={start:point(event),end:point(event)};$('trCanvas').setPointerCapture(event.pointerId);paint();});
  $('trCanvas').addEventListener('pointermove',event=>{if(state.drag){state.drag.end=point(event);paint();}});
  $('trCanvas').addEventListener('pointerup',event=>{
    if(!state.drag)return;const [x,y]=state.drag.start,[a,b]=point(event);state.drag=null;
    const bbox=[Math.min(x,a),Math.min(y,b),Math.abs(a-x),Math.abs(b-y)].map(value=>Math.round(value*100000)/100000);
    bbox[2]=Math.min(bbox[2],1-bbox[0]);bbox[3]=Math.min(bbox[3],1-bbox[1]);
    if(bbox[2]>.002 && bbox[3]>.002 && state.evidence.length<20){state.evidence.push({page:Number($('trPage').value),bbox,view:$('trViewName').value.trim()});}
    renderEvidence();paint();
  });
  $('trCanvas').addEventListener('pointercancel',()=>{state.drag=null;paint();});
  function renderEvidence(){
    const root=$('trEvidenceList');root.replaceChildren();
    state.evidence.forEach((region,index)=>{const item=node('div',undefined,'evidence-item');item.append(node('span',`${index+1}. หน้า ${region.page} · ${region.view || 'ยังไม่ระบุมุมมอง'}`));
      const view=node('button','ดูกรอบ');view.type='button';view.onclick=()=>{$('trPage').value=String(region.page);showPage();};
      const remove=node('button','เอากรอบออก');remove.type='button';remove.onclick=()=>{state.evidence.splice(index,1);renderEvidence();paint();};
      item.append(view,remove);root.append(item);});
    if(!state.evidence.length)root.append(node('p','ลากกรอบบนภาพเพื่ออ้างหลักฐาน สามารถเพิ่มได้หลายหน้า'));
  }
  $('trClearEvidence').onclick=()=>{state.evidence=[];renderEvidence();paint();};
  function newAnnotation(){
    state.annotation=null;state.evidence=[];
    for(const id of ['trQuestion','trPart','trAIValue','trValue','trUnit','trFormula','trExplanation','trReviewComment'])$(id).value='';
    $('trTask').value='question';$('trEvidenceType').value='direct';$('trPrediction').value='';$('trReviewPrivacy').checked=false;
    text('trAnnotationStatus','ตัวอย่างใหม่ · บันทึกเป็นร่างก่อนส่งตรวจ');$('trHistory').replaceChildren();renderEvidence();paint();
  }
  $('trNew').onclick=newAnnotation;
  function loadAnnotation(annotation){
    state.annotation=annotation;state.evidence=structuredClone(annotation.evidence || []);$('trPrediction').value='';
    const fields={trTask:'task_type',trQuestion:'question',trPart:'part_ref',trAIValue:'ai_value',trValue:'corrected_value',
      trUnit:'unit',trEvidenceType:'evidence_type',trFormula:'formula',trExplanation:'explanation',trAuthor:'author_ref'};
    for(const [id,key] of Object.entries(fields))$(id).value=annotation[key] ?? '';
    $('trReviewPrivacy').checked=false;renderEvidence();paint();
    text('trAnnotationStatus',labels[annotation.status]+' · รุ่น '+annotation.version+' · การแก้ไขต้องตรวจรับใหม่');
    api('/api/training/annotations/'+annotation.id+'/history').then(result=>{
      if(state.annotation?.id!==annotation.id)return;
      const root=$('trHistory');root.replaceChildren();
      for(const event of result.history){
        const entry=node('details'),snapshot=event.snapshot || {};
        entry.append(node('summary',`รุ่น ${event.version ?? snapshot.version ?? '—'} · ${event.action || event.event || 'ปรับปรุง'} · ${event.created_at || ''}`));
        entry.append(node('p','คำตอบ: '+(snapshot.corrected_value || 'ข้อมูลไม่พอ')+' '+(snapshot.unit || '')));
        entry.append(node('p','หลักฐาน: '+(labels[snapshot.evidence_type] || '')+' · '+(snapshot.evidence?.length || 0)+' กรอบ'));
        if(event.comment)entry.append(node('p','ผลตรวจ: '+event.comment));
        if(snapshot.explanation)entry.append(node('p',snapshot.explanation));
        root.append(entry);
      }
    }).catch(error=>message(error.message));
  }
  function renderAnnotations(){
    const root=$('trAnnotations');root.replaceChildren();
    for(const annotation of state.doc.annotations || []){
      const button=node('button',undefined,'tr-sample-card');button.type='button';
      button.append(node('strong',annotation.question),node('span',(annotation.corrected_value || 'ข้อมูลไม่พอ')+' '+(annotation.unit || '')),
        node('small',labels[annotation.status]+' · '+labels[annotation.evidence_type]+' · รุ่น '+annotation.version));
      button.onclick=()=>{loadAnnotation(annotation);const page=annotation.evidence?.[0]?.page;if(page){$('trPage').value=String(page);showPage();}};root.append(button);
    }
    if(!root.children.length)root.append(node('p','ยังไม่มีตัวอย่าง เริ่มจากคำถามหนึ่งข้อและชี้หลักฐานบนแบบ'));
  }
  $('annotationForm').addEventListener('submit',async event=>{
    event.preventDefault();const form=event.currentTarget;if(!state.doc || !form.reportValidity())return;
    const selected=state.candidates.find(candidate=>candidate.key===$('trPrediction').value);
    const payload={task_type:$('trTask').value,question:$('trQuestion').value.trim(),part_ref:$('trPart').value.trim(),
      ai_prediction_id:selected?.prediction_id || state.annotation?.ai_prediction_id || null,
      ai_value:$('trAIValue').value,corrected_value:$('trValue').value.trim(),unit:$('trUnit').value.trim(),
      evidence_type:$('trEvidenceType').value,formula:$('trFormula').value.trim(),explanation:$('trExplanation').value.trim(),
      evidence:structuredClone(state.evidence),author_ref:$('trAuthor').value.trim()};
    if(payload.evidence_type==='insufficient' && !payload.evidence.length){payload.evidence.push({page:Number($('trPage').value),bbox:[0,0,1,1],view:'บริบททั้งหน้า — ข้อมูลไม่พอ'});}
    if(state.annotation){payload.id=state.annotation.id;payload.expected_version=state.annotation.version;}
    setBusy(form,true);
    try{const saved=await post(documentPath('/annotations'),payload);await reloadCurrent(saved.id);message('บันทึกฉบับร่างแล้ว กรุณาส่งตรวจรับ','trAnnotationStatus');}
    catch(error){message(error.message,'trAnnotationStatus');}finally{setBusy(form,false);}
  });
  $('reviewForm').addEventListener('submit',async event=>{
    event.preventDefault();const form=event.currentTarget;if(!state.annotation){message('เลือกตัวอย่างที่บันทึกแล้วก่อนตรวจรับ','trAnnotationStatus');return;}
    setBusy(form,true);
    try{
      const saved=await post('/api/training/annotations/'+state.annotation.id+'/review',{
        expected_version:state.annotation.version,decision:$('trReviewDecision').value,
        reviewer_ref:$('trReviewer').value.trim(),comment:$('trReviewComment').value.trim(),privacy_confirmed:$('trReviewPrivacy').checked});
      await reloadCurrent(saved.id);message('บันทึกผลตรวจแล้ว · '+labels[saved.status],'trAnnotationStatus');
    }catch(error){message(error.message,'trAnnotationStatus');}finally{setBusy(form,false);}
  });
  $('trAssignSplit').onclick=async()=>{try{await post('/api/training/groups/'+encodeURIComponent(state.doc.group_id)+'/split',{split:$('trSplit').value});await reloadCurrent();message('กำหนดชุดให้ทั้งกลุ่มแล้ว revision และภาพย่อยต้องอยู่กลุ่มเดียวกัน');}catch(error){message(error.message);}};
  $('consentForm').addEventListener('submit',async event=>{
    event.preventDefault();const form=event.currentTarget;setBusy(form,true);
    try{await post(documentPath('/consent'),{allow_ai:$('trConsentAI').checked,allow_training:$('trConsentTraining').checked,
      privacy_confirmed:$('trConsentPrivacy').checked,expected_version:state.doc.consent_version});await reloadCurrent();message('บันทึกสิทธิ์แล้ว · การถอนสิทธิ์มีผลกับงานและการส่งออกครั้งถัดไป');}
    catch(error){message(error.message);}finally{setBusy(form,false);}
  });
  function renderCandidates(){
    state.candidates=[];
    const selector=$('trPrediction');selector.replaceChildren(new Option('คนทำเฉลยเอง / ไม่อ้างผล AI',''));
    for(const prediction of state.doc.predictions || []){
      const report=prediction.report || prediction.report_json || prediction.answer_json || {};
      for(const [partIndex,part] of (report.parts || []).entries()){
        for(const [field,label,task,unit] of [['width_mm','ความกว้าง','dimension','mm'],['length_mm','ความยาว','dimension','mm'],
          ['thickness_mm','ความหนา','dimension','mm'],['quantity','จำนวน','quantity','ชิ้น'],['material','วัสดุ','material','']]){
          if(part[field]===null || part[field]===undefined || part[field]==='')continue;
          const key=prediction.id+'-'+partIndex+'-'+field;
          state.candidates.push({key,prediction_id:prediction.id,value:String(part[field]),label:label+' '+part.name,
            task,unit,part:part.name,pages:part.source_pages});
          selector.append(new Option(label+' '+part.name+' = '+part[field]+' '+unit,key));
        }
      }
    }
    text('trModelVersion',state.doc.predictions?.length? 'ผล AI ที่เก็บไว้ '+state.doc.predictions.length+' ครั้ง · ยังไม่ใช่เฉลยที่อนุมัติ':'ยังไม่มีผล AI · คนทำเฉลยได้ทันที');
  }
  $('trPrediction').onchange=()=>{const candidate=state.candidates.find(item=>item.key===$('trPrediction').value);if(!candidate){$('trAIValue').value='';return;}
    $('trAIValue').value=candidate.value;$('trQuestion').value=candidate.label+'เท่าไร';$('trTask').value=candidate.task;
    $('trPart').value=candidate.part;$('trUnit').value=candidate.unit;
    message('เลือกค่าจากผล AI เดิมแล้ว กรุณาชี้หลักฐานและกรอกคำตอบที่ตรวจด้วยตนเอง','trAnnotationStatus');
    if(candidate.pages?.[0]){$('trPage').value=String(candidate.pages[0]);showPage();}
  };
  $('trPredict').onclick=async()=>{
    if(state.predicting || !state.doc)return;
    const first=Number($('trPredictFirst').value),last=Number($('trPredictLast').value);
    state.predicting=true;$('trPredict').disabled=true;message('กำลังเตรียมส่งหน้าที่อนุญาตให้ Codex…','trPredictStatus');
    try{
      const job=await post(documentPath('/predict'),{first,last});const drawingID=state.doc.id;
      const deadline=Date.now()+620000;
      while(Date.now()<deadline){await new Promise(resolve=>setTimeout(resolve,1500));const result=await api('/api/analysis/'+job.id);
        message(result.stage,'trPredictStatus');
        if(result.status==='error' || result.status==='cancelled')throw Error(result.error || 'การวิเคราะห์หยุดแล้ว');
        if(result.status==='done'){if(state.doc?.id===drawingID)await reloadCurrent();message('เก็บผล AI เดิมแล้ว เลือกค่าจากรายการเพื่อเริ่มตรวจ','trPredictStatus');return;}
      }
      throw Error('วิเคราะห์ใช้เวลานาน กรุณาตรวจหน้าต่างโปรแกรม');
    }catch(error){message(error.message,'trPredictStatus');}finally{state.predicting=false;if(state.doc)$('trPredict').disabled=!state.doc.allow_ai || !state.doc.privacy_confirmed;}
  };
  $('trExportButton').onclick=async()=>{
    if(!window.confirm('ยืนยันว่าตรวจทั้งหน้าแบบและข้อความที่จะส่งออกแล้วว่าไม่มีข้อมูลส่วนบุคคลหรือความลับที่ไม่อนุญาต? ไฟล์จะมีภาพทั้งหน้าที่ใช้อ้างอิง'))return;
    $('trExportButton').disabled=true;message('กำลังสร้าง snapshot ของรายการที่ผ่านเกณฑ์…','trExportStatus');
    try{
      const result=await api('/api/training/export',{method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({privacy_confirmation:true}),binary:true});
      const url=URL.createObjectURL(result),link=node('a');link.href=url;link.download='drawing-training-'+new Date().toISOString().slice(0,10)+'.zip';link.click();setTimeout(()=>URL.revokeObjectURL(url),10000);
      message('ส่งออก ZIP แล้ว: train/dev/test JSONL พร้อมภาพหลักฐานและ manifest · ยังไม่ได้สั่งฝึกโมเดล','trExportStatus');
    }catch(error){message(error.message,'trExportStatus');}finally{$('trExportButton').disabled=false;}
  };
  async function init(){
    try{const response=await fetch('/api/status',{cache:'no-store'});if(!response.ok)throw Error('โปรแกรมยังไม่พร้อม');
      const status=await response.json();if(!status.training)throw Error('กำลังเปิดโปรแกรมคนละรุ่น กรุณาใช้ Training Studio พอร์ต 8768');state.token=status.token;
      await refresh();message('พร้อมตรวจและเก็บข้อมูลในเครื่อง · เลือกแบบหรือเริ่มลงทะเบียน PDF');
    }catch(error){message(error.message);}
  }
  init();
})();

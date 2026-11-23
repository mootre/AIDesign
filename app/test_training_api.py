"""End-to-end local HTTP tests using a generated drawing, never customer files or AI calls."""
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen
import zipfile

import server
import training_api
from training_store import Store


def sample_pdf():
    content = b'0.2 0.4 0.3 RG 2 w 70 70 250 150 re S BT /F1 17 Tf 80 240 Td (CABINET SAMPLE - SYNTHETIC) Tj 0 -25 Td (Width 900 mm - Rev B) Tj ET'
    objects = [b'<< /Type /Catalog /Pages 2 0 R >>', b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
        b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 400 300] /Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>',
        b'<< /Length '+str(len(content)).encode()+b' >>\nstream\n'+content+b'\nendstream',
        b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>']
    result = b'%PDF-1.4\n'
    offsets = [0]
    for index, obj in enumerate(objects, 1):
        offsets.append(len(result)); result += str(index).encode()+b' 0 obj\n'+obj+b'\nendobj\n'
    xref = len(result)
    result += b'xref\n0 6\n0000000000 65535 f \n'
    result += b''.join(f'{offset:010d} 00000 n \n'.encode() for offset in offsets[1:])
    return result+b'trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n'+str(xref).encode()+b'\n%%EOF\n'


class TrainingHttpTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.previous_store = training_api._store
        training_api._store = Store(Path(self.temp.name)/'data')
        self.server = server.base.ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        self.old_port, self.old_base_port = server.PORT, server.base.PORT
        server.PORT = server.base.PORT = self.server.server_port
        self.url = 'http://127.0.0.1:'+str(self.server.server_port)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.metadata = {'code':'demo-cabinet','revision':'B','group_id':'cabinet-family','title':'Synthetic cabinet',
            'category':'cabinet','allow_storage':True,'allow_training':True,'allow_ai':False,'privacy_confirmed':True}

    def tearDown(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join()
        server.PORT, server.base.PORT = self.old_port, self.old_base_port
        training_api._store = self.previous_store
        self.temp.cleanup()

    def request(self, path, data=None, headers=None, raw=False):
        supplied = {'X-App-Token':server.base.TOKEN, **(headers or {})}
        body = data
        if isinstance(data, dict):
            body = json.dumps(data).encode(); supplied.setdefault('Content-Type','application/json')
        request = Request(self.url+path, data=body, headers=supplied)
        try: response = urlopen(request, timeout=15)
        except HTTPError as error: response = error
        with response:
            payload = response.read()
            return response.status, payload if raw else json.loads(payload)

    def register(self, metadata=None):
        status, drawing = self.request('/api/training/documents', sample_pdf(), headers={
            'Content-Type':'application/pdf','X-Training-Metadata':quote(json.dumps(metadata or self.metadata))})
        self.assertEqual(status, 201, drawing)
        return drawing

    def approved(self, drawing):
        status, annotation = self.request('/api/training/documents/'+drawing['id']+'/annotations', {
            'task_type':'dimension','question':'Width of the cabinet?', 'part_ref':'cabinet','ai_value':'',
            'corrected_value':'900','unit':'mm','evidence_type':'direct','formula':'',
            'explanation':'Front dimension label', 'evidence':[{'page':1,'bbox':[.1,.1,.8,.8],'view':'front'}],
            'author_ref':'expert-01'})
        self.assertEqual(status,200,annotation)
        for decision in ('submit','approve'):
            status, annotation = self.request('/api/training/annotations/'+annotation['id']+'/review', {
                'expected_version':annotation['version'],'decision':decision,'reviewer_ref':'reviewer-02',
                'comment':'Verified synthetic dimension','privacy_confirmed':True})
            self.assertEqual(status,200,annotation)
        return annotation

    def test_new_workspace_assets_and_unique_dom_ids(self):
        from html.parser import HTMLParser
        class IDs(HTMLParser):
            def __init__(self): super().__init__(); self.values=[]
            def handle_starttag(self, tag, attrs):
                for key,value in attrs:
                    if key=='id': self.values.append(value)
        status, html = self.request('/training', raw=True)
        self.assertEqual(status,200)
        parser=IDs();parser.feed(html.decode())
        self.assertEqual(len(parser.values),len(set(parser.values)))
        for value in ('trPrediction','trPredict','annotationForm','trCanvas','trExportButton'):
            self.assertIn(value,parser.values)
        for path in ('/training.js','/training.css','/'):
            self.assertEqual(self.request(path,raw=True)[0],200)

    def test_protection_and_invalid_upload(self):
        self.assertEqual(self.request('/api/training/documents',headers={'X-App-Token':''})[0],403)
        self.assertEqual(self.request('/api/training/documents',{},headers={'Origin':'https://evil.invalid'})[0],403)
        self.assertEqual(self.request('/api/training/documents',{},headers={'Host':'evil.invalid'})[0],403)
        status,_=self.request('/api/training/documents',b'not a pdf',headers={'Content-Type':'application/pdf',
            'X-Training-Metadata':quote(json.dumps(self.metadata))})
        self.assertEqual(status,400)
        self.assertEqual(training_api.store().stats()['drawings'],0)

    def test_manual_workflow_restart_persistence_and_png_evidence(self):
        drawing=self.register();annotation=self.approved(drawing)
        status,image=self.request('/api/training/documents/'+drawing['id']+'/page/1',raw=True)
        self.assertEqual(status,200);self.assertTrue(image.startswith(b'\x89PNG'))
        self.assertEqual(self.request('/api/training/documents/'+drawing['id']+'/page/2')[0],404)
        # Store reopened from disk: originals and annotations survive a process restart.
        training_api._store=Store(Path(self.temp.name)/'data')
        status,restored=self.request('/api/training/documents/'+drawing['id'])
        self.assertEqual(status,200);self.assertEqual(restored['annotations'][0]['status'],'approved')
        status,history=self.request('/api/training/annotations/'+annotation['id']+'/history')
        self.assertEqual(status,200);self.assertGreaterEqual(len(history['history']),3)

    def test_no_consent_means_no_codex_invocation(self):
        drawing=self.register()
        with patch.object(training_api._context['estimator'],'cli_status') as status:
            code,_=self.request('/api/training/documents/'+drawing['id']+'/predict',{'first':1,'last':1})
            self.assertEqual(code,403);status.assert_not_called()

    def test_revocation_cancels_training_job_and_blocks_inherited_ai_actions(self):
        drawing=self.register({**self.metadata,'allow_ai':True})
        ident='training-test-existing-job'
        job={'id':ident,'training_drawing_id':drawing['id'],'inflight':False,'status':'done','cancel':False}
        server.AI_JOBS[ident]=job
        try:
            self.assertEqual(self.request('/api/training/documents/'+drawing['id']+'/consent',{
                'allow_ai':False,'expected_version':drawing['consent_version']})[0],200)
            self.assertTrue(job['cancel'])
            for route in ('/api/chat','/api/create-drawing'):
                with patch.object(server.estimator,'call_codex') as cli:
                    code,_=self.request(route,{'id':ident,'question':'What is the width?'})
                    self.assertEqual(code,403);cli.assert_not_called()
        finally:
            del server.AI_JOBS[ident]

    def test_snapshot_keeps_revision_and_detects_changed_original(self):
        drawing=self.register();self.approved(drawing)
        self.request('/api/training/groups/cabinet-family/split',{'split':'train'})
        self.assertEqual(training_api.store().export_samples()['samples'][0]['source_revision'],'B')
        path=training_api.store().pdf_path(drawing['id'])
        path.write_bytes(sample_pdf()+b' changed source')
        self.assertEqual(self.request('/api/training/documents/'+drawing['id']+'/page/1')[0],409)

    def test_duplicate_revisions_share_one_image_asset_in_zip(self):
        first=self.register();second=self.register({**self.metadata,'revision':'C'})
        self.approved(first);self.approved(second)
        self.request('/api/training/groups/cabinet-family/split',{'split':'train'})
        code,data=self.request('/api/training/export',{'privacy_confirmation':True},raw=True)
        self.assertEqual(code,200)
        with zipfile.ZipFile(io.BytesIO(data)) as zipped:
            self.assertEqual(len(zipped.namelist()),len(set(zipped.namelist())))
            self.assertEqual(len([name for name in zipped.namelist() if name.endswith('.png')]),1)

    def test_opt_in_prediction_persists_original_report_but_does_not_create_gold(self):
        import time
        drawing=self.register({**self.metadata,'allow_ai':True})
        report={'summary':'Synthetic drawing','document_status':'draft','assumptions':[],
          'parts':[{'module':'cabinet','name':'panel','material':'MDF','thickness_mm':18,
            'width_mm':900,'length_mm':600,'quantity':1,'long_edges':0,'short_edges':0,
            'evidence':'Width label','source_pages':[1],'assumption_ids':[]}], 'hardware':[],'checks':[]}
        with patch.object(server.estimator,'cli_status',return_value={'logged_in':True,'message':'mock ready'}), \
             patch.object(server.estimator,'call_codex',return_value=report) as cli, \
             patch('training_api.prediction_version',return_value=('mock-codex-model','test-prompt-1')):
            status,job=self.request('/api/training/documents/'+drawing['id']+'/predict',{'first':1,'last':1})
            self.assertEqual(status,202,job)
            try:
                for _ in range(100):
                    status,result=self.request('/api/analysis/'+job['id'])
                    if result['status'] in ('done','error'): break
                    time.sleep(.03)
                self.assertEqual(result['status'],'done',result.get('error'))
                cli.assert_called_once()
                saved=training_api.store().get_drawing(drawing['id'])
                self.assertEqual(saved['predictions'][0]['report']['parts'][0]['width_mm'],900)
                report['parts'][0]['width_mm']=999
                self.assertEqual(training_api.store().get_drawing(drawing['id'])['predictions'][0]['report']['parts'][0]['width_mm'],900)
                self.assertEqual(saved['annotations'],[])
                self.assertEqual(training_api.store().export_samples()['samples'],[])
            finally:
                server.AI_JOBS.pop(job['id'],None)

    def test_approved_zip_contains_matching_images_and_no_identity(self):
        drawing=self.register();self.approved(drawing)
        self.request('/api/training/groups/cabinet-family/split',{'split':'train'})
        status,archive=self.request('/api/training/export',{'privacy_confirmation':True},raw=True)
        self.assertEqual(status,200)
        with zipfile.ZipFile(io.BytesIO(archive)) as zipped:
            row=json.loads(zipped.read('train.jsonl').decode())
            self.assertEqual(row['target']['value'],'900')
            self.assertIn(row['evidence'][0]['image'],zipped.namelist())
            self.assertTrue(zipped.read(row['evidence'][0]['image']).startswith(b'\x89PNG'))
            self.assertEqual(zipped.read('test.jsonl'),b'')
            for name in ('train.jsonl','manifest.json'):
                body=zipped.read(name).decode()
                self.assertNotIn('expert-01',body);self.assertNotIn('reviewer-02',body)
                self.assertNotIn('Synthetic cabinet',body);self.assertNotIn(str(self.temp.name),body)
        self.assertEqual(self.request('/api/training/documents/'+drawing['id']+'/consent',{'allow_training':False})[0],200)
        self.assertEqual(self.request('/api/training/export',{'privacy_confirmation':True})[0],409)

    def test_concurrent_edit_requires_new_review_before_export(self):
        drawing=self.register();annotation=self.approved(drawing)
        old=annotation.copy();old['id']=old.pop('id');old['expected_version']=annotation['version']
        old['corrected_value']='910'
        status,_=self.request('/api/training/documents/'+drawing['id']+'/annotations',old)
        self.assertEqual(status,200)
        self.assertEqual(training_api.store().export_samples()['samples'],[])
        self.assertEqual(self.request('/api/training/annotations/'+annotation['id']+'/review',{
            'expected_version':annotation['version'],'decision':'approve','reviewer_ref':'reviewer-02',
            'comment':'outdated approval','privacy_confirmed':True})[0],409)


if __name__=='__main__': unittest.main()

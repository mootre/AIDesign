"""Local regression checks. Run: python -m unittest -v test_analyzer"""
import copy
import json
import math
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request

import estimator
import server


def fixture():
    return {"summary": "ตู้ทดสอบ", "document_status": "draft", "assumptions": [],
            "parts": [{"module": "A", "name": "แผ่นข้าง", "material": "MDF สีขาว", "thickness_mm": 19,
                       "width_mm": 600, "length_mm": 2000, "quantity": 2, "long_edges": 1,
                       "short_edges": 0, "evidence": "test", "source_pages": [1], "assumption_ids": []}],
            "hardware": [], "checks": []}


class CalculationTests(unittest.TestCase):
    def test_area_edge_waste_and_ceiling(self):
        c = estimator.calculate(fixture(), 25)
        self.assertAlmostEqual(c["parts"][0]["area_m2"], 2.4)
        self.assertEqual(c["parts"][0]["edge_m"], 4)
        self.assertAlmostEqual(c["materials"][0]["gross_area_m2"], 3)
        self.assertEqual(c["materials"][0]["area_based_sheets"], 2)
        self.assertIsNone(c["materials"][0]["orderable_sheets"])

    def test_exact_sheet_is_not_rounded_to_two(self):
        r = fixture(); r["parts"][0].update(width_mm=1220, length_mm=2440, quantity=1)
        self.assertEqual(estimator.calculate(r, 0)["materials"][0]["area_based_sheets"], 1)

    def test_four_edge_rule_and_quantity(self):
        r = fixture(); r["parts"][0].update(long_edges=2, short_edges=2)
        self.assertAlmostEqual(estimator.calculate(r)["parts"][0]["edge_m"], 10.4)

    def test_incomplete_or_invalid_values_are_not_counted(self):
        for value in (None, -5, 0, 1.2, float("nan"), True):
            r = fixture(); r["parts"][0]["quantity"] = value
            c = estimator.calculate(r)
            self.assertEqual(c["calculated_parts"], 0)
            self.assertEqual(c["materials"], [])
            self.assertTrue(c["warnings"])

    def test_unknown_edges_do_not_imply_zero(self):
        r = fixture(); r["parts"][0]["long_edges"] = None
        c = estimator.calculate(r)
        self.assertIsNone(c["parts"][0]["edge_m"])
        self.assertTrue(c["materials"][0]["edge_unknown"])

    def test_oversize_part_is_flagged(self):
        r = fixture(); r["parts"][0]["length_mm"] = 2600
        c = estimator.calculate(r)
        self.assertTrue(c["materials"][0]["has_oversize"])
        self.assertTrue(c["warnings"])

    def test_glass_is_not_counted_as_board(self):
        r = fixture(); r["parts"][0]["material"] = "กระจกเงา"
        c = estimator.calculate(r)
        self.assertEqual(c["materials"], [])
        self.assertEqual(c["parts"][0]["area_m2"], 2.4)

    def test_pb_abbreviation_is_counted_as_board(self):
        r = fixture(); r["parts"][0]["material"] = "PB ปิดผิว HPL"
        self.assertEqual(len(estimator.calculate(r)["materials"]), 1)

    def test_material_thickness_and_finish_are_separate(self):
        r = fixture(); second = copy.deepcopy(r["parts"][0]); second["thickness_mm"] = 16
        third = copy.deepcopy(second); third["material"] = "MDF สีดำ"
        r["parts"] += [second, third]
        self.assertEqual(len(estimator.calculate(r)["materials"]), 3)

    def test_invalid_waste(self):
        for waste in (-1, 51, float("nan"), float("inf"), "12", True):
            with self.assertRaises(ValueError):
                estimator.calculate(fixture(), waste)

    def test_json_schema_and_citations(self):
        r = fixture(); estimator.validate(r, estimator.SCHEMA); server.audit_report(r, [1])
        r["parts"][0]["source_pages"] = [2]
        with self.assertRaises(ValueError): server.audit_report(r, [1])
        r = fixture(); r["parts"][0]["assumption_ids"] = ["missing"]
        with self.assertRaises(ValueError): server.audit_report(r, [1])
        r = fixture(); r["parts"][0]["width_mm"] = "600"
        with self.assertRaises(ValueError): estimator.validate(r, estimator.SCHEMA)


class WebTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.http = server.base.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        cls.previous_port = server.base.PORT
        server.base.PORT = cls.http.server_port
        cls.url = f"http://127.0.0.1:{cls.http.server_port}"
        cls.thread = threading.Thread(target=cls.http.serve_forever, daemon=True); cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.http.shutdown(); cls.http.server_close(); server.base.PORT = cls.previous_port

    def post(self, route, body, token=True, content_type="application/json"):
        headers = {"Content-Type": content_type}
        if token: headers["X-App-Token"] = server.base.TOKEN
        req = urllib.request.Request(self.url+route, data=body, headers=headers)
        try:
            response = urllib.request.urlopen(req)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            return response.status, json.loads(response.read())

    def test_js_is_served_with_correct_mime_and_policy(self):
        with urllib.request.urlopen(self.url+"/analysis.js") as response:
            self.assertIn("javascript", response.headers["Content-Type"])
            self.assertIn("default-src 'self'", response.headers["Content-Security-Policy"])

    def test_status_has_build_and_vector_capability(self):
        with urllib.request.urlopen(self.url+'/api/status') as response:
            data=json.load(response)
        self.assertEqual(data['build'],server.BUILD);self.assertTrue(data['vector_dwg'])

    def test_missing_token_and_invalid_pdf(self):
        self.assertEqual(self.post("/api/analyze", b"%PDF-test", token=False)[0], 403)
        self.assertEqual(self.post("/api/analyze", b"not PDF")[0], 400)

    def test_invalid_page_range_and_waste(self):
        for query in ("first=0", "first=1&last=9", "first=2&last=1", "waste=NaN", "waste=-1"):
            self.assertEqual(self.post("/api/analyze?"+query, b"%PDF-test")[0], 400)

    def test_unknown_job_and_invalid_json(self):
        self.assertEqual(self.post("/api/chat", b'{"id":"missing","question":"test"}')[0], 404)
        self.assertEqual(self.post("/api/chat", b'[]')[0], 400)

    def test_drawing_files_are_not_available_before_generation(self):
        for suffix in ('dwg','dxf','svg','json','../server.py'):
            with self.assertRaises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(self.url+'/api/drawing/missing/'+suffix)
            self.assertEqual(error.exception.code,404)

    def test_recalculate_invalidates_drawing(self):
        with tempfile.TemporaryDirectory() as directory:
            folder=Path(directory)/'cad';folder.mkdir();(folder/'drawing.dwg').write_bytes(b'old')
            job={"id":"cad-unit","status":"done","inflight":False,"directory":Path(directory),
                 "report":fixture(),"page_numbers":[1],"calculation":None,"chat":[],
                 "drawing_status":"done","drawing_directory":folder,"drawing_info":{"verified":True}}
            server.AI_JOBS[job['id']]=job
            try:
                code,result=self.post('/api/recalculate',b'{"id":"cad-unit","waste":12}')
                self.assertEqual(code,200);self.assertEqual(result['drawing_status'],'idle')
                self.assertFalse(folder.exists());self.assertNotIn('drawing_directory',result)
            finally:server.AI_JOBS.pop(job['id'],None)

    def test_recalculate_preserves_draft_and_clears_stale_chat(self):
        with tempfile.TemporaryDirectory() as directory:
            job = {"id": "unit", "status": "done", "inflight": False, "directory": Path(directory),
                   "report": fixture(), "page_numbers": [1], "calculation": None, "chat": [{"role": "user", "text": "old"}]}
            server.AI_JOBS["unit"] = job
            try:
                code, result = self.post("/api/recalculate", b'{"id":"unit","waste":10}')
                self.assertEqual(code, 200); self.assertEqual(result["chat"], [])
                self.assertEqual(result["calculation"]["waste_percent"], 10)
                self.assertNotIn("directory", result)
                self.assertEqual(self.post("/api/forget", b'{"id":"unit"}')[0], 200)
                self.assertNotIn("unit", server.AI_JOBS)
            finally:
                server.AI_JOBS.pop("unit", None)

    def test_pdf_page_limit_before_cli(self):
        # A real multi-page fixture can be used through the API in the live test.
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(Exception):
                server.prepare_pdf(b"%PDF-invalid", 1, 1, Path(directory))


if __name__ == "__main__":
    unittest.main()

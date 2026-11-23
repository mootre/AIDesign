import copy
import unittest

import drawing


def element(kind="polyline"):
    return {"kind": kind, "points": [{"x": 0, "y": 0}, {"x": 1200, "y": 0}, {"x": 1200, "y": 600}, {"x": 0, "y": 600}],
            "closed": True, "center_x": None, "center_y": None, "radius": None,
            "start_angle": None, "end_angle": None, "text": "", "height_mm": None,
            "offset_mm": None, "basis": "explicit", "evidence": "PDF page 1, 1200 x 600"}


def fixture():
    return {"title": "Test", "units": "mm", "notes": [], "assumptions": [],
            "views": [{"name": "Top", "source_pages": [1], "elements": [element()]}]}


class DrawingValidationTests(unittest.TestCase):
    def test_valid_geometry(self):
        drawing.validate(fixture(), [1])

    def test_wrong_units(self):
        s=fixture();s["units"]="inch"
        with self.assertRaises(ValueError):drawing.validate(s,[1])

    def test_missing_source_page(self):
        s=fixture();s["views"][0]["source_pages"]=[2]
        with self.assertRaises(ValueError):drawing.validate(s,[1])

    def test_disallow_executable_or_external_entities(self):
        for kind in ("XREF", "INSERT", "OLE", "script", "3DSOLID"):
            s=fixture();s["views"][0]["elements"][0]["kind"]=kind
            with self.assertRaises(ValueError):drawing.validate(s,[1])

    def test_unbounded_and_nonfinite_coordinates(self):
        for value in (float('nan'),float('inf'),100001):
            s=fixture();s["views"][0]["elements"][0]["points"][0]["x"]=value
            with self.assertRaises(ValueError):drawing.validate(s,[1])

    def test_dimensions_cannot_fake_values(self):
        s=fixture();e=element('dimension');e.update(points=[{"x":0,"y":0},{"x":1200,"y":0}],offset_mm=50,text="1500")
        s['views'][0]['elements'].append(e)
        with self.assertRaises(ValueError):drawing.validate(s,[1])
        e['text']='';drawing.validate(s,[1])

    def test_zero_dimension_and_negative_circle(self):
        s=fixture();e=element('dimension');e.update(points=[{"x":0,"y":0}]*2,offset_mm=50)
        s['views'][0]['elements'].append(e)
        with self.assertRaises(ValueError):drawing.validate(s,[1])
        e.update(kind='circle',center_x=10,center_y=10,radius=-2)
        with self.assertRaises(ValueError):drawing.validate(s,[1])

    def test_view_requires_geometry(self):
        s=fixture();e=element('text');e.update(points=[{"x":0,"y":0}],text='No geometry',height_mm=20)
        s['views'][0]['elements']=[e]
        with self.assertRaises(ValueError):drawing.validate(s,[1])

    def test_size_limits_and_duplicate_assumptions(self):
        s=fixture();s['views'][0]['elements']=[element()]*401
        with self.assertRaises(ValueError):drawing.validate(s,[1])

    def test_basic_isometric_has_geometry_only(self):
        s=fixture();drawing.validate_isometric(s,[1]);self.assertEqual(s['mode'],'isometric_basic')

    def test_basic_isometric_rejects_text_and_dimensions(self):
        for kind in ('text','dimension'):
            s=fixture();e=element(kind)
            if kind=='text':e.update(points=[{'x':0,'y':0}],text='REMOVE THIS',height_mm=20)
            else:e.update(points=[{'x':0,'y':0},{'x':100,'y':0}],offset_mm=20)
            s['views'][0]['elements'].append(e)
            with self.assertRaises(ValueError):drawing.validate_isometric(s,[1])

    def test_basic_isometric_rejects_missing_illustration_and_too_much_detail(self):
        s=fixture();s['views']=[]
        with self.assertRaisesRegex(ValueError,'Isometric'):drawing.validate_isometric(s,[1])
        s=fixture();s['views'][0]['elements']=[element()]*181
        with self.assertRaises(ValueError):drawing.validate_isometric(s,[1])
        s=fixture();a={"id":"A1","item":"x","value":"1","reason":"test","source_pages":[1]};s['assumptions']=[a,a]
        with self.assertRaises(ValueError):drawing.validate(s,[1])


if __name__=='__main__':unittest.main()

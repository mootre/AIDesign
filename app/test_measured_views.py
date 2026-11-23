import copy
import unittest
from unittest.mock import patch

import measured_views as mv
from test_drawing import element


def fixture():
    return {'title':'Test','units':'mm','notes':[],'assumptions':[],
            'detected_groups':[{'name':'TOP VIEW','kind':'TOP_VIEW','source_pages':[1],'evidence':'PDF top view'}],
            'omitted_groups':[],
            'views':[{'name':'TOP VIEW','kind':'TOP_VIEW','source_pages':[1],'elements':[element()],
                      'references':[{'p1':{'x':0,'y':0},'p2':{'x':1200,'y':0},'axis':'x','value_mm':1200,'evidence':'1200'},
                                    {'p1':{'x':0,'y':0},'p2':{'x':0,'y':600},'axis':'y','value_mm':600,'evidence':'600'}]}]}


class MeasuredViewTests(unittest.TestCase):
    def test_real_coordinates_pass_with_checks(self):
        s=fixture();mv.validate(s,[1]);self.assertEqual(s['mode'],'measured_views');self.assertEqual(len(s['measurement_checks']),2)

    def test_setting_mm_header_does_not_fix_wrong_scale(self):
        s=fixture();s['views'][0]['references'][0]['value_mm']=1500
        with self.assertRaisesRegex(ValueError,'1200'):mv.validate(s,[1])

    def test_normalized_coordinates_rejected(self):
        s=fixture()
        for p in s['views'][0]['elements'][0]['points']:p['x']*=1000/1200
        with self.assertRaises(ValueError):mv.validate(s,[1])

    def test_calibration_endpoints_must_touch_structure(self):
        s=fixture();r=s['views'][0]['references'][0];r['p1']['y']=20;r['p2']['y']=20
        with self.assertRaisesRegex(ValueError,'จุดตรวจขนาด'):mv.validate(s,[1])

    def test_both_axes_required(self):
        s=fixture();s['views'][0]['references'].pop()
        with self.assertRaises(ValueError):mv.validate(s,[1])

    def test_detected_second_elevation_must_be_accounted_for(self):
        s=fixture();s['detected_groups'].append({'name':'ELEVATION B','kind':'ELEVATION','source_pages':[1],'evidence':'PDF label'})
        with self.assertRaisesRegex(ValueError,'ELEVATION B'):mv.validate(s,[1])
        s['omitted_groups']=[{'name':'ELEVATION B','reason':'Missing height dimension'}];mv.validate(s,[1])

    def test_cannot_invent_view_not_in_inventory(self):
        s=fixture();s['views'][0]['name']='INSIDE VIEW'
        with self.assertRaises(ValueError):mv.validate(s,[1])

    def test_isometric_is_reference_only(self):
        s=fixture();iso=copy.deepcopy(s['views'][0]);iso.update(name='ISOMETRIC',kind='ISOMETRIC',references=[])
        s['detected_groups'].append({'name':'ISOMETRIC','kind':'ISOMETRIC','source_pages':[1],'evidence':'PDF iso'});s['views'].append(iso)
        mv.validate(s,[1]);self.assertEqual(len(s['measurement_checks']),2)
        s.pop('mode');s.pop('measurement_checks');iso['references']=copy.deepcopy(s['views'][0]['references'])
        with self.assertRaisesRegex(ValueError,'Isometric'):mv.validate(s,[1])

    def test_one_correction_pass_is_validated(self):
        wrong=fixture();wrong['views'][0]['references'][0]['value_mm']=1000
        with patch('measured_views.estimator.call_codex',side_effect=[wrong,fixture()]) as call:
            result=mv.generate('.',[],[1],'PDF 1200 x 600')
        self.assertEqual(call.call_count,2);self.assertEqual(result['measurement_checks'][0]['actual_mm'],1200)

    def test_isometric_alone_cannot_claim_measurable_drawing(self):
        s=fixture();s['detected_groups'][0].update(name='ISOMETRIC',kind='ISOMETRIC')
        s['views'][0].update(name='ISOMETRIC',kind='ISOMETRIC',references=[])
        with self.assertRaisesRegex(ValueError,'ไม่มีรูปฉาย'):mv.validate(s,[1])

    def test_native_pdf_elevation_count_cannot_be_dropped(self):
        s=fixture()
        with self.assertRaises(ValueError):mv.validate_inventory(s,'TOP VIEW ELEVATION A ELEVATION B INSIDE VIEW')
        s['detected_groups'] += [{'name':n,'kind':k,'source_pages':[1],'evidence':'PDF'} for n,k in [('ELEVATION A','ELEVATION'),('ELEVATION B','ELEVATION'),('INSIDE VIEW','INSIDE_VIEW')]]
        mv.validate_inventory(s,'TOP VIEW ELEVATION A ELEVATION B INSIDE VIEW')

    def test_single_elevation_does_not_require_second_view(self):
        s=fixture();s['detected_groups'].append({'name':'ELEVATION A','kind':'ELEVATION','source_pages':[1],'evidence':'PDF'})
        mv.validate_inventory(s,'TOP VIEW ELEVATION A')


if __name__=='__main__':unittest.main()

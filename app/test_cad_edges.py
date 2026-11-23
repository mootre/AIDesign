"""Run with the CAD Python interpreter that provides ezdxf."""
import unittest
import ezdxf
from cad_worker import add_structural_path


class SelectableEdgeTests(unittest.TestCase):
    def setUp(self):
        self.doc=ezdxf.new();self.msp=self.doc.modelspace()

    def test_single_inside_edge_is_native_line(self):
        edges=add_structural_path(self.msp,[(200,2200),(1500,2200)],False,'0')
        self.assertEqual(len(edges),1);self.assertEqual(edges[0].dxftype(),'LINE')
        self.assertAlmostEqual((edges[0].dxf.end-edges[0].dxf.start).magnitude,1300)

    def test_open_corner_is_separately_selectable_segments(self):
        edges=add_structural_path(self.msp,[(0,0),(500,0),(500,800)],False,'0')
        self.assertEqual([e.dxftype() for e in edges],['LINE','LINE'])
        self.assertEqual([(e.dxf.end-e.dxf.start).magnitude for e in edges],[500,800])

    def test_closed_rectangle_retains_width_height_selection(self):
        edges=add_structural_path(self.msp,[(0,0),(1500,0),(1500,2255),(0,2255)],True,'0')
        self.assertEqual(len(edges),1);self.assertTrue(edges[0].closed)
        self.assertEqual(edges[0].dxftype(),'LWPOLYLINE')

    def test_zero_length_segment_does_not_create_unselectable_edge(self):
        edges=add_structural_path(self.msp,[(0,0),(0,0),(100,0)],False,'0')
        self.assertEqual(len(edges),1)


if __name__=='__main__':unittest.main()

import math
import unittest

from pdf_vector import MM_PER_PT, display_point, cubic


class PdfVectorTests(unittest.TestCase):
    def test_points_are_paper_millimetres(self):
        x,y=display_point(72,144,(0,0,300,500),0)
        self.assertAlmostEqual(x,25.4);self.assertAlmostEqual(y,50.8)

    def test_crop_origin_and_all_rotations(self):
        box=(10,20,110,220)
        expected={0:(0,0),90:(0,100),180:(100,200),270:(200,0)}
        for rotation,(x,y) in expected.items():
            result=display_point(10,20,box,rotation)
            self.assertAlmostEqual(result[0],x*MM_PER_PT)
            self.assertAlmostEqual(result[1],y*MM_PER_PT)

    def test_rotated_wardrobe_pdf_landscape(self):
        x,y=display_point(100,700,(0,0,595,842),270)
        self.assertAlmostEqual(x,142*MM_PER_PT)
        self.assertAlmostEqual(y,100*MM_PER_PT)

    def test_bezier_curve_retains_endpoint_and_shape(self):
        pts=cubic((0,0),(0,10),(10,10),(10,0))
        self.assertEqual(pts[-1],(10,0));self.assertGreater(len(pts),4)
        self.assertTrue(any(abs(x-5)<.01 and abs(y-7.5)<.01 for x,y in pts))
        self.assertTrue(all(0<=x<=10 and 0<=y<=7.5 for x,y in pts))

    def test_straight_and_degenerate_beziers_are_safe(self):
        self.assertEqual(cubic((0,0),(1,0),(2,0),(3,0)),[(3,0)])
        self.assertEqual(cubic((0,0),(0,0),(0,0),(0,0)),[(0,0)])


if __name__=='__main__':unittest.main()

"""Import existing PDF paths into CAD at paper scale, without inventing real dimensions."""
import ctypes
import math
import pypdfium2 as pdfium

MM_PER_PT = 25.4 / 72


def display_point(x, y, box, rotation):
    left,bottom,right,top=box;x-=left;y-=bottom;w=right-left;h=top-bottom
    if rotation==90:x,y=y,w-x
    elif rotation==180:x,y=w-x,h-y
    elif rotation==270:x,y=h-y,x
    return (x*MM_PER_PT,y*MM_PER_PT)


def cubic(a,b,c,d,tolerance=.015,depth=0):
    """Flatten cubic Beziers to paper-mm tolerance, preserving their endpoint."""
    def distance(p):
        dx=d[0]-a[0];dy=d[1]-a[1];length=math.hypot(dx,dy)
        return math.hypot(p[0]-a[0],p[1]-a[1]) if length<1e-12 else abs(dx*(a[1]-p[1])-(a[0]-p[0])*dy)/length
    if depth>=12 or max(distance(b),distance(c))<=tolerance:return [d]
    def mid(p,q):return ((p[0]+q[0])/2,(p[1]+q[1])/2)
    ab=mid(a,b);bc=mid(b,c);cd=mid(c,d);abc=mid(ab,bc);bcd=mid(bc,cd);m=mid(abc,bcd)
    return cubic(a,ab,abc,m,tolerance,depth+1)+cubic(m,bcd,cd,d,tolerance,depth+1)


def color(obj,stroke):
    values=[ctypes.c_uint() for _ in range(4)]
    fn=pdfium.raw.FPDFPageObj_GetStrokeColor if stroke else pdfium.raw.FPDFPageObj_GetFillColor
    if not fn(obj,*values):return [0,0,0,255]
    return [v.value for v in values]


def extract(raw, first=1, last=0):
    doc=pdfium.PdfDocument(raw)
    views=[];path_count=0;image_count=0;clipped=0;text_count=0
    try:
        end=last or len(doc)
        if not 1<=first<=end<=len(doc) or end-first+1>8:
            raise ValueError("เลือกช่วง PDF 1–8 หน้าสำหรับสร้าง DWG")
        for index in range(first-1,end):
            page=doc[index];tp=page.get_textpage();items=[]
            try:
                box=page.get_bbox();rotation=page.get_rotation()
                def transform(obj,x,y):
                    x,y=obj.get_matrix().on_point(x,y)
                    parent=obj.container
                    while parent:
                        x,y=parent.get_matrix().on_point(x,y);parent=parent.container
                    return display_point(x,y,box,rotation)
                for obj in page.get_objects(textpage=tp):
                    if obj.type==pdfium.raw.FPDF_PAGEOBJ_PATH:
                        count=pdfium.raw.FPDFPath_CountSegments(obj)
                        if count>100000:raise ValueError("เส้น PDF ซับซ้อนเกินขอบเขต")
                        contours=[];points=[];controls=[]
                        for i in range(count):
                            segment=pdfium.raw.FPDFPath_GetPathSegment(obj,i)
                            x=ctypes.c_float();y=ctypes.c_float()
                            if not pdfium.raw.FPDFPathSegment_GetPoint(segment,x,y):continue
                            p=transform(obj,x.value,y.value);kind=pdfium.raw.FPDFPathSegment_GetType(segment)
                            close=bool(pdfium.raw.FPDFPathSegment_GetClose(segment))
                            if kind==pdfium.raw.FPDF_SEGMENT_MOVETO:
                                if len(points)>1:contours.append({"points":points,"closed":False})
                                points=[p];controls=[]
                            elif kind==pdfium.raw.FPDF_SEGMENT_LINETO:
                                points.append(p)
                            elif kind==pdfium.raw.FPDF_SEGMENT_BEZIERTO:
                                controls.append(p)
                                if len(controls)==3 and points:
                                    points+=cubic(points[-1],*controls);controls=[]
                            if close and len(points)>1:
                                contours.append({"points":points,"closed":True});points=[]
                        if len(points)>1:contours.append({"points":points,"closed":False})
                        if not contours:continue
                        fill=ctypes.c_int();stroke=ctypes.c_int();width=ctypes.c_float()
                        pdfium.raw.FPDFPath_GetDrawMode(obj,fill,stroke)
                        pdfium.raw.FPDFPageObj_GetStrokeWidth(obj,width)
                        # Keep draw order, opaque white masking paths, colours and closed filled contours.
                        stroke_color=color(obj,True) if stroke.value else None
                        fill_color=color(obj,False) if fill.value else None
                        if not stroke_color and not fill_color:continue
                        x0,y0=transform(obj,0,0);x1,y1=transform(obj,1,0)
                        items.append({"kind":"path","contours":contours,"stroke_color":stroke_color,"fill_color":fill_color,
                                      "fill_rule":fill.value,"stroke_width":abs(width.value)*math.hypot(x1-x0,y1-y0)})
                        path_count+=1
                        clip=pdfium.raw.FPDFPageObj_GetClipPath(obj)
                        if clip and pdfium.raw.FPDFClipPath_CountPaths(clip)>0:clipped+=1
                    elif obj.type==pdfium.raw.FPDF_PAGEOBJ_TEXT:
                        length=pdfium.raw.FPDFTextObj_GetText(obj,tp,None,0)
                        if length<2 or length>100000:continue
                        buffer=(ctypes.c_ushort*((length+1)//2))()
                        pdfium.raw.FPDFTextObj_GetText(obj,tp,buffer,length)
                        text=bytes(buffer).decode('utf-16-le',errors='replace').rstrip('\x00')
                        if not text.strip():continue
                        x,y=transform(obj,0,0);dx,dy=transform(obj,1,0);ux,uy=transform(obj,0,1)
                        bounds=obj.get_bounds()
                        # PDF text has embedded-font metrics; use a native CAD text with a fitted baseline width.
                        font_size=obj.get_font_size()
                        fontsize=font_size*math.hypot(ux-x,uy-y)*.72
                        items.append({"kind":"pdf_text","text":text,"x":x,"y":y,"height":max(fontsize,.1),
                                      "rotation":math.degrees(math.atan2(dy-y,dx-x)),"color":color(obj,False)})
                        text_count+=1
                    elif obj.type==pdfium.raw.FPDF_PAGEOBJ_IMAGE:image_count+=1
                    if path_count>120000:raise ValueError("PDF มีเส้นเกิน 120,000 รายการ กรุณาลดช่วงหน้า")
                w,h=page.get_size()
                views.append({"name":f"PDF page {index+1}","source_pages":[index+1],"width_mm":w*MM_PER_PT,"height_mm":h*MM_PER_PT,"elements":items})
            finally:
                tp.close();page.close()
    finally:doc.close()
    if not path_count:raise ValueError("ไม่พบเส้นเวกเตอร์ใน PDF นี้ อาจเป็นภาพสแกน กรุณาใช้โหมด AI หรือไฟล์ต้นฉบับจาก CAD")
    notes=["นำเข้าเส้นจาก PDF โดยตรง ไม่ได้ให้ AI สร้างมุมมองใหม่",
           "พิกัดเป็นมิลลิเมตรบนหน้ากระดาษ PDF (1 point = 25.4/72 มม.) ไม่ยืนยันว่าเป็นขนาดชิ้นงานจริง 1:1",
           "ข้อความขนาดที่มีใน PDF ยังคงเป็นข้อความ/เส้นที่นำเข้า ไม่ใช่ native CAD Dimension ที่ผูกกับรูปทรง",
           "อาจรวมเส้นหรือข้อความในชั้นซ่อนที่ฝังอยู่ใน PDF ด้วย ต้องเทียบกับหน้าที่มองเห็นก่อนใช้เป็นแบบผลิต",
           "ส่วนโค้ง Bezier แปลงเป็นเส้นย่อย ค่าคลาดเคลื่อนเป้าหมาย 0.015 มม. ที่สเกลกระดาษ; ฟอนต์อาจเปลี่ยนรูปเมื่อแทนด้วย Tahoma"]
    if image_count:notes.append(f"ไม่ได้นำเข้าภาพ bitmap {image_count} รายการ เช่น โลโก้หรือลายเซ็น")
    if clipped:notes.append(f"พบ clipping path {clipped} รายการ ยังไม่รองรับการตัดเส้นซ้อนรูปทรงทุกรูปแบบ ต้องเทียบภาพตัวอย่างกับ PDF")
    return {"mode":"pdf_vector","title":"PDF vector import - PAPER SCALE / VERIFY BEFORE USE","units":"mm","notes":notes,
            "assumptions":[],"views":views,"source_stats":{"paths":path_count,"text_objects":text_count,"omitted_images":image_count,"clipped_paths":clipped}}

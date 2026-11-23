"""Runs with a CAD-enabled Python. Produces editable R2018 DWG and a DXF twin."""
import hashlib
import html
import json
import math
import re
import os
from pathlib import Path
import subprocess
import sys
from collections import Counter

import ezdxf


def clean(text):
    # Plain TEXT only: no CAD fields, hyperlinks, commands or external references.
    return str(text).replace("%<", "").replace(">%", "").replace("\\", "/").replace("\n", " ").replace("\r", " ")[:240]


def bounds(view):
    pts=[]
    for e in view["elements"]:
        if e["kind"] == "polyline":
            pts += [(p["x"], p["y"]) for p in e["points"]]
        elif e["kind"] in ("circle", "arc"):
            cx,cy,r=e["center_x"],e["center_y"],e["radius"]
            angles = [0,90,180,270] if e["kind"]=="circle" else [e["start_angle"],e["end_angle"]]
            if e["kind"]=="arc":
                start=e["start_angle"]%360; span=(e["end_angle"]-e["start_angle"])%360
                angles += [a for a in (0,90,180,270) if (a-start)%360<=span]
            pts += [(cx+r*math.cos(math.radians(a)),cy+r*math.sin(math.radians(a))) for a in angles]
    xs,ys=zip(*pts)
    return min(xs),min(ys),max(xs),max(ys)


def convert(exe, source, destination, output_type, pattern):
    env=os.environ.copy()
    for key in ("QT_QPA_PLATFORM", "QT_PLUGIN_PATH", "QT_QPA_PLATFORM_PLUGIN_PATH", "QT_QPA_FONTDIR"):
        env.pop(key,None)
    r=subprocess.run([str(exe),str(source),str(destination),"ACAD2018",output_type,"0","1",pattern],
                     capture_output=True,env=env,timeout=75,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
    if r.returncode:
        raise ValueError(f"ODA conversion failed: exit {r.returncode}")


def add_structural_path(msp,points,closed,layer):
    """Closed plates keep width/height; each open edge is a selectable native LINE."""
    if closed:
        return [msp.add_lwpolyline(points,close=True,dxfattribs={'layer':layer})]
    return [msp.add_line(a,b,dxfattribs={'layer':layer}) for a,b in zip(points,points[1:]) if math.dist(a,b)>1e-9]


def create_vector(spec,folder,converter):
    doc=ezdxf.new('R2018');doc.units=4;doc.header['$INSUNITS']=4;doc.header['$MEASUREMENT']=1
    doc.styles.new('THAI',dxfattribs={'font':'tahoma.ttf'})
    for layer in ('PDF_STROKES','PDF_FILLS','PDF_TEXT','IMPORT_NOTES'):doc.layers.new(layer)
    msp=doc.modelspace();svg=[];offset=0;maxheight=0
    def rgb(c):return '#%02x%02x%02x'%tuple(c[:3])
    def attrs(layer,c):return {'layer':layer,'true_color':ezdxf.colors.rgb2int(tuple(c[:3]))}
    def colour(entity,c):
        if len(c)>3 and c[3]<255:entity.transparency=1-c[3]/255
    for view in spec['views']:
        h=view['height_mm'];w=view['width_mm'];maxheight=max(maxheight,h)
        for item in view['elements']:
            if item['kind']=='path':
                contours=[([(x+offset,y) for x,y in c['points']],c['closed']) for c in item['contours']]
                fill=item['fill_color'];stroke=item['stroke_color']
                if fill and fill[3]>0:
                    hatch=msp.add_hatch(color=7,dxfattribs=attrs('PDF_FILLS',fill));colour(hatch,fill)
                    for points,closed in contours:
                        if len(points)>2:hatch.paths.add_polyline_path(points,is_closed=True,flags=0)
                    if not hatch.paths.paths:msp.delete_entity(hatch)
                if stroke and stroke[3]>0:
                    for points,closed in contours:
                        entity=msp.add_lwpolyline(points,close=closed,dxfattribs=attrs('PDF_STROKES',stroke));colour(entity,stroke)
                commands=[]
                for points,closed in contours:
                    commands.append('M '+' L '.join(f'{x},{-y}' for x,y in points)+(' Z' if closed else ''))
                svg.append('<path d="'+ ' '.join(commands)+'" fill="'+(rgb(fill) if fill else 'none')+'" fill-rule="'+('evenodd' if item['fill_rule']==1 else 'nonzero')+'" stroke="'+(rgb(stroke) if stroke else 'none')+'" stroke-width="'+str(max(item['stroke_width'],.035))+'"/>')
            else:
                c=item['color'];x=item['x']+offset;y=item['y'];height=item['height'];text=clean(item['text']);rotation=item['rotation']
                entity=msp.add_text(text,dxfattribs={**attrs('PDF_TEXT',c),'height':height,'insert':(x,y),'rotation':rotation,'style':'THAI'})
                colour(entity,c)
                svg.append(f'<text x="{x}" y="{-y}" font-size="{height}" fill="{rgb(c)}" transform="rotate({-rotation} {x} {-y})">{html.escape(text)}</text>')
        offset+=w+15
    # Explicit object ordering preserves opaque masks in the PDF instead of exposing covered geometry.
    msp.set_redraw_order([(entity.dxf.handle,f'{i+1:X}') for i,entity in enumerate(msp)])
    doc.set_modelspace_vport(height=maxheight*1.08,center=((offset-15)/2,maxheight/2))
    if doc.audit().has_errors:raise ValueError('PDF vector DXF audit failed')
    source=folder/'cad-source';source.mkdir(exist_ok=True);doc.saveas(source/'drawing.dxf')
    (folder/'drawing.dxf').write_bytes((source/'drawing.dxf').read_bytes())
    convert(converter,source,folder,'DWG','drawing.dxf')
    dwg=folder/'drawing.dwg'
    if not dwg.exists() or dwg.read_bytes()[:6]!=b'AC1032':raise ValueError('PDF vector output is not DWG R2018')
    check=folder/'cad-roundtrip';check.mkdir(exist_ok=True);convert(converter,folder,check,'DXF','drawing.dwg')
    reopened=ezdxf.readfile(check/'drawing.dxf')
    if reopened.audit().has_errors:raise ValueError('PDF vector DWG audit failed')
    expected=Counter(e.dxftype() for e in msp);actual=Counter(e.dxftype() for e in reopened.modelspace())
    if expected!=actual:raise ValueError('PDF vector entity counts changed in DWG conversion')
    def shapes(d):return sorted((tuple((round(x,4),round(y,4)) for x,y in e.get_points('xy')),bool(e.closed)) for e in d.modelspace().query('LWPOLYLINE'))
    if shapes(doc)!=shapes(reopened):raise ValueError('PDF vector coordinates changed in DWG conversion')
    if reopened.header.get('$INSUNITS')!=4:raise ValueError('PDF vector units changed in DWG conversion')
    width=offset-15
    svgtext=f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="-2 {-maxheight-2} {width+4} {maxheight+4}" style="background:white;font-family:Tahoma,sans-serif">'+''.join(svg)+'</svg>'
    (folder/'drawing.svg').write_text(svgtext,encoding='utf-8')
    result={'verified':True,'geometry_preserved':True,'version':'AutoCAD R2018','units':'mm','views':len(spec['views']),
            'mode':'pdf_vector','coordinate_system':'paper_mm','entity_counts':dict(expected),'dimension_values_mm':[],
            'source_stats':spec['source_stats'],'dwg_sha256':hashlib.sha256(dwg.read_bytes()).hexdigest(),
            'notice':'นำเข้าเส้นต้นฉบับที่สเกลกระดาษ PDF; ข้อความมิติไม่ใช่เส้นบอกขนาด CAD และไม่ได้รับรองขนาดชิ้นงาน 1:1'}
    (folder/'drawing-check.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    return result


def create_isometric(spec,folder,converter):
    """No labels, dimensions, legends or title block are allowed in this output."""
    doc=ezdxf.new('R2018');doc.units=4;doc.header['$INSUNITS']=4
    doc.layers.new('BASIC_STRUCTURE',dxfattribs={'color':7})
    msp=doc.modelspace();svg=[];offset=0;allpts=[]
    reference_checks=[];view_layers=[]
    for index,view in enumerate(spec['views']):
        box=bounds(view);tx=offset-box[0];ty=-box[1]
        label=re.sub(r'[^A-Za-z0-9_-]+','_',view['name']).strip('_') or view.get('kind','ISOMETRIC')
        layer='VIEW_'+str(index+1)+'_'+label[:70]
        if spec.get('mode')=='measured_views' and view.get('kind')=='ISOMETRIC':layer='REF_ISOMETRIC_NOT_FOR_MEASUREMENT_'+str(index+1)
        view_layers.append({'name':view['name'],'kind':view.get('kind','ISOMETRIC'),'layer':layer,'source_pages':view['source_pages'],
                            'measurable':view.get('kind') in ('TOP_VIEW','ELEVATION','INSIDE_VIEW')})
        if layer not in doc.layers:doc.layers.new(layer,dxfattribs={'color':7})
        handles=[]
        for e in view['elements']:
            if e['kind']=='polyline':
                points=[(p['x']+tx,p['y']+ty) for p in e['points']]
                entities=add_structural_path(msp,points,e['closed'],layer);handles.extend(e.dxf.handle for e in entities)
                tag='polygon' if e['closed'] else 'polyline';pts=' '.join(f'{x},{-y}' for x,y in points)
                svg.append(f'<{tag} points="{pts}" fill="none" stroke="#202c29" stroke-width="1.4" stroke-linejoin="round"/>');allpts+=points
            elif e['kind'] in ('circle','arc'):
                cx=e['center_x']+tx;cy=e['center_y']+ty;r=e['radius']
                if e['kind']=='circle':
                    entity=msp.add_circle((cx,cy),r,dxfattribs={'layer':layer});handles.append(entity.dxf.handle)
                    svg.append(f'<circle cx="{cx}" cy="{-cy}" r="{r}" fill="none" stroke="#202c29" stroke-width="1.4"/>')
                else:
                    entity=msp.add_arc((cx,cy),r,e['start_angle'],e['end_angle'],dxfattribs={'layer':layer});handles.append(entity.dxf.handle)
                    start=math.radians(e['start_angle']);span=math.radians((e['end_angle']-e['start_angle'])%360)
                    points=[(cx+r*math.cos(start+span*i/80),cy+r*math.sin(start+span*i/80)) for i in range(81)]
                    svg.append('<polyline points="'+' '.join(f'{x},{-y}' for x,y in points)+'" fill="none" stroke="#202c29" stroke-width="1.4"/>')
                allpts.extend([(cx-r,cy-r),(cx+r,cy+r)])
            else:raise ValueError('Basic isometric rejects text/dimensions')
        for ref in view.get('references',[]):
            a=(ref['p1']['x']+tx,ref['p1']['y']+ty);b=(ref['p2']['x']+tx,ref['p2']['y']+ty)
            reference_checks.append({'view':view['name'],'layer':layer,'p1':a,'p2':b,'axis':ref['axis'],'expected_mm':ref['value_mm'],'evidence':ref['evidence']})
        offset+=box[2]-box[0]+150
    if not allpts:raise ValueError('No isometric geometry')
    xs,ys=zip(*allpts);pad=35;xmin=min(xs)-pad;xmax=max(xs)+pad;ymin=min(ys)-pad;ymax=max(ys)+pad
    doc.set_modelspace_vport(height=ymax-ymin,center=((xmin+xmax)/2,(ymin+ymax)/2))
    if doc.audit().has_errors:raise ValueError('Isometric DXF audit failed')
    source=folder/'cad-source';source.mkdir(exist_ok=True);doc.saveas(source/'drawing.dxf');(folder/'drawing.dxf').write_bytes((source/'drawing.dxf').read_bytes())
    convert(converter,source,folder,'DWG','drawing.dxf');dwg=folder/'drawing.dwg'
    if not dwg.exists() or dwg.read_bytes()[:6]!=b'AC1032':raise ValueError('Not an R2018 DWG')
    check=folder/'cad-roundtrip';check.mkdir(exist_ok=True);convert(converter,folder,check,'DXF','drawing.dwg');again=ezdxf.readfile(check/'drawing.dxf')
    expected=Counter(e.dxftype() for e in msp);actual=Counter(e.dxftype() for e in again.modelspace())
    if expected!=actual or again.audit().has_errors:raise ValueError('Isometric roundtrip failed')
    forbidden={'TEXT','MTEXT','DIMENSION','HATCH','LEADER','MLEADER','IMAGE','INSERT'}
    if set(actual)&forbidden:raise ValueError('Text or detail entities found in basic isometric')
    def signature(d):
        entries=[]
        for e in d.modelspace():
            if e.dxftype()=='LWPOLYLINE':entries.append(('P',tuple((round(x,4),round(y,4)) for x,y in e.get_points('xy')),bool(e.closed)))
            elif e.dxftype()=='LINE':entries.append(('LINE',tuple(round(v,4) for v in e.dxf.start),tuple(round(v,4) for v in e.dxf.end)))
            else:entries.append((e.dxftype(),round(e.dxf.center.x,4),round(e.dxf.center.y,4),round(e.dxf.radius,4),
                                 round(e.dxf.start_angle,4) if e.dxftype()=='ARC' else 0,round(e.dxf.end_angle,4) if e.dxftype()=='ARC' else 0))
        return sorted(entries)
    if signature(doc)!=signature(again):raise ValueError('Isometric geometry changed')
    measured=spec.get('mode')=='measured_views'
    if measured:
        if again.header.get('$INSUNITS')!=4:raise ValueError('Measured DWG unit is not millimetres')
        for ref in reference_checks:
            vertices=[]
            for entity in again.modelspace():
                if entity.dxf.layer!=ref['layer']:continue
                if entity.dxftype()=='LWPOLYLINE':vertices+=list(entity.get_points('xy'))
                elif entity.dxftype()=='LINE':vertices += [(entity.dxf.start.x,entity.dxf.start.y),(entity.dxf.end.x,entity.dxf.end.y)]
                elif entity.dxftype()=='CIRCLE':
                    c=entity.dxf.center;r=entity.dxf.radius;vertices += [(c.x-r,c.y),(c.x+r,c.y),(c.x,c.y-r),(c.x,c.y+r)]
            if not vertices:raise ValueError('No DWG geometry for reference check')
            a=min(vertices,key=lambda p:math.dist(p,ref['p1']));b=min(vertices,key=lambda p:math.dist(p,ref['p2']))
            if math.dist(a,ref['p1'])>.05 or math.dist(b,ref['p2'])>.05:raise ValueError('Reference endpoint lost in DWG')
            actual=abs(b[0]-a[0]) if ref['axis']=='x' else abs(b[1]-a[1]) if ref['axis']=='y' else math.dist(a,b)
            if abs(actual-ref['expected_mm'])>max(.05,ref['expected_mm']*.0001):raise ValueError('Actual DWG measurement differs from PDF reference')
            ref['dwg_actual_mm']=actual
    (folder/'drawing.svg').write_text(f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{xmin} {-ymax} {xmax-xmin} {ymax-ymin}" style="background:white">'+''.join(svg)+'</svg>',encoding='utf-8')
    result={'verified':True,'geometry_preserved':True,'version':'AutoCAD R2018','units':'mm' if measured else 'relative drawing units','coordinate_system':'real_mm' if measured else 'relative_isometric',
            'mode':'measured_views' if measured else 'isometric_basic','views':len(spec['views']),'entity_counts':dict(expected),'dimension_values_mm':[],
            'measurement_scope':'orthographic_views_only' if measured else 'reference_only',
            'measurement_checks':reference_checks,'view_layers':view_layers,'selectable_open_edges':expected.get('LINE',0),
            'detected_groups':spec.get('detected_groups',[]),'omitted_groups':spec.get('omitted_groups',[]),
            'dwg_sha256':hashlib.sha256(dwg.read_bytes()).hexdigest(),'notice':'รูปฉาย 1:1 มม. ตรวจขนาดอ้างอิงใน DWG แล้ว ยังต้องตรวจแบบและมิติที่อนุมานก่อนผลิต' if measured else 'โครงสร้างพื้นฐานตามภาพ Isometric ไม่มีข้อความหรือมิติ ไม่ใช่ขนาดผลิต 1:1'}
    (folder/'drawing-check.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8');return result


def create(spec, folder, converter):
    doc=ezdxf.new("R2018",setup=True)
    doc.units=4
    doc.header["$INSUNITS"]=4
    doc.header["$MEASUREMENT"]=1
    doc.styles.new("THAI",dxfattribs={"font":"tahoma.ttf"})
    for name,color in (("SOURCE_GEOMETRY",7),("DERIVED_GEOMETRY",4),("ASSUMED_GEOMETRY",30),("DIMENSIONS",3),("NOTES",7)):
        doc.layers.new(name,dxfattribs={"color":color})
    doc.set_modelspace_vport(height=5000,center=(2000,0))
    msp=doc.modelspace()
    boxes=[bounds(v) for v in spec["views"]]
    width=max(b[2]-b[0] for b in boxes);height=max(b[3]-b[1] for b in boxes)
    width=max(width,100);height=max(height,100)
    unit=max(width,height)
    th=max(5,unit/85);gap=max(180,unit*.32)
    svgs=[];all_points=[]
    colors={"explicit":"#202b28","derived":"#216e86","assumed":"#b27622"}
    layers={"explicit":"SOURCE_GEOMETRY","derived":"DERIVED_GEOMETRY","assumed":"ASSUMED_GEOMETRY"}

    def text(value,x,y,size=th,layer="NOTES"):
        value=clean(value)
        msp.add_text(value,dxfattribs={"height":size,"insert":(x,y),"style":"THAI","layer":layer})
        svgs.append(f'<text x="{x}" y="{-y}" font-size="{size}" fill="#263b34">{html.escape(value)}</text>')
        all_points.extend([(x,y-size),(x+len(value)*size*.55,y+size)])

    def dimension(a,b,offset,layer="DIMENSIONS",show_svg=True):
        dist=math.dist(a,b)
        if dist<.001:return
        msp.add_aligned_dim(p1=a,p2=b,distance=offset,dimstyle="Standard",
            override={"dimtxt":th*.8,"dimasz":th*.6,"dimgap":th*.25,"dimexo":th*.2,"dimexe":th*.3,
                      "dimdec":2,"dimzin":8,"dimlfac":1,"dimtad":1,"dimtxsty":"THAI"},
            dxfattribs={"layer":layer}).render()
        if show_svg:
            nx=-(b[1]-a[1])/dist;ny=(b[0]-a[0])/dist
            c=(a[0]+nx*offset,a[1]+ny*offset);d=(b[0]+nx*offset,b[1]+ny*offset)
            for p,q in ((a,c),(b,d),(c,d)):
                svgs.append(f'<line x1="{p[0]}" y1="{-p[1]}" x2="{q[0]}" y2="{-q[1]}" stroke="#69856b" stroke-width="{th*.045}"/>')
            svgs.append(f'<text x="{(c[0]+d[0])/2}" y="{-(c[1]+d[1])/2-th*.25}" text-anchor="middle" font-size="{th*.85}" fill="#42634c">{dist:g}</text>')
            all_points.extend([a,b,c,d])

    text("DRAFT / PRELIMINARY - VERIFY BEFORE MANUFACTURE",0,height+gap*1.1,th*1.2)
    text(spec["title"],0,height+gap*.8,th*1.1)
    text("Units: mm | Source: black | Derived: blue | Assumed: orange | Dimensions measure geometry",0,height+gap*.5,th*.75)
    for i,(view,box) in enumerate(zip(spec["views"],boxes)):
        x0=(i%2)*(width+gap);y0=-(i//2)*(height+gap)
        tx=x0-box[0];ty=y0-box[1]
        w=box[2]-box[0];h=box[3]-box[1]
        def pt(p):return p["x"]+tx,p["y"]+ty
        text(view["name"]+" / PDF page "+",".join(map(str,view["source_pages"])),x0,y0+h+gap*.2,th)
        for e in view["elements"]:
            layer=layers[e["basis"]];color=colors[e["basis"]];kind=e["kind"]
            if kind=="polyline":
                points=[pt(p) for p in e["points"]]
                entity=msp.add_lwpolyline(points,close=e["closed"],dxfattribs={"layer":layer})
                svgpts=" ".join(f"{x},{-y}" for x,y in points)
                tag="polygon" if e["closed"] else "polyline"
                svgs.append(f'<{tag} points="{svgpts}" fill="none" stroke="{color}" stroke-width="{th*.08}"/>')
                all_points+=points
            elif kind=="circle":
                cx=e["center_x"]+tx;cy=e["center_y"]+ty;r=e["radius"]
                entity=msp.add_circle((cx,cy),r,dxfattribs={"layer":layer})
                svgs.append(f'<circle cx="{cx}" cy="{-cy}" r="{r}" fill="none" stroke="{color}" stroke-width="{th*.08}"/>')
                all_points.extend([(cx-r,cy-r),(cx+r,cy+r)])
            elif kind=="arc":
                cx=e["center_x"]+tx;cy=e["center_y"]+ty;r=e["radius"]
                entity=msp.add_arc((cx,cy),r,e["start_angle"],e["end_angle"],dxfattribs={"layer":layer})
                start=math.radians(e["start_angle"]);span=math.radians((e["end_angle"]-e["start_angle"])%360)
                points=[(cx+r*math.cos(start+span*j/80),cy+r*math.sin(start+span*j/80)) for j in range(81)]
                svgpts=" ".join(f"{x},{-y}" for x,y in points)
                svgs.append(f'<polyline points="{svgpts}" fill="none" stroke="{color}" stroke-width="{th*.08}"/>')
                all_points+=points
            elif kind=="text":
                x,y=pt(e["points"][0]);text(e["text"],x,y,e["height_mm"],layer)
                continue
            else:
                dimension(pt(e["points"][0]),pt(e["points"][1]),e["offset_mm"])
                continue
            # Human-readable source evidence stays attached as non-executable XDATA.
            if "FURNITURE_SOURCE" not in doc.appids:doc.appids.new("FURNITURE_SOURCE")
            evidence=clean(e["evidence"]).encode("utf-8")[:220].decode("utf-8", errors="ignore")
            entity.set_xdata("FURNITURE_SOURCE",[(1000,e["basis"]),(1000,evidence)])
        if w>.001:dimension((x0,y0),(x0+w,y0),-gap*.16)
        if h>.001:dimension((x0,y0+h),(x0,y0),-gap*.16)
    bottom=-(math.ceil(len(spec["views"])/2)-1)*(height+gap)-gap*.42
    text("NOT FOR PRODUCTION - UNRESOLVED ASSUMPTIONS / REVIEW NOTES",0,bottom,th)
    for i,note in enumerate((spec["notes"]+[f"{a['id']}: {a['item']} = {a['value']}" for a in spec["assumptions"]])[:18]):
        # Split long notes for readable native text entities.
        value=clean(note)
        for piece in [value[j:j+100] for j in range(0,len(value),100)]:
            bottom-=th*1.65;text(piece,0,bottom,th*.75)
    audit=doc.audit()
    if audit.has_errors:raise ValueError("DXF audit failed")
    source=folder/"cad-source";source.mkdir(exist_ok=True)
    doc.saveas(source/"drawing.dxf")
    shutil_target=folder/"drawing.dxf"
    shutil_target.write_bytes((source/"drawing.dxf").read_bytes())
    convert(converter,source,folder,"DWG","drawing.dxf")
    dwg=folder/"drawing.dwg"
    if not dwg.exists() or dwg.read_bytes()[:6]!=b"AC1032":raise ValueError("Output is not an R2018 DWG")
    check=folder/"cad-roundtrip";check.mkdir(exist_ok=True)
    convert(converter,folder,check,"DXF","drawing.dwg")
    reopened=ezdxf.readfile(check/"drawing.dxf")
    if reopened.audit().has_errors:raise ValueError("DWG roundtrip audit failed")
    from collections import Counter
    expected=Counter(e.dxftype() for e in doc.modelspace())
    actual=Counter(e.dxftype() for e in reopened.modelspace())
    if expected!=actual:raise ValueError("Entity counts changed in DWG roundtrip")
    def dimensions(d):
        return sorted(round(e.get_measurement(),4) for e in d.modelspace().query("DIMENSION"))
    if dimensions(doc)!=dimensions(reopened):raise ValueError("Dimension values changed in DWG roundtrip")
    def geometry(d):
        values=[]
        for entity in d.modelspace():
            kind=entity.dxftype()
            if kind=="LWPOLYLINE":
                values.append((kind,entity.dxf.layer,tuple((round(x,4),round(y,4)) for x,y in entity.get_points('xy')),bool(entity.closed)))
            elif kind in ("CIRCLE","ARC"):
                c=entity.dxf.center
                values.append((kind,entity.dxf.layer,round(c.x,4),round(c.y,4),round(entity.dxf.radius,4),
                               round(entity.dxf.start_angle,4) if kind=='ARC' else 0,round(entity.dxf.end_angle,4) if kind=='ARC' else 0))
        return sorted(values)
    if geometry(doc)!=geometry(reopened):raise ValueError("Geometry changed in DWG roundtrip")
    if reopened.header.get("$INSUNITS")!=4:raise ValueError("DWG units changed")
    xs,ys=zip(*all_points);pad=th*3
    minx=min(xs)-pad;maxx=max(xs)+pad;miny=min(ys)-pad;maxy=max(ys)+pad
    svg=f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{minx} {-maxy} {maxx-minx} {maxy-miny}" style="background:white;font-family:Tahoma,sans-serif">'+''.join(svgs)+'</svg>'
    (folder/"drawing.svg").write_text(svg,encoding='utf-8')
    result={"verified":True,"geometry_preserved":True,"version":"AutoCAD R2018","units":"mm","views":len(spec["views"]),
            "entity_counts":dict(expected),"dimension_values_mm":dimensions(doc),
            "dwg_sha256":hashlib.sha256(dwg.read_bytes()).hexdigest(),
            "notice":"ผ่านการแปลงกลับและตรวจโครงสร้างไฟล์ ไม่ใช่การรับรองความถูกต้องของการถอดแบบหรืออนุมัติผลิต"}
    (folder/"drawing-check.json").write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    return result


if __name__=='__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    folder=Path(sys.argv[1]);spec=json.loads((folder/'drawing-spec.json').read_text(encoding='utf-8'))
    fn=create_isometric if spec.get('mode') in ('isometric_basic','measured_views') else create_vector if spec.get('mode')=='pdf_vector' else create
    print(json.dumps(fn(spec,folder,Path(sys.argv[2])),ensure_ascii=False))

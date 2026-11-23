"""Render an embedded-vector SVG as a supplemental image; never install fonts."""
import os
import sys
from pathlib import Path

os.environ['QT_QPA_PLATFORM']='offscreen'
from PySide6.QtGui import QGuiApplication,QImage,QPainter,QFontDatabase
from PySide6.QtSvg import QSvgRenderer

app=QGuiApplication([])
font=Path(os.environ.get('WINDIR',r'C:\Windows'))/'Fonts/tahoma.ttf'
if font.exists():QFontDatabase.addApplicationFont(str(font))
renderer=QSvgRenderer(sys.argv[1])
if not renderer.isValid():raise ValueError('Invalid PDF vector preview')
size=renderer.defaultSize();w=2600;h=max(1,round(w*size.height()/size.width()))
if h>4000:w=max(1,round(w*4000/h));h=4000
img=QImage(w,h,QImage.Format_ARGB32);img.fill(0xffffffff)
p=QPainter(img);renderer.render(p);p.end()
if not img.save(sys.argv[2]):raise ValueError('Could not save reference preview')

from pathlib import Path
import re
import json
from docx import Document
from docx.shared import Cm, Pt, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK
from docx.enum.table import WD_TABLE_ALIGNMENT, WD_CELL_VERTICAL_ALIGNMENT
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).parent
OUT = ROOT.parent / 'Диплом Локальная RAG система УФК.docx'
doc = Document()
for el in doc.styles.element.iter():
    if el.tag == qn('w:rFonts'):
        for key in ['asciiTheme','hAnsiTheme','eastAsiaTheme','cstheme','csTheme']:
            el.attrib.pop(qn('w:'+key),None)
    if el.tag == qn('w:pBdr'):
        el.getparent().remove(el)
sec = doc.sections[0]
sec.page_width, sec.page_height = Cm(21), Cm(29.7)
sec.left_margin, sec.right_margin = Cm(3), Cm(1.5)
sec.top_margin = sec.bottom_margin = Cm(2)
sec.header_distance = sec.footer_distance = Cm(1)
sec.different_first_page_header_footer = True
for name in ['Normal','Title','Subtitle','Heading 1','Heading 2','Heading 3','Caption','TOC Heading']:
    s = doc.styles[name]
    s.font.name = 'Times New Roman'
    s.font.color.rgb = RGBColor(0,0,0)
    s._element.get_or_add_rPr().get_or_add_rFonts().set(qn('w:eastAsia'),'Times New Roman')
    lang = OxmlElement('w:lang'); lang.set(qn('w:val'),'ru-RU'); s._element.get_or_add_rPr().append(lang)
normal = doc.styles['Normal']
normal.font.size = Pt(14)
normal.paragraph_format.line_spacing = 1.5
normal.paragraph_format.first_line_indent = Cm(1.25)
normal.paragraph_format.space_after = Pt(0)
normal.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
normal.paragraph_format.widow_control = True
for name in ['Heading 1','Heading 2','Heading 3']:
    s = doc.styles[name]
    s.font.size = Pt(14); s.font.bold = True
    s.paragraph_format.first_line_indent = Cm(0)
    s.paragraph_format.line_spacing = 1.25
    s.paragraph_format.space_before = Pt(14)
    s.paragraph_format.space_after = Pt(8)
    s.paragraph_format.keep_with_next = True
    s.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.LEFT
doc.styles['Heading 1'].paragraph_format.page_break_before = True
doc.styles['Title'].font.size = Pt(18)
doc.styles['Title'].font.bold = True
doc.styles['Title'].paragraph_format.first_line_indent = Cm(0)
doc.styles['Title'].paragraph_format.alignment = WD_ALIGN_PARAGRAPH.CENTER
doc.styles['Caption'].font.size = Pt(12)
doc.styles['Caption'].font.italic = False
doc.styles['Caption'].font.bold = False
doc.styles['Caption'].paragraph_format.first_line_indent = Cm(0)
doc.styles['Caption'].paragraph_format.line_spacing = 1
doc.styles['Caption'].paragraph_format.space_before = Pt(6)
doc.styles['Caption'].paragraph_format.space_after = Pt(6)
for name in ['TOC 1','TOC 2']:
    if name not in doc.styles:
        doc.styles.add_style(name,1)
    s=doc.styles[name]; s.font.name='Times New Roman'; s.font.size=Pt(12)
    s.paragraph_format.first_line_indent=Cm(0); s.paragraph_format.line_spacing=1.05
    s.paragraph_format.space_after=Pt(4)
    if name=='TOC 2': s.paragraph_format.left_indent=Cm(.5)
foot=sec.footer.paragraphs[0]
foot.alignment=WD_ALIGN_PARAGRAPH.CENTER
foot.paragraph_format.first_line_indent=Cm(0)
r=foot.add_run(); r.font.size=Pt(12)
fld=OxmlElement('w:fldSimple'); fld.set(qn('w:instr'),'PAGE'); r._r.addnext(fld)

def p(text='', style=None):
    return doc.add_paragraph(text,style)

def center(text,size=14,bold=False,after=8):
    x=p(text); x.alignment=WD_ALIGN_PARAGRAPH.CENTER
    x.paragraph_format.first_line_indent=Cm(0); x.paragraph_format.space_after=Pt(after)
    for r in x.runs:r.font.size=Pt(size);r.bold=bold
    return x

center('Уральский федеральный университет',14)
center('Институт ______________________________',14)
center('Кафедра _______________________________',14)
center('Направление подготовки\nИнформационно-аналитические системы безопасности',14)
x=p(); x.paragraph_format.space_after=Pt(22)
center('ВЫПУСКНАЯ КВАЛИФИКАЦИОННАЯ РАБОТА',14,True,15)
p('Разработка локальной системы RAG\nдля поиска и формирования ответов\nпо документам Управления Федерального казначейства по Свердловской области','Title')
center('Пояснительная записка',14,False,22)
for t in ['Обучающийся ____________________________','Группа __________________________________','Руководитель _____________________________']:
    x=p(t);x.paragraph_format.first_line_indent=Cm(0);x.paragraph_format.space_after=Pt(8)
center('Екатеринбург\n2026',14,False,0)

toc_heading=doc.styles['TOC Heading']
toc_heading.font.size=Pt(14);toc_heading.font.bold=True
toc_heading.paragraph_format.page_break_before=True
toc_heading.paragraph_format.first_line_indent=Cm(0)
toc_heading.paragraph_format.space_after=Pt(10)
toc_heading.paragraph_format.alignment=WD_ALIGN_PARAGRAPH.LEFT
outline=OxmlElement('w:outlineLvl');outline.set(qn('w:val'),'9');toc_heading._element.get_or_add_pPr().append(outline)
p('Содержание','TOC Heading')
x=p();x.paragraph_format.first_line_indent=Cm(0)
begin=OxmlElement('w:fldChar');begin.set(qn('w:fldCharType'),'begin')
instr=OxmlElement('w:instrText');instr.set(qn('xml:space'),'preserve');instr.text=' TOC \\o "1-2" \\h \\z \\u '
sep=OxmlElement('w:fldChar');sep.set(qn('w:fldCharType'),'separate')
end=OxmlElement('w:fldChar');end.set(qn('w:fldCharType'),'end')
for el in [begin,instr,sep]: x.add_run()._r.append(el)
x.add_run('Содержание обновляется в Microsoft Word')
x.add_run()._r.append(end)

def table(lines):
    rows=[[c.strip() for c in line.strip().strip('|').split('|')] for line in lines]
    rows=[r for r in rows if not all(re.fullmatch(r'[-: ]+',c or '-') for c in r)]
    widths = {2:[5.3,11.2],3:[7.1,4.7,4.7],4:[5.8,3.6,3.6,3.5]}.get(len(rows[0]))
    if len(rows[0])==3 and rows[0][1]=='Количество':widths=[6.3,2.4,7.8]
    t=doc.add_table(rows=0,cols=len(rows[0]));t.alignment=WD_TABLE_ALIGNMENT.CENTER;t.autofit=False
    if widths:
        for col,w in zip(t.columns,widths):col.width=Cm(w)
    props=t._tbl.tblPr
    borders=OxmlElement('w:tblBorders')
    for edge in ['top','left','bottom','right','insideH','insideV']:
        e=OxmlElement('w:'+edge);e.set(qn('w:val'),'single');e.set(qn('w:sz'),'4');e.set(qn('w:color'),'D9D9D9');borders.append(e)
    props.append(borders)
    for i,data in enumerate(rows):
        row=t.add_row()
        pr=row._tr.get_or_add_trPr();pr.append(OxmlElement('w:cantSplit'))
        if i==0:pr.append(OxmlElement('w:tblHeader'))
        for j,(cell,txt) in enumerate(zip(row.cells,data)):
            if widths:cell.width=Cm(widths[j])
            cell.vertical_alignment=WD_CELL_VERTICAL_ALIGNMENT.CENTER
            cp=cell._tc.get_or_add_tcPr();mar=OxmlElement('w:tcMar')
            for edge in ['top','bottom','left','right']:
                e=OxmlElement('w:'+edge);e.set(qn('w:w'),'85');e.set(qn('w:type'),'dxa');mar.append(e)
            cp.append(mar)
            if i==0:
                sh=OxmlElement('w:shd');sh.set(qn('w:fill'),'E8E8E8');cp.append(sh)
            x=cell.paragraphs[0];x.paragraph_format.first_line_indent=Cm(0)
            x.paragraph_format.line_spacing=1;x.paragraph_format.space_after=Pt(2)
            x.paragraph_format.keep_with_next=(i==0)
            x.alignment=WD_ALIGN_PARAGRAPH.LEFT
            if len(txt)<18 and j>0:x.alignment=WD_ALIGN_PARAGRAPH.CENTER
            r=x.add_run(txt);r.font.size=Pt(11);r.bold=(i==0)
    x=p();x.paragraph_format.line_spacing=1;x.paragraph_format.space_after=Pt(3)
    x.paragraph_format.first_line_indent=Cm(0);x.add_run().font.size=Pt(3)

def equation(text):
    x=p();x.alignment=WD_ALIGN_PARAGRAPH.CENTER;x.paragraph_format.first_line_indent=Cm(0)
    x.paragraph_format.line_spacing=1.15;x.paragraph_format.space_before=Pt(6);x.paragraph_format.space_after=Pt(6)
    def run(text):
        r=OxmlElement('m:r');mt=OxmlElement('m:t');mt.text=text;r.append(mt);return r
    def wrap(tag,*items):
        e=OxmlElement('m:'+tag)
        for it in items:e.append(run(it) if isinstance(it,str) else it)
        return e
    def frac(a,b):return wrap('f',wrap('num',a),wrap('den',b))
    def sub(a,b):return wrap('sSub',wrap('e',a),wrap('sub',b))
    def sup(a,b):return wrap('sSup',wrap('e',a),wrap('sup',b))
    def summation(lower,upper,body):
        props=wrap('naryPr');ch=OxmlElement('m:chr');ch.set(qn('m:val'),'∑');props.append(ch)
        loc=OxmlElement('m:limLoc');loc.set(qn('m:val'),'subSup');props.append(loc)
        return wrap('nary',props,wrap('sub',lower),wrap('sup',upper),wrap('e',body))
    math=OxmlElement('m:oMath')
    if text.startswith('Precision'):
        items=['Precision@k(q) = ',frac('|Dₖ(q) ∩ R(q)|','k')]
    elif text.startswith('Recall'):
        items=['Recall@k(q) = ',frac('|Dₖ(q) ∩ R(q)|','|R(q)|')]
    elif text.startswith('RRF'):
        den=wrap('box',wrap('e','60 + ',sub('rank','m'),'(d)'))
        items=['RRF(d) = ',summation('m ∈ M(d)','',frac('1',den))]
    elif text.startswith('MRR'):
        items=['MRR@k = ',frac('1','N'),summation('j = 1','N',frac('1',wrap('box',wrap('e',sub('rank','first'),'(qⱼ)'))))]
    elif text.startswith('DCG'):
        num=wrap('box',wrap('e',sup('2',sub('rel','i')),' − 1'))
        den=wrap('box',wrap('e',sub('log','2'),'(i + 1)'))
        items=['DCG@k = ',summation('i = 1','k',frac(num,den))]
    elif text.startswith('nDCG'):items=['nDCG@k = ',frac('DCG@k','IDCG@k')]
    else:items=[text]
    for item in items:math.append(run(item) if isinstance(item,str) else item)
    x._p.append(math)

def diagram():
    im=Image.new('RGB',(1800,1260),'white');dr=ImageDraw.Draw(im)
    font=ImageFont.truetype('C:/Windows/Fonts/times.ttf',37)
    bold=ImageFont.truetype('C:/Windows/Fonts/timesbd.ttf',39)
    def box(rect,text,b=False):
        dr.rounded_rectangle(rect,radius=12,fill='#F4F4F4',outline='black',width=3)
        f=bold if b else font
        bb=dr.multiline_textbbox((0,0),text,font=f,spacing=10,align='center')
        x=(rect[0]+rect[2]-(bb[2]-bb[0]))/2;y=(rect[1]+rect[3]-(bb[3]-bb[1]))/2-bb[1]
        dr.multiline_text((x,y),text,font=f,fill='black',spacing=10,align='center')
    def arrow(a,b):
        dr.line([a,b],fill='black',width=4)
        import math
        angle=math.atan2(b[1]-a[1],b[0]-a[0]);l=20
        pts=[b,(b[0]-l*math.cos(angle-.5),b[1]-l*math.sin(angle-.5)),(b[0]-l*math.cos(angle+.5),b[1]-l*math.sin(angle+.5))]
        dr.polygon(pts,fill='black')
    box((35,35,635,150),'Подготовка базы знаний',True)
    box((1015,35,1765,150),'Обработка вопроса',True)
    left=[('Загрузка текстового PDF',(35,205,635,325)),('Извлечение текста и структуры\nРазбиение на фрагменты',(35,380,635,525)),('E5  →  векторы фрагментов\nSQLite  →  текст и метаданные',(35,580,635,725)),('Публикация документа\nСтатус готов и назначенные права',(35,790,635,950))]
    right=[('Вход и проверка сессии\nВопрос пользователя',(1015,205,1765,325)),('Отбор разрешённых документов\nACL  ·  уровень  ·  группа  ·  статус',(1015,380,1765,525)),('FAISS и BM25\nОбъединение рейтингов RRF',(1015,580,1765,725)),('Контекст  →  локальная Qwen3\nПовторная проверка прав',(1015,790,1765,950))]
    for txt,rect in left+right:box(rect,txt)
    for x in [335,1390]:
        for y1,y2 in [(150,205),(325,380),(525,580),(725,790)]:arrow((x,y1),(x,y2))
    arrow((635,868),(780,868));arrow((780,868),(780,452));arrow((780,452),(1015,452))
    box((700,1050,1765,1215),'Ответ и ссылки на источники\nДокумент  ·  страница PDF  ·  исходный текст',True)
    arrow((1390,950),(1390,1050))
    path=ROOT/'architecture.png';im.save(path)
    x=p();x.alignment=WD_ALIGN_PARAGRAPH.CENTER;x.paragraph_format.first_line_indent=Cm(0)
    x.paragraph_format.keep_with_next=True
    run=x.add_run();run.add_picture(str(path),width=Cm(16.3))
    inline=run._r.xpath('.//wp:docPr')[0];inline.set('descr','Две ветви архитектуры RAG: загрузка и индексация PDF; проверка доступа, гибридный поиск и локальная генерация ответа.')

lines=(ROOT/'content.md').read_text(encoding='utf-8').splitlines()
i=0
while i<len(lines):
    line=lines[i].strip();i+=1
    if not line:continue
    if line.startswith('|'):
        block=[line]
        while i<len(lines) and lines[i].strip().startswith('|'):block.append(lines[i].strip());i+=1
        table(block)
    elif line=='@diagram':diagram()
    elif line.startswith('@eq '):equation(line[4:])
    elif line.startswith('@caption '):
        x=p(line[9:],'Caption');x.paragraph_format.keep_with_next=line[9:].startswith('Таблица')
    elif line.startswith('@code '):
        x=p(line[6:]);x.paragraph_format.first_line_indent=Cm(0);x.paragraph_format.line_spacing=1
        x.paragraph_format.space_before=Pt(5);x.paragraph_format.space_after=Pt(5)
        for r in x.runs:r.font.name='Consolas';r.font.size=Pt(10)
    elif line.startswith('# '):p(line[2:],'Heading 1')
    elif line.startswith('## '):p(line[3:],'Heading 2')
    elif line.startswith('### '):p(line[4:],'Heading 3')
    else:p(line)

doc.core_properties.title='Разработка локальной RAG системы по документам УФК по Свердловской области'
doc.core_properties.subject='Выпускная квалификационная работа и доклад для защиты'
doc.core_properties.author=''
doc.core_properties.keywords='RAG, УФК, FAISS, BM25, Qwen3, управление доступом'
doc.core_properties.comments=''
for paragraph in doc.paragraphs:
    if paragraph.style.name in ['Title','Heading 1','Heading 2','TOC Heading']:
        for run in paragraph.runs:
            run.font.name='Times New Roman'
            fonts=run._r.get_or_add_rPr().get_or_add_rFonts()
            fonts.set(qn('w:ascii'),'Times New Roman');fonts.set(qn('w:hAnsi'),'Times New Roman')
            fonts.set(qn('w:eastAsia'),'Times New Roman');fonts.set(qn('w:cs'),'Times New Roman')
            if paragraph.style.name=='Title':run.font.size=Pt(18)
doc.save(OUT)
print(json.dumps({'output':str(OUT),'paragraphs':len(doc.paragraphs),'tables':len(doc.tables)},ensure_ascii=False))

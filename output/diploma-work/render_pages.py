from pathlib import Path
import json
from pypdf import PdfReader
from pdf2image import convert_from_path

root=Path(__file__).parent
pdf=root/'diploma-qa.pdf'
out=root/'pages';out.mkdir(exist_ok=True)
poppler=r'C:\Users\Pk_sn\.cache\codex-runtimes\codex-primary-runtime\dependencies\native\poppler\Library\bin'
paths=convert_from_path(str(pdf),dpi=115,fmt='png',thread_count=4,output_folder=str(out),paths_only=True,poppler_path=poppler)
for i,path in enumerate(paths,1):
    Path(path).replace(out/f'page-{i}.png')
reader=PdfReader(str(pdf))
summary=[]
for i,page in enumerate(reader.pages,1):
    txt=(page.extract_text() or '').encode('utf-16','surrogatepass').decode('utf-16','replace')
    summary.append({'page':i,'chars':len(txt),'start':txt[:170],'end':txt[-180:]})
(root/'pdf-text-summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'pages':len(paths),'sparse':[x for x in summary if x['chars']<650]},ensure_ascii=False))

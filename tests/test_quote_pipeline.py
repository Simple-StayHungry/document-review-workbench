"""The quote fix must run in the real analyze/export order, before freezing."""
import copy
import tempfile
import zipfile
import unittest
from pathlib import Path
from workbench.docxio import w,text,resolve_revisions,structural_digest_content
from workbench.engine import Engine
from test_engine_boundaries import COMPANY,FACT,make_docx
from test_copy_retention import rewrite_document,read_root,paragraphs
from test_source_coverage import paragraph

OPENER='2.2.4发生下列事项之一，需要决定或授权采取相应措施（包括但不限于与发行人等相关方进行协商谈判，提起、参与仲裁或诉讼程序，处置担保物或者其他有利于投资者权益保护的措施等）的：'
TAILS=['a.发行人已经或预计不能按期支付本次债券的本金或者利息；',
       'b.发行人发生减资、合并、分立、被责令停产停业、被暂扣或者吊销许可证、被托管、解散、申请破产或者依法进入破产程序的；',
       'c.发生其他对债券持有人权益有重大不利影响的事项。']
class QuotedPipelineTests(unittest.TestCase):
 def test_actual_analysis_and_export_recover_quote_opener(self):
  with tempfile.TemporaryDirectory() as tmp:
   base=Path(tmp);files=[make_docx(base/'底稿.docx'),make_docx(base/'当前募集说明书.docx',source=True)]
   for f in files:
    def edit(body,f=f):
     anchor=next(p for p in body if p.tag==w('p') and text(p)==FACT);at=list(body).index(anchor)+1
     vals=[('“' if f['role']=='target' else '')+OPENER]+TAILS[:-1]+[TAILS[-1]+('”' if f['role']=='target' else '')]
     for i,v in enumerate(vals):body.insert(at+i,paragraph(v,bold=f['role']=='source'))
    rewrite_document(f,edit)
   engine=Engine();result=engine.analyze(files,{'author':'柒','issuer':COMPANY})
   p=next(p for p in result['proposals'] if p['old_text']=='“'+OPENER)
   self.assertEqual('accept',p['decision'],p)
   out=base/'out';ex=engine.export(result,files,out);root=read_root(out/ex['checks'][0]['file'])
   with zipfile.ZipFile(ex['zip']) as archive:
    names=archive.namelist()
   self.assertTrue(names)
   self.assertTrue(all(name.lower().endswith('.docx') and '/' not in name for name in names), names)
   inserted=paragraphs(root,OPENER);self.assertEqual(1,len(inserted));self.assertIsNotNone(inserted[0].find('.//'+w('ins')));self.assertIsNone(inserted[0].find('.//'+w('b')))  # wording from source, inline emphasis from target workpaper
   accepted=copy.deepcopy(root);resolve_revisions(accepted,True)
   self.assertNotIn('“'+OPENER,text(accepted));self.assertNotIn(TAILS[-1]+'”',text(accepted))
   resolve_revisions(root,False);self.assertEqual(structural_digest_content(read_root(files[0]['path'])),structural_digest_content(root))
 def test_real_fifth_chapter_opener_is_not_lost_before_retention_classification(self):
  from workbench.model import Document
  fixtures=Path(__file__).resolve().parents[2]/'核查工作台_Codex交接包/02_示例项目A'
  if not fixtures.exists():self.skipTest('external real fixtures absent')
  paths=[next((fixtures/'02_原始底稿').glob('*第五章.docx')),next((fixtures/'01_正式来源').glob('2 *.docx'))]
  files=[]
  for role,path in zip(('target','source'),paths):
   d=Document(path);files.append({'id':d.hash,'name':d.name,'path':str(path),'role':role,'profile':d.profile,'origin':d.name,'aliases':[]})
  engine=Engine();result=engine.analyze(files,{'author':'柒','issuer':files[1]['profile']['issuer']})
  p=next(p for p in result['proposals'] if p['start']==34)
  self.assertEqual('accept',p['decision'],p)
  self.assertEqual([1351],p['candidates'][0]['indices'])
  self.assertFalse(p['candidates'][0].get('span'))
  self.assertFalse(p['candidates'][0].get('projection'))
  source=engine.load(files[1]);self.assertEqual(source.blocks[1351].text,p['candidates'][0]['text'])
  with tempfile.TemporaryDirectory() as tmp:
   ex=engine.export(result,files,Path(tmp));root=read_root(Path(tmp)/ex['checks'][0]['file'])
   value=p['candidates'][0]['text'];inserted=paragraphs(root,value)
   self.assertEqual(1,len(inserted));self.assertIsNotNone(inserted[0].find('.//'+w('ins')))
   self.assertTrue(ex['checks'][0]['independent_output_audit']['passed'])
if __name__=='__main__':unittest.main()

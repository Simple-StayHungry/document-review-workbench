import copy,tempfile,unittest
from pathlib import Path
from xml.etree import ElementTree as E
from workbench.wording import adapt,POLICY
from workbench.docxio import Package,w,text
from workbench.output_audit import audit_docx,element_paths,revision_ids
from tests.test_output_audit import write_doc,para,table

class WordingTests(unittest.TestCase):
    def test_split_runs_and_table_cells(self):
        p=para('截至本募集说明');E.SubElement(E.SubElement(p,w('r')),w('t')).text='书签署日，本公司经营正常。详见本募集说明书第五节。'
        t=table('本公司2026年');new=[p,t];changes=adapt(new,'prospectus')
        self.assertEqual(text(p),'截至本核查分析文件出具日，公司经营正常。详见募集说明书第五节。')
        self.assertIn('公司2026年',text(t));self.assertNotIn('本公司',text(t));self.assertEqual(2,len(changes))
    def test_opinion_broker_voice_and_explicit_issuer_quote(self):
        ps=[para('本公司经核查认为，截至本核查意见出具日发行人符合要求。'),para('发行人全体董事已声明：“本公司承诺本募集说明书真实。”')]
        adapt(ps,'opinion')
        self.assertEqual(text(ps[0]),'本公司经核查认为，截至本核查分析文件出具日发行人符合要求。')
        self.assertEqual(text(ps[1]),'发行人全体董事已声明：“公司承诺募集说明书真实。”')
    def test_independent_audit_authorized_transform_and_tamper(self):
        with tempfile.TemporaryDirectory() as td:
            d=Path(td);old=d/'old.docx';src=d/'src.docx';out=d/'out.docx'
            write_doc(old,[para('原稿')]);write_doc(src,[para('截至本募集说明书签署日，本公司运行正常。'),table('本公司')])
            pkg=Package(old);source=Package(src);original=list(pkg.body)[:1];raw=list(source.body)[:-1]
            tp=element_paths(pkg.root,original);sp=element_paths(source.root,raw);new=copy.deepcopy(raw);changes=adapt(new,'prospectus')
            ins=pkg.replace(original,new,source=source,whole=True);pkg.save(out)
            rec={'id':'copy','action':'source_copy','source_file':str(src),'source_sha256':source.hash,'source_role':'prospectus','source_kind':'prospectus','source_paths':sp,'target_paths':tp,'deletion_paths':element_paths(pkg.root,original),'output_paths':element_paths(pkg.root,ins),'insertion_revision_ids':revision_ids(ins,'ins'),'deletion_revision_ids':revision_ids(original,'del'),'wording_policy':POLICY,'wording_changes':changes}
            report=audit_docx(out,old,[rec]);self.assertTrue(report['passed'],report['errors'])
            rec['wording_changes'][0]['after']='被伪造';self.assertFalse(audit_docx(out,old,[rec])['passed'])
    def test_pure_source_addition_audits_position_and_reject(self):
        with tempfile.TemporaryDirectory() as td:
            d=Path(td);old=d/'old.docx';src=d/'src.docx';out=d/'out.docx'
            write_doc(old,[para('同科目原文'),para('下一个原标题')]);write_doc(src,[para('同科目新增来源正文')])
            pkg=Package(old);source=Package(src);original=list(pkg.body)[:2];bp=element_paths(pkg.root,original);raw=list(source.body)[:1]
            ins=pkg.insert_source(original[1],raw,before=True,source=source);pkg.save(out)
            rec={'id':'add','action':'source_insert','source_file':str(src),'source_sha256':source.hash,'source_role':'prospectus','source_paths':element_paths(source.root,raw),'target_paths':[],'deletion_paths':[],'output_paths':element_paths(pkg.root,ins),'insertion_revision_ids':revision_ids(ins,'ins'),'deletion_revision_ids':[],'insertion_boundary':{side:{'baseline_path':bp[i],'output_path':element_paths(pkg.root,[original[i]])[0]} for side,i in [('after',0),('before',1)]}}
            report=audit_docx(out,old,[rec]);self.assertTrue(report['passed'],report['errors']);self.assertEqual(1,report['source_insert_count'])
            rec['insertion_boundary']['before']['output_path']=element_paths(pkg.root,[original[0]])[0]
            self.assertFalse(audit_docx(out,old,[rec])['passed'])

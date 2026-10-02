"""Source additions need a real body gap, stable topic and preserved outline."""
import copy
import tempfile
import unittest
from pathlib import Path
from xml.etree import ElementTree as E
from workbench.docxio import w,text
from workbench.model import Document
from workbench.source_additions import recover_source_additions
from test_engine_boundaries import COMPANY,make_docx
from test_copy_retention import rewrite_document
from test_source_coverage import paragraph

HEADING='（一）食品销售业务收入确认方法'
LEFT='发行人的食品销售业务按照合同约定进行商品采购，在取得商品控制权后向客户销售并依法确认收入。'
RIGHT='发行人将继续完善食品销售业务的管理制度，加强应收账款催收和合同管理，保持业务稳健发展。'
NEW='发行人新增月度存货盘点和供应商准入复核程序，由财务部门与业务部门共同执行并保存完整记录。'


class SourceAdditionTests(unittest.TestCase):
    def setUp(self):
        t=tempfile.TemporaryDirectory();self.addCleanup(t.cleanup);self.base=Path(t.name)

    def doc(self,name,content,source=False,heading=HEADING):
        f=make_docx(self.base/name,source=source)
        def rewrite(body):
            sect=body.find(w('sectPr'));body[:]=[]
            for val in (COMPANY,'2026年公司债券','募集说明书' if source else '核查记录及分析文件'):
                body.append(paragraph(val))
            for val in (['第一章 发行人基本情况'] if source else ['1-1-1 发行人基本情况核查记录','二、核查情况']):body.append(paragraph(val))
            body.append(paragraph(heading))
            for val in content:body.append(copy.deepcopy(val) if hasattr(val,'tag') else paragraph(val))
            body.append(paragraph('（二）其他事项'))
            body.append(paragraph('其他事项保持原样，并按照对应材料另行进行核对。'))
            if sect is not None:
                if sect.find(w('pgMar')) is None:E.SubElement(sect,w('pgMar'),{w(k):'1440' for k in ('left','right','top','bottom')})
                body.append(sect)
        rewrite_document(f,rewrite)
        return Document(f['path'])

    def prop(self,td,sd,value):
        b=next(b for b in td.blocks if b.text==value);s=next(b for b in sd.blocks if b.text==value)
        return {'id':td.hash+':'+str(b.index),'target_id':td.hash,'target_name':td.name,
                'start':b.index,'end':b.index+1,'matter':b.matter or '正文','old_text':value,'kind':'material',
                'decision':'accept','selected':0,'candidates':[{'doc_hash':sd.hash,'source_name':sd.name,
                'start':s.index,'end':s.index+1,'indices':[s.index],'text':value,'score':1.,'exact':True}]}

    def run_case(self,target,source,values=(LEFT,RIGHT),**kwargs):
        td=self.doc('原稿.docx',target);sd=self.doc('当前募集说明书.docx',source,source=True)
        ps=[self.prop(td,sd,v) for v in values]
        out,notes=recover_source_additions(td,[sd],ps,**kwargs)
        return td,sd,ps,[p for p in out if p.get('action')=='source_insert'],notes

    def test_middle_addition_is_real_zero_width_source_range(self):
        td,sd,ps,added,notes=self.run_case([LEFT,RIGHT],[LEFT,NEW,RIGHT])
        self.assertEqual(1,len(added));p=added[0]
        self.assertEqual(ps[1]['start'],p['start']);self.assertEqual(p['start'],p['end'])
        self.assertEqual(NEW,p['candidates'][0]['text']);self.assertEqual('two-adjacent-source-anchors',p['candidates'][0]['correspondence']['method'])
        self.assertTrue(p['insert_before_fingerprint']);self.assertTrue(p['insert_after_fingerprint'])
        self.assertEqual({q['id'] for q in ps},set(p['anchor_sources']))
        for q in ps:
            self.assertEqual(sd.hash,p['anchor_sources'][q['id']]['doc_hash'])
            self.assertEqual(q['candidates'][0]['indices'],p['anchor_sources'][q['id']]['indices'])
        self.assertEqual('added',notes[0]['status'])

    def test_leaf_prefix_and_suffix_are_added_inside_original_heading(self):
        td,sd,ps,added,notes=self.run_case([LEFT],[NEW,LEFT,RIGHT],values=(LEFT,))
        self.assertEqual(2,len(added));self.assertEqual({NEW,RIGHT},{p['candidates'][0]['text'] for p in added})
        self.assertEqual({ps[0]['start'],ps[0]['end']},{p['start'] for p in added})
        self.assertTrue(all(not sd.blocks[i].heading for p in added for i in p['candidates'][0]['indices']))

    def test_heading_change_cannot_move_new_text_across_topics(self):
        td=self.doc('原稿.docx',[LEFT,RIGHT]);sd=self.doc('募集说明书.docx',[LEFT,NEW,RIGHT],True,heading='（一）电力销售业务收入确认方法')
        ps=[self.prop(td,sd,v) for v in (LEFT,RIGHT)]
        out,notes=recover_source_additions(td,[sd],ps)
        self.assertEqual(ps,out);self.assertFalse(notes)

    def test_source_subheading_and_new_topic_are_not_inserted(self):
        td,sd,ps,added,notes=self.run_case([LEFT,RIGHT],[LEFT,'（二）银行融资管理安排',NEW,RIGHT])
        self.assertEqual([],added)

    def test_uncopied_independent_target_judgment_blocks_leaf_extension(self):
        judgment='项目组经查阅和访谈后认为相关核查证据充分，未发现需要进一步报告的异常事项。'
        td,sd,ps,added,notes=self.run_case([LEFT,judgment],[LEFT,NEW],values=(LEFT,))
        self.assertEqual([],added)

    def test_same_target_topic_text_is_not_duplicated(self):
        td,sd,ps,added,notes=self.run_case([NEW,LEFT,RIGHT],[LEFT,NEW,RIGHT])
        self.assertEqual([],added);self.assertIn('相同正文',notes[0]['reason'])

    def test_source_warning_retains_gap_with_specific_reason(self):
        td=self.doc('原稿.docx',[LEFT,RIGHT]);sd=self.doc('当前募集说明书.docx',[LEFT,NEW,RIGHT],True)
        ps=[self.prop(td,sd,v) for v in (LEFT,RIGHT)];i=next(b.index for b in sd.blocks if b.text==NEW)
        out,notes=recover_source_additions(td,[sd],ps,{(sd.hash,i):['来源数据冲突']})
        self.assertFalse(any(p.get('action')=='source_insert' for p in out));self.assertIn('来源数据冲突',notes[0]['reason'])

    def test_formal_opinion_is_eligible_but_other_issuer_is_not(self):
        td=self.doc('原稿.docx',[LEFT,RIGHT]);sd=self.doc('当前核查意见.docx',[LEFT,NEW,RIGHT],True)
        sd.profile['kind']='opinion';ps=[self.prop(td,sd,v) for v in (LEFT,RIGHT)]
        out,_=recover_source_additions(td,[sd],ps)
        self.assertEqual(1,sum(p.get('action')=='source_insert' for p in out))
        sd.profile['issuer']='另一家公司';out,_=recover_source_additions(td,[sd],ps)
        self.assertFalse(any(p.get('action')=='source_insert' for p in out))

    def test_existing_insert_plan_makes_second_recovery_idempotent(self):
        td,sd,ps,added,notes=self.run_case([LEFT,RIGHT],[LEFT,NEW,RIGHT])
        out,_=recover_source_additions(td,[sd],ps+added)
        self.assertEqual(1,sum(p.get('action')=='source_insert' for p in out))

    def test_complete_source_table_can_be_added(self):
        tbl=E.Element(w('tbl'));tr=E.SubElement(tbl,w('tr'))
        for value in ('事项','新增完整来源表'):
            tc=E.SubElement(tr,w('tc'));tc.append(paragraph(value))
        td,sd,ps,added,notes=self.run_case([LEFT,RIGHT],[LEFT,tbl,RIGHT])
        self.assertEqual(1,len(added));self.assertTrue(added[0]['has_table'])
        self.assertEqual('table',sd.blocks[added[0]['candidates'][0]['start']].kind)

    def table_prefix_case(self,missing_anchor=False):
        before='发行人的投资收益来源已经按照会计准则进行完整披露，相关业务情况和本期变化原因详见上述投资收益分析。'
        table=E.Element(w('tbl'));tr=E.SubElement(table,w('tr'))
        for value in ('产生投资收益的来源','新增完整来源表'):
            tc=E.SubElement(tr,w('tc'));tc.append(paragraph(value))
        td=self.doc('原稿.docx',['单位：万元',before,LEFT,RIGHT],heading='（一）非经常性损益分析')
        sd=self.doc('募集说明书.docx',[before,'（二）食品销售业务收入确认方法','表：食品销售统计表','单位：万元',table,LEFT,RIGHT],True,heading='（一）投资收益分析')
        ps=[self.prop(td,sd,v) for v in (before,LEFT,RIGHT) if not (missing_anchor and v==RIGHT)]
        return td,sd,ps,recover_source_additions(td,[sd],ps)

    def test_missing_source_subheading_does_not_block_proved_leading_table(self):
        td,sd,ps,(out,notes)=self.table_prefix_case()
        added=[p for p in out if p.get('action')=='source_insert']
        self.assertEqual(1,len(added));p=added[0]
        self.assertEqual('complete-leaf-table-prefix-three-anchors',p['candidates'][0]['correspondence']['method'])
        self.assertEqual(3,len(p['anchor_sources']))
        self.assertTrue(p['has_table']);self.assertTrue(all(not sd.blocks[i].heading for i in p['candidates'][0]['indices']))

    def test_leading_table_without_two_body_anchors_is_not_added(self):
        td,sd,ps,(out,notes)=self.table_prefix_case(missing_anchor=True)
        self.assertFalse(any(p.get('action')=='source_insert' for p in out))

    def test_engine_copies_new_table_at_anchor_and_reject_restores_outline(self):
        from workbench.engine import Engine
        from workbench.docxio import resolve_revisions,structural_digest_content
        from test_copy_retention import read_root
        before='发行人的投资收益来源已经按照会计准则进行完整披露，相关业务情况和本期变化原因详见上述投资收益分析。'
        table=E.Element(w('tbl'));pr=E.SubElement(table,w('tblPr'))
        E.SubElement(pr,w('tblW'),{w('w'):'6000',w('type'):'dxa'})
        grid=E.SubElement(table,w('tblGrid'))
        for _ in range(2):E.SubElement(grid,w('gridCol'),{w('w'):'3000'})
        for values in (('产生投资收益的来源','金额'),('食品业务新增收益','12.50')):
            tr=E.SubElement(table,w('tr'))
            for value in values:
                tc=E.SubElement(tr,w('tc'));tcp=E.SubElement(tc,w('tcPr'))
                E.SubElement(tcp,w('tcW'),{w('w'):'3000',w('type'):'dxa'});tc.append(paragraph(value))
        td=self.doc('原稿.docx',[before,LEFT,RIGHT],heading='二、核查情况')
        sd=self.doc('募集说明书.docx',[before,'（二）食品销售业务收入确认方法','表：食品销售统计表','单位：万元',table,LEFT,RIGHT],True,heading='（一）投资收益分析')
        files=[{'id':d.hash,'name':d.name,'path':str(d.path),'role':role,'profile':d.profile,'origin':d.name,'aliases':[]}
               for d,role in ((td,'target'),(sd,'source'))]
        engine=Engine();result=engine.analyze(files,{'author':'柒','issuer':COMPANY})
        added=[p for p in result['proposals'] if p.get('action')=='source_insert']
        self.assertEqual(1,len(added));self.assertEqual(added[0]['start'],added[0]['end'])
        self.assertEqual('accept',added[0]['decision'],added[0])
        out=self.base/'new-table';export=engine.export(result,files,out)
        check=export['checks'][0];self.assertTrue(check['independent_output_audit']['passed'])
        self.assertTrue(check['independent_output_audit']['reject_restores_baseline'])
        root=read_root(out/check['file']);accepted=copy.deepcopy(root);resolve_revisions(accepted,True)
        items=list(accepted.find(w('body')));texts=[text(e) for e in items]
        anchor=texts.index(LEFT)
        self.assertEqual(['表：食品销售统计表','单位：万元',text(table)],texts[anchor-3:anchor],texts)
        self.assertFalse(any(text(e)=='（二）食品销售业务收入确认方法' for e in items))
        old_titles=[b.text for b in td.blocks if b.heading]
        new_titles=[b.text for b in Document(out/check['file']).blocks if b.heading]
        self.assertEqual(old_titles,new_titles)
        resolve_revisions(root,False)
        self.assertEqual(structural_digest_content(read_root(td.path)),structural_digest_content(root))

    def test_complete_scope_conflict_uses_prospectus_not_exact_old_opinion(self):
        from workbench.matching import Index
        from workbench.scopes import reconcile
        td=self.doc('原稿.docx',[LEFT,RIGHT])
        prospectus=self.doc('募集说明书.docx',[LEFT,NEW],True)
        opinion=self.doc('核查意见.docx',[LEFT,RIGHT],True);opinion.profile['kind']='opinion'
        left=self.prop(td,opinion,LEFT);other=self.prop(td,prospectus,LEFT)['candidates'][0]
        left['candidates'].append(other)
        right=self.prop(td,opinion,RIGHT)
        for p in (left,right):p.update(has_table=False,heading=HEADING,status='auto')
        out,_=reconcile(td,Index([prospectus,opinion]),[left,right])
        scope=next(p for p in out if p.get('scope_mode'))
        self.assertEqual(prospectus.hash,scope['candidates'][scope['selected']]['doc_hash'])
        self.assertEqual(prospectus.hash,scope['source_precedence']['preferred'])
        self.assertIn(NEW,scope['candidates'][scope['selected']]['text'])

    def test_mixed_equivalent_sources_are_not_reported_as_missing_body(self):
        from workbench.matching import Index
        from workbench.scopes import source_gaps
        td=self.doc('原稿.docx',[LEFT,NEW,RIGHT])
        prospectus=self.doc('募集说明书.docx',[LEFT,NEW,RIGHT],True)
        opinion=self.doc('核查意见.docx',[LEFT,NEW,RIGHT],True,heading='（二）食品销售业务收入确认方法')
        opinion.profile['kind']='opinion'
        ps=[self.prop(td,prospectus,LEFT),self.prop(td,prospectus,NEW),self.prop(td,opinion,RIGHT)]
        gaps=source_gaps(td,Index([prospectus,opinion]),ps)
        self.assertEqual([],gaps)


if __name__=='__main__':unittest.main()

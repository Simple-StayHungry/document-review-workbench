"""Reverse coverage must recover source content without opening judgment guards."""
import copy
import os
import tempfile
import unittest
from pathlib import Path
from xml.etree import ElementTree as E
from workbench.docxio import w,text,resolve_revisions,structural_digest_content
from workbench.engine import Engine
from workbench.model import Document
from test_engine_boundaries import COMPANY,FACT,make_docx
from test_copy_retention import rewrite_document,read_root,paragraphs

SUMMARY='综上，发行人的食品经营业务具有完善的存货管理程序，发行人在取得商品控制权后依据合同向客户提供商品，并按照企业会计准则确认营业收入。'
JUDGMENT='综上，项目组经核查认为相关事项均已按照核查计划执行，相关证据与原始文件一致。'

def paragraph(value,bold=False):
    p=E.Element(w('p'));r=E.SubElement(p,w('r'))
    if bold:E.SubElement(E.SubElement(r,w('rPr')),w('b'))
    E.SubElement(r,w('t')).text=value
    return p

class SourceCoverageTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup);self.base=Path(tmp.name)
        self.files=[make_docx(self.base/'原始.docx'),make_docx(self.base/'当前募集说明书.docx',source=True)]
        self.params={'author':'柒','issuer':COMPANY}
    def seed(self,value=SUMMARY,source_heading=None,conclusion=False):
        for f in self.files:
            def edit(body,f=f):
                anchor=next(p for p in body if p.tag==w('p') and text(p)==FACT)
                at=list(body).index(anchor)+1
                if conclusion and f['role']=='target':
                    anchor=next(p for p in body if p.tag==w('p') and text(p)=='三、核查结论');at=list(body).index(anchor)+1
                if source_heading and f['role']=='source':body.insert(at,paragraph(source_heading));at+=1
                body.insert(at,paragraph(value,f['role']=='source'))
            rewrite_document(f,edit)
    def analyze(self):
        engine=Engine();result=engine.analyze(self.files,self.params);return engine,result
    def test_identical_prospectus_summary_is_actually_copied_and_reject_restores(self):
        self.seed();engine,result=self.analyze()
        p=next(p for p in result['proposals'] if p['old_text']==SUMMARY)
        self.assertEqual('accept',p['decision']);self.assertEqual('exact-object-adjacent-source-copy',p['candidates'][0]['correspondence']['method'])
        out=self.base/'out';export=engine.export(result,self.files,out);root=read_root(out/export['checks'][0]['file'])
        ps=paragraphs(root,SUMMARY);self.assertEqual(1,len(ps));self.assertIsNotNone(ps[0].find('.//'+w('ins')));self.assertIsNone(ps[0].find('.//'+w('b')))
        self.assertTrue(any(SUMMARY==''.join(t.text or '' for t in d.iter(w('delText'))) for d in root.iter(w('del'))))
        resolve_revisions(root,False)
        self.assertEqual(structural_digest_content(read_root(self.files[0]['path'])),structural_digest_content(root))
    def test_exact_project_judgment_still_protected(self):
        self.seed(JUDGMENT);_,result=self.analyze();self.assertFalse(any(p['old_text']==JUDGMENT and p['decision']=='accept' for p in result['proposals']))
    def test_exact_conclusion_section_still_protected(self):
        self.seed(conclusion=True);_,result=self.analyze();self.assertFalse(any(p['old_text']==SUMMARY and p['decision']=='accept' for p in result['proposals']))
    def test_exact_summary_in_another_business_cannot_unlock(self):
        self.seed(source_heading='（五）其他公司的经营情况');_,result=self.analyze();self.assertFalse(any(p['old_text']==SUMMARY and p['decision']=='accept' for p in result['proposals']))
    def test_identical_heading_keeps_original_format_while_body_is_copied(self):
        self.seed()
        heading='（一）食品销售业务收入确认方法'
        for f in self.files:
            def edit(body,f=f):
                old=next(p for p in body if p.tag==w('p') and text(p)=='（一）主要业务情况')
                body[list(body).index(old)]=paragraph(heading,f['role']=='source')
            rewrite_document(f,edit)
        engine,result=self.analyze()
        copied=[p for p in result['proposals'] if ':coverage:' in p['id'] and p['decision']=='accept']
        self.assertFalse(any(p['old_text']==heading for p in copied))
        out=self.base/'heading-retained';export=engine.export(result,self.files,out)
        root=read_root(out/export['checks'][0]['file']);kept=paragraphs(root,heading)
        self.assertEqual(1,len(kept));self.assertIsNone(kept[0].find('.//'+w('ins')))
        self.assertIsNone(kept[0].find('.//'+w('b')))
        self.assertTrue(any(p['old_text']==SUMMARY and p['decision']=='accept' for p in copied))
    def test_source_warning_leaves_recovered_summary_pending(self):
        self.seed();engine,result=self.analyze()
        from workbench.source_coverage import recover_source_coverage
        td=engine.load(self.files[0]);sd=engine.load(self.files[1]);sb=next(b for b in sd.blocks if b.text==SUMMARY)
        initial=[p for p in result['proposals'] if ':coverage:' not in p['id']]
        ps=recover_source_coverage(td,[sd],initial,{(sd.hash,sb.index):['来源数字冲突']})
        found=next(p for p in ps if p['old_text']==SUMMARY);self.assertEqual('pending',found['decision']);self.assertIsNone(found['selected'])

    def test_short_title_stays_unchanged_even_when_its_body_is_source_owned(self):
        heading='1、货币资金'
        for f in self.files:
            def edit(body,f=f):
                parent=next(p for p in body if p.tag==w('p') and text(p)=='（一）主要业务情况')
                body[list(body).index(parent)]=paragraph('（一）偿债资金来源' if f['role']=='target' else '（一）现金流量分析')
                anchor=next(p for p in body if p.tag==w('p') and text(p)==FACT)
                body.insert(list(body).index(anchor),paragraph(heading,f['role']=='source'))
            rewrite_document(f,edit)
        engine,result=self.analyze()
        self.assertFalse(any(p['old_text']==heading and p['decision']=='accept' for p in result['proposals']))
        out=self.base/'short-title-output';export=engine.export(result,self.files,out)
        root=read_root(out/export['checks'][0]['file']);inserted=paragraphs(root,heading)
        self.assertEqual(1,len(inserted));self.assertIsNone(inserted[0].find('.//'+w('ins')))
        self.assertIsNone(inserted[0].find('.//'+w('b')))
        resolve_revisions(root,False)
        self.assertEqual(structural_digest_content(read_root(self.files[0]['path'])),structural_digest_content(root))

    def test_same_short_title_cannot_borrow_an_anchor_from_before_its_source_section(self):
        heading='1、货币资金'
        for f in self.files:
            def edit(body,f=f):
                parent=next(p for p in body if p.tag==w('p') and text(p)=='（一）主要业务情况')
                body[list(body).index(parent)]=paragraph('（一）偿债资金来源' if f['role']=='target' else '（一）现金流量分析')
                anchor=next(p for p in body if p.tag==w('p') and text(p)==FACT)
                # Target fact is under this heading; source fact precedes it and
                # therefore cannot establish ownership of the following section.
                body.insert(list(body).index(anchor)+(1 if f['role']=='source' else 0),paragraph(heading,f['role']=='source'))
            rewrite_document(f,edit)
        _,result=self.analyze()
        self.assertTrue(any(p['old_text']==FACT and p['decision']=='accept' for p in result['proposals']))
        self.assertFalse(any(p['old_text']==heading and p['decision']=='accept' for p in result['proposals']))

    def numbered_title_case(self, child=False, different_wording=False):
        old='（1）设立募集资金专项账户';new='1、设立募集资金专项账户'
        if different_wording:new='1、设立偿债资金专项账户'
        for f in self.files:
            def edit(body,f=f):
                anchor=next(p for p in body if p.tag==w('p') and text(p)==FACT)
                at=list(body).index(anchor)
                if child:
                    body.insert(at,paragraph('（一）控股股东' if f['role']=='target' else '（二）控股股东'));at+=1
                body.insert(at,paragraph(old if f['role']=='target' else new,f['role']=='source'))
            rewrite_document(f,edit)
        return old,new

    def test_numbered_heading_variant_does_not_change_workpaper_numbering(self):
        old,new=self.numbered_title_case();engine,result=self.analyze()
        self.assertFalse(any(p['old_text']==old and p['decision']=='accept' for p in result['proposals']))
        out=self.base/'renumbered-output';export=engine.export(result,self.files,out)
        root=read_root(out/export['checks'][0]['file'])
        self.assertEqual([],paragraphs(root,new))
        retained=paragraphs(root,old);self.assertEqual(1,len(retained))
        self.assertIsNone(retained[0].find('.//'+w('ins')))
        self.assertFalse(any(old==''.join(t.text or '' for t in d.iter(w('delText'))) for d in root.iter(w('del'))))
        resolve_revisions(root,False)
        self.assertEqual(structural_digest_content(read_root(self.files[0]['path'])),structural_digest_content(root))

    def test_parent_numbering_is_preserved_when_children_match_source(self):
        self.numbered_title_case(child=True);_,result=self.analyze()
        self.assertFalse(any(p['old_text']=='（一）控股股东' and p['decision']=='accept' for p in result['proposals']))

    def test_numbering_normalization_does_not_change_heading_words(self):
        old,new=self.numbered_title_case(different_wording=True);_,result=self.analyze()
        self.assertFalse(any(p['old_text']==old and ':coverage:' in p['id'] and p['decision']=='accept' for p in result['proposals']))

    def test_export_rejects_any_source_copy_plan_covering_original_heading(self):
        old,new=self.numbered_title_case();engine,result=self.analyze()
        doc=engine.load(self.files[0]);index=next(b.index for b in doc.blocks if b.text==old)
        proposal=copy.deepcopy(next(p for p in result['proposals'] if p['old_text']==FACT and p['decision']=='accept'))
        proposal.update(id=doc.hash+':invalid-heading-copy',start=index,end=index+1,old_text=old)
        result['proposals'].append(proposal)
        with self.assertRaisesRegex(ValueError,'底稿标题及编号须保留原样'):
            engine.export(result,self.files,self.base/'invalid-heading-output')

    def heading_bridge_case(self,variant='same',value=SUMMARY):
        self.files=[make_docx(self.base/'原始.docx'),make_docx(self.base/'当前募集说明书.docx',source=True)]
        heading='（二）食品经营收入确认方法'
        for f in self.files:
            def edit(body,f=f):
                source=f['role']=='source';anchor=next(p for p in body if p.tag==w('p') and text(p)==FACT)
                at=list(body).index(anchor)+1
                if source and variant=='changed-anchor':anchor.find('.//'+w('t')).text=FACT+'发行人新增了新的业务板块。'
                body.insert(at,paragraph('（三）食品经营收入确认方法' if source and variant=='changed-heading' else heading,source))
                if source and variant=='gap':body.insert(at+1,paragraph(''));at+=1
                body.insert(at+1,paragraph(value,source))
            rewrite_document(f,edit)
        return heading

    def test_retained_identical_heading_locates_body_but_is_not_copied(self):
        heading=self.heading_bridge_case();engine,result=self.analyze()
        p=next(p for p in result['proposals'] if p['old_text']==SUMMARY)
        self.assertEqual('accept',p['decision'])
        self.assertEqual('identical-retained-heading',p['candidates'][0]['correspondence']['anchors'][0]['method'])
        self.assertFalse(any(p['old_text']==heading for p in result['proposals']))
        out=self.base/'retained-locator';export=engine.export(result,self.files,out)
        self.assertTrue(export['checks'][0]['independent_output_audit']['passed'])
        root=read_root(out/export['checks'][0]['file']);kept=paragraphs(root,heading)[0]
        self.assertIsNone(kept.find('.//'+w('ins')));self.assertIsNone(kept.find('.//'+w('b')))
        copied=paragraphs(root,SUMMARY)[0]
        self.assertIsNotNone(copied.find('.//'+w('ins')));self.assertIsNone(copied.find('.//'+w('b')))
        resolve_revisions(root,False)
        self.assertEqual(structural_digest_content(read_root(self.files[0]['path'])),structural_digest_content(root))

    def test_heading_locator_does_not_cross_gaps_or_change_numbering_or_body_anchor(self):
        for variant in ('gap','changed-heading','changed-anchor'):
            with self.subTest(variant=variant):
                self.heading_bridge_case(variant);_,result=self.analyze()
                self.assertFalse(any(p['old_text']==SUMMARY and p['decision']=='accept' for p in result['proposals']))

    def test_heading_locator_never_unlocks_independent_judgment(self):
        self.heading_bridge_case(value=JUDGMENT);_,result=self.analyze()
        self.assertFalse(any(p['old_text']==JUDGMENT and p['decision']=='accept' for p in result['proposals']))

    def test_heading_locator_requires_accepted_anchor_and_undisputed_heading(self):
        from workbench.source_coverage import recover_source_coverage
        heading=self.heading_bridge_case();engine,result=self.analyze()
        td,sd=map(engine.load,self.files)
        base=[p for p in result['proposals'] if ':coverage:' not in p['id']]
        for issue in ('pending-anchor','heading-warning'):
            with self.subTest(issue=issue):
                props=copy.deepcopy(base);flags={}
                if issue=='pending-anchor':
                    for p in props:
                        if p['old_text']==FACT:p.update(decision='pending',selected=None)
                else:flags[(sd.hash,next(b.index for b in sd.blocks if b.text==heading))]=['标题来源冲突']
                recovered=recover_source_coverage(td,[sd],props,flags)
                self.assertFalse(any(p['old_text']==SUMMARY and p['decision']=='accept' for p in recovered))

    def test_source_only_subheading_does_not_enter_scope_or_export(self):
        from test_engine_boundaries import JUDGMENT as ordinary_judgment
        source_heading='1、基础设施业务收入'
        for f in self.files:
            def edit(body,f=f):
                for p in list(body):
                    if text(p)==ordinary_judgment:body.remove(p)
                if f['role']=='source':
                    anchor=next(p for p in body if p.tag==w('p') and text(p)==FACT)
                    body.insert(list(body).index(anchor),paragraph(source_heading))
            rewrite_document(f,edit)
        engine,result=self.analyze();sd=engine.load(self.files[1])
        for p in result['proposals']:
            if p['decision'] not in ('accept','same') or not p['candidates']:continue
            self.assertFalse(any(sd.blocks[i].heading for i in p['candidates'][p['selected']]['indices']))
        out=self.base/'no-new-heading';export=engine.export(result,self.files,out)
        self.assertEqual([],paragraphs(read_root(out/export['checks'][0]['file']),source_heading))
        # A persisted/manual plan cannot bypass the source-side export guard.
        p=next(p for p in result['proposals'] if p['old_text']==FACT and p['decision']=='accept')
        c=p['candidates'][p['selected']];i=next(b.index for b in sd.blocks if b.text==source_heading)
        c.update(start=i,indices=[i]+c['indices'])
        with self.assertRaisesRegex(ValueError,'来源替换范围不得新增标题'):
            engine.export(result,self.files,self.base/'invalid-source-heading')

    def quoted_clause_case(self,closed=True,interrupted=False):
        from types import SimpleNamespace
        from workbench.model import Block
        from workbench.source_coverage import recover_quoted_clause
        opener='2.2.4发生下列事项之一，需要决定或授权采取相应措施，包括与发行人进行协商谈判以及提起仲裁程序的：'
        tail=['a.发行人已经或预计不能按期支付本次债券的本金或者利息；',
              'b.发生其他对债券持有人权益有重大不利影响的事项。']
        def blocks(values):
            return [Block(i,paragraph(value), 'paragraph',value,path=['债券持有人会议规则'],
                          matter='5-2-6',section='situ') for i,value in enumerate(values)]
        target=SimpleNamespace(blocks=blocks(['“'+opener,tail[0],tail[1]+('”' if closed else '')]),profile={'issuer':COMPANY})
        source=SimpleNamespace(blocks=blocks([opener]+tail),profile={'issuer':COMPANY,'kind':'prospectus'},
                               hash='source-bytes',name='当前募集说明书.docx')
        source.blocks[0].el=paragraph(opener,True)
        props=[{'id':'target:a0','start':0,'end':1,'old_text':'“'+opener,'content_class':'no_correspondence_retained',
                'decision':'keep','candidates':[],'selected':None}]
        props.extend({'id':'target:a'+str(i),'start':i,'end':i+1,'decision':'pending' if interrupted and i==1 else 'accept',
                      'selected':0,'candidates':[{'doc_hash':source.hash,'indices':[i]}]} for i in (1,2))
        return recover_quoted_clause(target,[source],props)[0],source

    def test_numbered_quote_opener_recovers_only_from_complete_copied_tail(self):
        from workbench.precision import fingerprint
        p,source=self.quoted_clause_case()
        self.assertEqual('accept',p['decision']);self.assertEqual([0],p['candidates'][0]['indices'])
        self.assertEqual(source.blocks[0].text,p['candidates'][0]['text'])
        self.assertEqual(fingerprint([source.blocks[0].el]),p['candidates'][0]['source_fingerprint'])
        self.assertEqual(['target:a1','target:a2'],p['candidates'][0]['correspondence']['tail_proposals'])

    def test_numbered_quote_with_uncopied_intermediate_tail_keeps_original(self):
        p,_=self.quoted_clause_case(interrupted=True)
        self.assertEqual('keep',p['decision']);self.assertEqual([],p['candidates'])

    def test_numbered_quote_without_original_closing_quote_keeps_original(self):
        p,_=self.quoted_clause_case(closed=False)
        self.assertEqual('keep',p['decision']);self.assertEqual([],p['candidates'])

class RepeatedSourceHeadingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root=Path(os.environ.get('WORKBENCH_REAL_FIXTURES',str(Path(__file__).resolve().parents[2]/'核查工作台_Codex交接包')))/'02_示例项目A'
        if not root.exists():raise unittest.SkipTest('真实交接包未提供。')
        cls.source=Document(next((root/'01_正式来源').glob('2 *.docx')))
        cls.ninth=Document(root/'02_原始底稿'/'核查记录及分析文件-第九章.docx')
        cls.tenth=Document(root/'02_原始底稿'/'核查记录及分析文件-第十章.docx')

    def recover(self,target,index,with_before=True):
        from workbench.source_coverage import recover_source_coverage
        pairs=[(index+1,1120)]
        if with_before:pairs.insert(0,(index-1,1284))
        props=[{'id':target.hash+':anchor'+str(t),'start':t,'end':t+1,
                'matter':target.blocks[t].matter,'decision':'accept','selected':0,
                'candidates':[{'doc_hash':self.source.hash,'start':s,'end':s+1,
                               'indices':[s],'text':self.source.blocks[s].text}]} for t,s in pairs]
        return [p for p in recover_source_coverage(target,[self.source],props) if p['start']==index]

    def test_real_exact_title_stays_outside_copy_plan(self):
        self.assertEqual([],self.recover(self.tenth,98))

    def test_real_renumbered_title_stays_outside_copy_plan(self):
        self.assertEqual([],self.recover(self.ninth,90))

    def test_repeated_child_alone_cannot_pull_heading_from_other_chapter(self):
        self.assertEqual([],self.recover(self.tenth,98,with_before=False))


if __name__=='__main__':unittest.main()

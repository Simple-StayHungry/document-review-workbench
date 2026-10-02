"""Formal opinion matching is bounded by source evidence, not blanket unprotection."""
import copy
import tempfile
import unittest
from pathlib import Path
from xml.etree import ElementTree as E

from workbench.docxio import w,text,resolve_revisions,structural_digest_content
from workbench.engine import Engine
from workbench.model import Document,Unit
from workbench.matching import Index,normal
from workbench.source_policy import prepare_target,independent_reason
from test_engine_boundaries import make_docx,COMPANY,FACT
from test_copy_retention import rewrite_document,read_root,paragraphs

OPINION='经核查，发行人聘请的证券服务机构及其签字人员均具备相应执业资格，未发现影响本次债券发行的重大不利事项。'
TOPIC='（一）证券服务机构资格情况'


def set_paragraph(p,value,bold=False):
    for child in list(p):p.remove(child)
    r=E.SubElement(p,w('r'))
    if bold:E.SubElement(E.SubElement(r,w('rPr')),w('b'))
    E.SubElement(r,w('t')).text=value


class EqualSourceTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory(prefix='equal-source-');self.addCleanup(tmp.cleanup)
        self.base=Path(tmp.name)
        self.target=make_docx(self.base/'底稿.docx')
        self.prospectus=make_docx(self.base/'当前募集说明书.docx',source=True)
        self.opinion=make_docx(self.base/'主承销商核查意见.docx',source=True)
        self.params={'author':'柒','issuer':COMPANY}
        self.seed(self.target,OPINION)
        self.seed(self.opinion,OPINION,opinion=True)
    def seed(self,record,value,opinion=False,topic=TOPIC):
        def edit(body):
            for p in body.findall(w('p')):
                if text(p)=='募集说明书' and opinion:set_paragraph(p,'主承销商核查意见')
                elif text(p)=='（一）主要业务情况':set_paragraph(p,topic)
                elif text(p)==FACT:set_paragraph(p,value,bold=opinion)
        rewrite_document(record,edit)
    def documents(self):return [Document(f['path']) for f in (self.target,self.prospectus,self.opinion)]
    def prepared(self):
        t,p,o=self.documents();prepare_target(t,[p,o]);return t,p,o
    def test_actual_opinion_copy_uses_target_format_and_reject_restores(self):
        engine=Engine();files=[self.target,self.prospectus,self.opinion]
        result=engine.analyze(files,self.params)
        copied=next(p for p in result['proposals'] if p['old_text']==OPINION)
        self.assertEqual('accept',copied['decision'])
        self.assertEqual(self.opinion['id'],copied['candidates'][copied['selected']]['doc_hash'])
        out=self.base/'out';export=engine.export(result,files,out)
        root=read_root(out/export['checks'][0]['file'])
        values=paragraphs(root,OPINION);self.assertEqual(1,len(values))
        self.assertIsNotNone(values[0].find('.//'+w('ins')))
        self.assertIsNone(values[0].find('.//'+w('b')))
        resolve_revisions(root,False)
        self.assertEqual(structural_digest_content(read_root(self.target['path'])),structural_digest_content(root))
    def test_source_removal_revokes_cached_permission(self):
        target,primary,opinion=self.prepared();b=next(b for b in target.blocks if b.text==OPINION)
        self.assertFalse(b.protected);self.assertFalse(independent_reason(b))
        prepare_target(target,[primary]);self.assertTrue(b.protected)
        self.assertFalse(getattr(b,'formal_source_correspondence',None))
    def test_only_proven_paragraph_unlocked_not_neighboring_judgment(self):
        target,primary,opinion=self.prepared()
        approved=next(b for b in target.blocks if b.text==OPINION)
        other=next(b for b in target.blocks if b.text.startswith('经项目组核查'))
        self.assertTrue(other.protected);self.assertTrue(independent_reason(other))
        own=next(u for u in target.units if u.start<=approved.index<u.end)
        self.assertEqual((approved.index,approved.index+1),(own.start,own.end))
        self.assertTrue(all(not b.heading for u in target.units for b in u.blocks))
    def test_matching_opinion_never_unlocks_external_query(self):
        value='经企查查查询，发行人及其相关重要子公司不存在重大违法违规或严重失信行为，具体以本次查询结果为依据。'
        for f in (self.target,self.opinion):
            rewrite_document(f,lambda body:[set_paragraph(p,value) for p in body.findall(w('p')) if text(p)==OPINION])
        t,p,o=self.prepared();b=next(b for b in t.blocks if b.text==value)
        self.assertTrue(b.protected);self.assertFalse(getattr(b,'formal_source_correspondence',None))
    def test_opinion_cannot_unlock_procedure_section(self):
        for f in (self.target,self.opinion):
            def edit(body,f=f):
                anchor=next(p for p in body.findall(w('p')) if text(p)==OPINION)
                if f is self.target:
                    body.remove(anchor);proc=next(p for p in body.findall(w('p')) if text(p)=='一、核查程序')
                    body.insert(list(body).index(proc)+1,anchor)
            rewrite_document(f,edit)
        t,p,o=self.prepared();b=next(b for b in t.blocks if b.text==OPINION)
        self.assertEqual('proc',b.section);self.assertTrue(b.protected)
    def test_same_number_conflict_prefers_prospectus_even_if_opinion_matches_old(self):
        old='截至2026年3月末，发行人已获得金融机构授信额度共计100,000.00万元，剩余未使用额度为30,000.00万元，融资渠道较为稳定。'
        new=old.replace('100,000.00','120,000.00').replace('30,000.00','50,000.00')
        for f in (self.target,self.opinion):
            rewrite_document(f,lambda body:[set_paragraph(p,old) for p in body.findall(w('p')) if text(p)==OPINION])
        self.seed(self.prospectus,new)
        t,p,o=self.documents();b=next(b for b in t.blocks if b.text==old)
        u=Unit('test',b.index,b.index+1,[b],TOPIC,b.path,b.matter)
        status,candidates,reason=Index([p,o]).assess(u)
        self.assertEqual('auto',status,reason);self.assertEqual(p.hash,candidates[0]['doc_hash'])
        self.assertEqual(new,candidates[0]['text'])
        self.assertEqual('conflicting-formal-sources-prefer-prospectus',candidates[0]['source_precedence']['rule'])
    def test_weak_shared_heading_cannot_displace_real_body_candidate(self):
        from types import SimpleNamespace
        from workbench.model import Block
        old='2026年【】月【】日，本公司董事会会议审议并通过了面向专业投资者非公开发行公司债券的议案，并出具了《董事会决议》（【】号）。'
        current=old.replace('【】月【】日','6月18日').replace('（【】号）','（董〔2026〕06-01号）')
        def unit(i,value,heading):
            el=E.Element(w('p'));r=E.SubElement(el,w('r'));E.SubElement(r,w('t')).text=value
            b=Block(i,el,'paragraph',value,path=[heading],section='situ')
            return Unit(str(i),i,i+1,[b],heading,b.path,'')
        target=unit(0,old,'有权机构的核查结论')
        primary=SimpleNamespace(hash='prospectus',name='当前募集说明书',profile={'kind':'prospectus'},source_units=[unit(1,current,'内部批准情况及注册情况')])
        opinion=SimpleNamespace(hash='opinion',name='当前核查意见',profile={'kind':'opinion'},source_units=[unit(2,'本次公司债券发行完成后，主承销商将继续关注募集资金的使用情况及发行人的信息披露义务。','有权机构的核查结论')])
        candidates=Index([primary,opinion]).candidates(target)
        self.assertTrue(candidates)
        self.assertEqual('prospectus',candidates[0]['doc_hash'])
        self.assertEqual([1],candidates[0]['indices'])

    def test_same_heading_unrelated_opinion_does_not_unlock(self):
        rewrite_document(self.opinion,lambda body:[set_paragraph(p,'经核查，本次债券募集资金拟全部用于符合规定的工程项目，相关项目已获得完整审批文件，资金使用情况将持续披露。') for p in body.findall(w('p')) if text(p)==OPINION])
        t,p,o=self.prepared();b=next(b for b in t.blocks if b.text==OPINION)
        self.assertTrue(b.protected);self.assertFalse(getattr(b,'formal_source_correspondence',None))
    def test_partial_opinion_does_not_delete_unmatched_tail(self):
        extension='发行人与服务机构另行签订的补充协议还约定每季度执行专项穿透核查并另行出具专项分析报告。'
        rewrite_document(self.target,lambda body:[set_paragraph(p,OPINION+extension) for p in body.findall(w('p')) if text(p)==OPINION])
        t,p,o=self.prepared();b=next(b for b in t.blocks if b.text==OPINION+extension)
        self.assertTrue(b.protected);self.assertFalse(getattr(b,'formal_source_correspondence',None))

if __name__=='__main__':unittest.main()

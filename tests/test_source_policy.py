"""Regressions for ambiguous current versions and source-merge safeguards."""
import copy
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from xml.etree import ElementTree as E

from workbench.docxio import w
from workbench.engine import Engine
from workbench.model import Block, Unit
from workbench.matching import Index, normal
from workbench.source_policy import paragraph_merges
from test_engine_boundaries import make_docx, COMPANY


OPENING='公司主要经营的产业包括种猪培育、商品猪饲养、屠宰加工和销售，且生产环节形成完整商业链条。'
TAIL='该行业产量在报告期内稳步增长，市场供需矛盾有所改善，居民肉类消费习惯相对稳定，生猪养殖的规模化水平进一步提高。'


def paragraph(index, value, protected=False, images=0):
    element=E.Element(w('p'));run=E.SubElement(element,w('r'))
    E.SubElement(run,w('t')).text=value
    if images:E.SubElement(run,w('drawing'))
    return Block(index,element,'paragraph',value,path=['4、产业发展情况'],matter='1-1',
                 section='situ',protected=protected,images=images)


def merge_fixture(intervening=()):
    blocks=[paragraph(0,OPENING+'公司具有旧版自有繁育系统。')]
    for value,protected,images in intervening:
        blocks.append(paragraph(len(blocks),value,protected,images))
    blocks.append(paragraph(len(blocks),TAIL))
    source=SimpleNamespace(hash='current-source',name='当前募集说明书.docx',
                           blocks=[paragraph(0,OPENING+TAIL)])
    proposals=[{'id':'p'+str(b.index),'start':b.index,'end':b.index+1,
                'candidates':[],'status':'review','decision':'pending'}
               for b in (blocks[0],blocks[-1])]
    return SimpleNamespace(blocks=blocks),source,proposals


class CurrentSourceVersionTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='workbench-source-policy-')
        self.addCleanup(self.tmp.cleanup);self.base=Path(self.tmp.name)

    def test_engine_rejects_different_current_prospectus_bytes_before_matching(self):
        target=make_docx(self.base/'待更新底稿.docx')
        old=make_docx(self.base/'当前募集版本甲.docx',source=True)
        changed=make_docx(self.base/'当前募集版本乙.docx',source=True,
                          extra='本版本新增了另一项正式披露，不能依靠与旧底稿相似度选择当前版本。')
        self.assertNotEqual(old['id'],changed['id'])
        self.assertEqual(old['profile']['issuer'],changed['profile']['issuer'])
        with self.assertRaisesRegex(ValueError,'多份不同字节.*募集说明书|唯一当前版本'):
            Engine().analyze([target,old,changed],{'issuer':COMPANY,'author':'柒'})

    def test_same_bytes_aliases_are_one_current_source(self):
        target=make_docx(self.base/'待更新底稿.docx')
        source=make_docx(self.base/'当前募集说明书.docx',source=True)
        alias=copy.deepcopy(source);alias['name']='同字节募集副本.docx'
        result=Engine().analyze([target,source,alias],{'issuer':COMPANY,'author':'柒'})
        active=[p for p in result['proposals'] if p['decision'] in ('accept','same') and p['kind']=='material']
        self.assertTrue(active)
        self.assertTrue(all(p['candidates'][p['selected']]['doc_hash']==source['id'] for p in active))


class SourceMergeWarningTests(unittest.TestCase):
    def test_retrieval_date_alias_does_not_authorize_an_exact_source_excerpt(self):
        old='截至本核查分析文件出具日，发行人控股股东和实际控制人持有可支配的发行人股权不存在质押、冻结或发生诉讼仲裁等事项。'
        excerpt=old.replace('本核查分析文件出具日','本募集说明书签署日')
        whole=excerpt+'发行人报告期内的股权结构保持稳定，具体控制关系以本节披露的股权结构图为准。'
        target_block=paragraph(0,old)
        target=Unit('target',0,1,[target_block],target_block.path[-1],target_block.path,'1-1')
        source_block=paragraph(0,whole);excerpt_block=paragraph(0,excerpt)
        full=Unit('full',0,1,[source_block],source_block.path[-1],source_block.path,'')
        sliced=Unit('excerpt',0,1,[excerpt_block],excerpt_block.path[-1],excerpt_block.path,'')
        sliced.span={'block':0,'start':0,'end':len(excerpt)}
        source=SimpleNamespace(hash='current-source',name='当前募集说明书.docx',
                               profile={'kind':'prospectus'},source_units=[full,sliced])
        # Retrieval deliberately treats the phrases as aliases. That must not
        # become permission to copy a partial source as an identical old sentence.
        self.assertEqual(normal(old),normal(excerpt));self.assertNotEqual(old,excerpt)
        status,candidates,_reason=Index([source]).assess(target)
        self.assertTrue(candidates)
        self.assertTrue(all(not c.get('span') for c in candidates))
        self.assertEqual(whole,candidates[0]['text'])
        if status=='auto':self.assertEqual('full',candidates[0]['unit_id'])

    def test_merge_tail_voice_alias_is_not_literal_correspondence(self):
        doc,source,props=merge_fixture()
        doc.blocks[-1]=paragraph(1,TAIL+'截至本核查分析文件出具日。')
        source.blocks[0]=paragraph(0,OPENING+TAIL+'截至本募集说明书签署日。')
        merged=paragraph_merges(doc,[source],props)
        self.assertTrue(all(p['decision']=='pending' for p in merged))
        self.assertTrue(all(p.get('action')!='source_merge' for p in merged))

    def test_source_flags_keep_copy_and_deletion_pending(self):
        doc,source,props=merge_fixture()
        result=paragraph_merges(doc,[source],props,{(source.hash,0):['来源披露存在一致性提示']})
        self.assertEqual(2,len(result))
        for p in result:
            self.assertEqual(('review','pending',None),(p['status'],p['decision'],p['selected']))
            self.assertEqual(['来源披露存在一致性提示'],p['candidates'][0]['source_warnings'])
        self.assertEqual('source_merge',result[1]['action'])
        self.assertEqual(result[0]['id'],result[1]['merge_into'])

    def test_existing_same_source_candidate_warnings_are_not_cleared(self):
        doc,source,props=merge_fixture()
        props[0]['candidates']=[{'doc_hash':source.hash,'indices':[0],
                                  'source_warnings':['原候选保留的来源提示']}]
        props[1]['candidates']=[{'doc_hash':source.hash,'indices':[0],
                                  'source_warnings':['后段候选来源提示']}]
        result=paragraph_merges(doc,[source],props)
        expected=sorted(['原候选保留的来源提示','后段候选来源提示'])
        for p in result:
            self.assertEqual('pending',p['decision'])
            self.assertEqual(expected,p['candidates'][0]['source_warnings'])

    def test_merge_never_crosses_independent_protected_narrative(self):
        doc,source,props=merge_fixture([('项目组认为上述业务情况需要另行核实，不能以来源披露替代核查结论。',True,0)])
        result=paragraph_merges(doc,[source],props)
        self.assertTrue(all(p['decision']=='pending' for p in result))
        self.assertTrue(all(not p.get('action') and not p.get('merge_into') for p in result))
        self.assertTrue(all(not p['candidates'] for p in result))

    def test_unrelated_factual_text_also_blocks_merge(self):
        doc,source,props=merge_fixture([('该公司的另一项业务具有独立经营范围和单独财务核算安排。',False,0)])
        result=paragraph_merges(doc,[source],props)
        self.assertTrue(all(p['decision']=='pending' and not p.get('action') for p in result))

    def test_intervening_caption_and_image_are_retained_without_being_consumed(self):
        doc,source,props=merge_fixture([('图：旧版产业链结构',True,0),('',True,1)])
        result=paragraph_merges(doc,[source],props)
        self.assertTrue(all(p['decision']=='accept' for p in result))
        deleted=next(p for p in result if p.get('action')=='source_merge')
        self.assertEqual((3,4),(deleted['start'],deleted['end']))
        touched={i for p in result for i in range(p['start'],p['end'])}
        self.assertEqual({0,3},touched)
        self.assertTrue(doc.blocks[1].protected);self.assertEqual(1,doc.blocks[2].images)


if __name__=='__main__':unittest.main()

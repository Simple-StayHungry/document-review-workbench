import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from xml.etree import ElementTree as E

from workbench.docxio import w
from workbench.figures import figure_proposals
from workbench.model import Document
from tests.test_figures import sample_doc

M='http://schemas.openxmlformats.org/officeDocument/2006/math'


def formula_document(path, source=False, value='ER=BE-PE', issuer='甲方城市发展有限公司',
                     before='根据本项目的温室气体减排量计算标准，年度减排量计算如下：',
                     after='式中，各参数均按照本项目当年的计量结果及现行排放因子计算。',
                     caption='：一定时期内项目温室气体减排量，单位为吨二氧化碳。',
                     duplicate=False):
    root=E.Element(w('document'));body=E.SubElement(root,w('body'))
    def paragraph(text):
        p=E.SubElement(body,w('p'));r=E.SubElement(p,w('r'));E.SubElement(r,w('t')).text=text
        return p
    paragraph(issuer);paragraph('2026年公司债券');paragraph('募集说明书' if source else '核查记录及分析文件')
    if not source:paragraph('8-2-1 特殊品种发行条件核查记录');paragraph('二、核查情况')
    paragraph('2、低碳转型绩效目标（SPT）的设置');paragraph('6）预期环境效益')
    for _ in range(2 if duplicate else 1):
        if before:paragraph(before)
        p=paragraph(caption)
        math=E.SubElement(p,'{'+M+'}oMath');r=E.SubElement(math,'{'+M+'}r');E.SubElement(r,'{'+M+'}t').text=value
        if source:
            pr=E.SubElement(p.find(w('r')),w('rPr'));E.SubElement(pr,w('b'))
        if after:paragraph(after)
    with zipfile.ZipFile(path,'w') as z:z.writestr('word/document.xml',E.tostring(root))
    return Document(path)


class NativeFormulaRouteTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.base=Path(self.tmp.name)

    def test_same_formula_is_a_full_source_paragraph_not_a_restyled_target(self):
        target=formula_document(self.base/'target.docx');source=formula_document(self.base/'source.docx',source=True)
        p=figure_proposals(target,[source])[0]
        self.assertEqual('accept',p['decision']);self.assertFalse(p['figure'])
        self.assertEqual('native_formula',p['object_kind'])
        c=p['candidates'][0]
        self.assertEqual(source.hash,c['doc_hash']);self.assertIsNone(c['span'])
        self.assertTrue(c['correspondence']['whole_source_paragraph'])
        self.assertEqual({'before':True,'after':True},c['correspondence']['neighbor_agreements'])

    def test_two_neighbors_identify_a_changed_source_formula(self):
        target=formula_document(self.base/'target.docx',value='ER=BE+PE')
        source=formula_document(self.base/'source.docx',source=True,value='ER=BE-PE')
        p=figure_proposals(target,[source])[0]
        self.assertEqual('accept',p['decision'])
        self.assertFalse(p['candidates'][0]['correspondence']['formula_equal'])

    def test_changed_formula_needs_both_neighbors(self):
        target=formula_document(self.base/'target.docx',value='ER=BE+PE',after='')
        source=formula_document(self.base/'source.docx',source=True,value='ER=BE-PE',after='')
        self.assertNotEqual('accept',figure_proposals(target,[source])[0]['decision'])

    def test_same_formula_can_use_a_section_edge_and_its_explanation(self):
        target=formula_document(self.base/'target.docx',after='')
        source=formula_document(self.base/'source.docx',source=True,after='')
        self.assertEqual('accept',figure_proposals(target,[source])[0]['decision'])

    def test_same_formula_and_same_heading_without_prose_anchors_is_not_ownership(self):
        target=formula_document(self.base/'target.docx',before='',after='')
        source=formula_document(self.base/'source.docx',source=True,before='',after='')
        self.assertNotEqual('accept',figure_proposals(target,[source])[0]['decision'])

    def test_duplicate_formula_slots_are_pending(self):
        target=formula_document(self.base/'target.docx')
        source=formula_document(self.base/'source.docx',source=True,duplicate=True)
        self.assertEqual('pending',figure_proposals(target,[source])[0]['decision'])

    def test_other_issuer_is_not_a_formula_source(self):
        target=formula_document(self.base/'target.docx')
        source=formula_document(self.base/'source.docx',source=True,issuer='乙方城市发展有限公司')
        self.assertEqual([],figure_proposals(target,[source])[0]['candidates'])

    def test_signature_image_is_a_protected_workpaper_record(self):
        target=sample_doc(self.base/'target.docx')
        group=next(b for b in target.blocks if b.images)
        group.el.find('./'+w('r')+'/'+w('t')).text='调查人签字：';group.text='调查人签字：'
        p=figure_proposals(target,[])[0]
        self.assertEqual('protected',p['decision']);self.assertIn('签字',p['reason'])

    def test_signature_word_in_substantive_prose_does_not_become_signature_record(self):
        target=sample_doc(self.base/'target.docx')
        group=next(b for b in target.blocks if b.images)
        group.el.find('./'+w('r')+'/'+w('t')).text='本文件须经调查人签字后提交审核。';group.text='本文件须经调查人签字后提交审核。'
        self.assertEqual('pending',figure_proposals(target,[])[0]['decision'])


class RealFormulaRoutes(unittest.TestCase):
    def test_xiaolan_all_24_formula_positions_have_unique_current_source(self):
        configured=os.environ.get('WORKBENCH_REAL_FIXTURES')
        root=Path(configured).expanduser().resolve() if configured else Path(__file__).resolve().parents[2]/'核查工作台_Codex交接包'
        fixture=root/'02_示例项目A'
        if not fixture.exists():self.skipTest('真实交接包未在开发目录旁提供。')
        target=Document(fixture/'02_原始底稿/核查记录及分析文件-第八章.docx')
        source=Document(next((fixture/'01_正式来源').glob('2 *.docx')))
        proposals=[p for p in figure_proposals(target,[source]) if p.get('object_kind')=='native_formula']
        self.assertEqual(24,len(proposals))
        self.assertTrue(all(p['decision']=='accept' for p in proposals),[(p['start'],p['reason']) for p in proposals if p['decision']!='accept'])
        self.assertTrue(all(p['candidates'][0]['start']==p['start']+162 for p in proposals))

    def test_legacy_ole_formulas_are_counted_as_pending_instead_of_disappearing(self):
        configured=os.environ.get('WORKBENCH_REAL_FIXTURES')
        root=Path(configured).expanduser().resolve() if configured else Path(__file__).resolve().parents[2]/'核查工作台_Codex交接包'
        fixture=root/'02_示例项目A'
        if not fixture.exists():self.skipTest('真实交接包未在开发目录旁提供。')
        target=Document(fixture/'02_原始底稿/核查记录及分析文件-第八章.docx')
        source=Document(next((fixture/'01_正式来源').glob('2 *.docx')))
        proposals={p['start']:p for p in figure_proposals(target,[source])}
        for index in (129,156,199):
            self.assertEqual('',target.blocks[index].text)
            self.assertEqual(0,target.blocks[index].images)
            self.assertIn(index,proposals)
            self.assertEqual('pending',proposals[index]['decision'])
            self.assertIn('OLE',proposals[index]['reason'])
            self.assertEqual([],proposals[index]['candidates'])


if __name__=='__main__':unittest.main()

import hashlib
import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from xml.etree import ElementTree as E

from workbench.docxio import W, R, A, PR, w
from workbench.model import Document
from workbench.figures import figure_proposals


def sample_doc(path, issuer='甲方城市发展有限公司', source=False, payload=b'fixture-raster',
               heading='4、屠宰行业', caption='图：价格变化情况（单位：元/公斤）',
               lead='价格方面，市场供需变化影响生猪销售价格。过去十年全国市场价格变动情况如下。',
               chart=False, external=False):
    root=E.Element(w('document'));body=E.SubElement(root,w('body'))
    def paragraph(value):
        p=E.SubElement(body,w('p'));r=E.SubElement(p,w('r'));E.SubElement(r,w('t')).text=value
        return p
    paragraph(issuer);paragraph('2026年公司债券');paragraph('募集说明书' if source else '核查记录及分析文件')
    if not source:
        paragraph('1-4-3 行业情况核查记录');paragraph('二、核查情况')
    paragraph(heading);paragraph('根据官网查询结果，核查截图如下：' if external else lead)
    paragraph(caption);p=paragraph('');run=p.find(w('r'));drawing=E.SubElement(run,w('drawing'))
    if chart:E.SubElement(drawing,'{http://schemas.openxmlformats.org/drawingml/2006/chart}chart',{'{'+R+'}id':'rIdMedia'})
    else:E.SubElement(drawing,'{'+A+'}blip',{'{'+R+'}embed':'rIdMedia'})
    paragraph('数据来源：农业农村部');paragraph('5、其他行业')
    rels=E.Element('{'+PR+'}Relationships')
    E.SubElement(rels,'{'+PR+'}Relationship',{'Id':'rIdMedia','Type':R+'/chart' if chart else R+'/image','Target':'media/source.png'})
    with zipfile.ZipFile(path,'w') as z:
        z.writestr('word/document.xml',E.tostring(root));z.writestr('word/_rels/document.xml.rels',E.tostring(rels))
        z.writestr('word/media/source.png',payload)
    return Document(path)


class FigureProposalTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.base=Path(self.tmp.name)
    def docs(self, **source_kwargs):
        target=sample_doc(self.base/'target.docx')
        source=sample_doc(self.base/'source.docx',source=True,**source_kwargs)
        return target,source
    def test_same_image_is_source_backed_group_copy(self):
        target,source=self.docs();p=figure_proposals(target,[source])[0]
        self.assertEqual(('auto','accept'),(p['status'],p['decision']))
        self.assertEqual(3,p['end']-p['start'])
        c=p['candidates'][0];media=c['figure_evidence']['source_media'][0]
        self.assertEqual(source.hash,c['doc_hash']);self.assertEqual('rIdMedia',media['relationship_id'])
        self.assertEqual(hashlib.sha256(b'fixture-raster').hexdigest(),media['sha256'])
        self.assertEqual('current_prospectus',c['source_role'])
        self.assertTrue(media['xml_path'].endswith('/body[1]/p[7]'))
    def test_cross_issuer_same_caption_media_and_context_never_matches(self):
        target,source=self.docs(issuer='乙方城市发展有限公司')
        p=figure_proposals(target,[source])[0]
        self.assertEqual('keep',p['decision']);self.assertEqual([],p['candidates'])
    def test_same_caption_needs_heading_and_neighbor(self):
        target,source=self.docs(heading='4、能源行业',lead='电力供应关系及配电网络结构如下。配电网服务不同地区电力终端用户，其产业链包含发电和售电。')
        self.assertEqual([],figure_proposals(target,[source])[0]['candidates'])
    def test_external_evidence_is_protected_before_source_matching(self):
        target=sample_doc(self.base/'target.docx',external=True)
        source=sample_doc(self.base/'source.docx',source=True)
        p=figure_proposals(target,[source])[0]
        self.assertEqual('protected',p['decision']);self.assertEqual([],p['candidates'])
    def test_native_chart_is_pending_and_cannot_be_forced_to_image(self):
        target=sample_doc(self.base/'target.docx',chart=True)
        source=sample_doc(self.base/'source.docx',source=True)
        p=figure_proposals(target,[source])[0]
        self.assertEqual('pending',p['decision']);self.assertIn('原生图表',p['reason']);self.assertEqual([],p['candidates'])
    def test_competing_current_media_are_not_chosen_by_old_image(self):
        target,source=self.docs()
        other=sample_doc(self.base/'other.docx',source=True,payload=b'different-source-image')
        p=figure_proposals(target,[source,other])[0]
        self.assertEqual('pending',p['decision']);self.assertIn('不同当前来源图',p['reason'])
    def test_opinion_is_not_a_prospectus_figure_source(self):
        target,source=self.docs();source.profile['kind']='opinion'
        self.assertEqual([],figure_proposals(target,[source])[0]['candidates'])
    def test_missing_figure_keeps_old_group(self):
        target,source=self.docs(caption='图：其他不同图组')
        p=figure_proposals(target,[source])[0]
        self.assertEqual('keep',p['decision']);self.assertEqual(3,p['end']-p['start'])
    def test_missing_source_relationship_media_never_autocopies(self):
        target,source=self.docs();del source.pkg.entries['word/media/source.png']
        p=figure_proposals(target,[source])[0]
        self.assertEqual('pending',p['decision']);self.assertIn('媒体不存在',p['reason'])


class RealXiaolanFigureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        configured=os.environ.get('WORKBENCH_REAL_FIXTURES')
        cls.fixture_root=Path(configured).expanduser().resolve() if configured else Path(__file__).resolve().parents[2]/'核查工作台_Codex交接包'
        fixtures=cls.fixture_root/'02_示例项目A'
        if configured and not fixtures.exists():raise AssertionError('WORKBENCH_REAL_FIXTURES does not contain 02_示例项目A.')
        if not fixtures.exists():raise unittest.SkipTest('真实交接包未在开发目录旁提供。')
        cls.source=Document(next((fixtures/'01_正式来源').glob('2 *.docx')))
        cls.target=Document(fixtures/'02_原始底稿'/'核查记录及分析文件-第一章.docx')
        cls.proposals=figure_proposals(cls.target,[cls.source])
    def test_actual_price_chart_uses_current_source_media(self):
        p=next(p for p in self.proposals if '2015年至今全国猪肉' in p['heading'])
        self.assertEqual('accept',p['decision'])
        source=p['candidates'][0]['figure_evidence']['source_media'][0]
        self.assertEqual('rId61',source['relationship_id'])
        self.assertEqual('word/media/image42.png',source['part'])
        self.assertEqual('8baec5723868ef75335e9d3219ce1b0722b1e51475a5fc67d45241da884ec114',source['sha256'])
        self.assertEqual(source['sha256'],p['figure_evidence']['target_media'][0]['sha256'])
        self.assertEqual(3,p['end']-p['start'])
    def test_old_industry_chain_has_no_automatic_deletion(self):
        p=next(p for p in self.proposals if '肉猪养殖行业和公司肉猪养殖产业链' in p['heading'])
        self.assertEqual('keep',p['decision']);self.assertEqual([],p['candidates'])
        self.assertEqual('b11b1ebd5e51f8a4dd02765286554f9f325ce38dcb34b92cd19530492e1e70fb',p['figure_evidence']['target_media'][0]['sha256'])
    def test_real_guangzhou_source_is_not_used_for_xiaolan(self):
        fixtures=self.fixture_root/'03_示例城投'/'01_独立正式来源'
        if not fixtures.exists():self.skipTest('示例城投独立正式来源未提供。')
        source=Document(next(fixtures.glob('*.docx')))
        self.assertTrue(all(not p['candidates'] for p in figure_proposals(self.target,[source])))


if __name__=='__main__':unittest.main()

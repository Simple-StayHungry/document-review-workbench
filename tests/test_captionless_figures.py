"""Source-backed captionless raster copy and safe ownership boundaries."""
import copy
import hashlib
import os
import tempfile
import unittest
from pathlib import Path
from xml.etree import ElementTree as E

from workbench.docxio import Package, R, A, w
from workbench.figures import figure_proposals
from workbench.model import Document
from workbench.output_audit import Reader, audit_docx, element_paths, revision_ids
from test_output_audit import write_doc, para, media_extra

LEAD='公司根据内部管理和业务需求设置办公室、人力资源部、财务部、企业管理部和审计部共5个部门。公司组织架构如下图：'


def specimen(path,source=False,payload=b'actual-organization-source',external=False,after='（1）办公室'):
    blocks=[para('甲方城市发展有限公司'),para('2026年公司债券'),para('募集说明书' if source else '核查记录及分析文件')]
    if not source:blocks.extend([para('1-5-5 关于公司治理及内部控制情况的核查记录'),para('二、核查情况')])
    blocks.extend([para('（一）发行人的治理结构及组织机构设置和运行情况'),para('2、发行人的组织结构'),para(('根据官网查询结果，核查截图如下图。'+LEAD) if external else LEAD)])
    image=E.Element(w('p'));run=E.SubElement(image,w('r'));drawing=E.SubElement(run,w('drawing'))
    E.SubElement(drawing,'{'+A+'}blip',{'{'+R+'}embed':'rId1'})
    blocks.extend([image,para(after),para('主要负责统筹全线安全生产、工会、共青团、妇女及后勤保障工作。')])
    write_doc(path,blocks,media_extra(payload));return Document(path)


class CaptionlessFigureTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory(prefix='captionless-figure-');self.addCleanup(temp.cleanup);self.path=Path(temp.name)
    def test_identical_image_is_actually_copied_from_source_relationship(self):
        d=specimen(self.path/'target.docx');s=specimen(self.path/'source.docx',source=True)
        p=figure_proposals(d,[s])[0];self.assertEqual('accept',p['decision'])
        c=p['candidates'][p['selected']];self.assertTrue(c['figure_evidence']['captionless_context_match'])
        pkg=Package(d.path);source=Package(s.path);old=[list(pkg.body)[p['start']]];new=[list(source.body)[c['start']]]
        targets=element_paths(pkg.root,old);inserted=pkg.replace(old,new,source=source,whole=True)
        out=self.path/'out.docx';pkg.save(out)
        rec={'id':p['id'],'action':'source_copy','source_file':str(s.path),'source_sha256':s.hash,'source_role':'prospectus',
             'target_paths':targets,'source_paths':element_paths(source.root,new),'output_paths':element_paths(pkg.root,inserted),
             'deletion_paths':element_paths(pkg.root,old),'insertion_revision_ids':revision_ids(inserted,'ins'),'deletion_revision_ids':revision_ids(old,'del')}
        report=audit_docx(out,d.path,[rec],[]);self.assertTrue(report['passed'],report['errors'])
        rd=Reader(out);blip=inserted[0].find('.//{'+A+'}blip');part=rd.rel('word/document.xml',blip.get('{'+R+'}embed'))[2]
        self.assertIn('/wb_'+source.hash[:10]+'_',part)
        self.assertEqual(hashlib.sha256(b'actual-organization-source').hexdigest(),hashlib.sha256(rd.parts[part]).hexdigest())
        self.assertTrue(inserted[0].find('.//'+w('ins')) is not None)
    def test_ambiguous_matching_context_cannot_choose_by_old_media(self):
        d=specimen(self.path/'target.docx');s=specimen(self.path/'source.docx',source=True)
        other=specimen(self.path/'other.docx',source=True,payload=b'different-current-source')
        p=figure_proposals(d,[s,other])[0]
        self.assertEqual('pending',p['decision']);self.assertEqual(2,len(p['candidates']))
    def test_external_screenshot_stays_protected_even_with_same_context(self):
        d=specimen(self.path/'target.docx',external=True);s=specimen(self.path/'source.docx',source=True,external=True)
        p=figure_proposals(d,[s])[0];self.assertEqual('protected',p['decision']);self.assertEqual([],p['candidates'])
    def test_missing_following_heading_does_not_match_identical_image(self):
        d=specimen(self.path/'target.docx');s=specimen(self.path/'source.docx',source=True,after='（1）企业管理部')
        p=figure_proposals(d,[s])[0];self.assertEqual('keep',p['decision']);self.assertEqual([],p['candidates'])
    def test_no_explicit_introduction_does_not_match(self):
        d=specimen(self.path/'target.docx');s=specimen(self.path/'source.docx',source=True)
        for doc in (d,s):
            b=next(b for b in doc.blocks if b.text==LEAD);b.text=LEAD.replace('公司组织架构如下图：','以上部门按各自职责独立开展工作。')
        self.assertEqual([],figure_proposals(d,[s])[0]['candidates'])
    def test_source_warnings_keep_proposal_pending(self):
        d=specimen(self.path/'target.docx');s=specimen(self.path/'source.docx',source=True)
        ix=next(b.index for b in s.blocks if b.images)
        p=figure_proposals(d,[s],{(s.hash,ix):['来源一致性待核对']})[0]
        self.assertEqual('pending',p['decision'])


class RealOrganizationFigureTest(unittest.TestCase):
    def test_xiaolan_target540_maps_only_current_source577(self):
        configured=os.environ.get('WORKBENCH_REAL_FIXTURES')
        base=Path(configured).expanduser().resolve() if configured else Path(__file__).resolve().parents[2]/'核查工作台_Codex交接包'
        base=base/'02_示例项目A'
        if not base.exists():self.skipTest('真实交接包未提供。')
        d=Document(base/'02_原始底稿'/'核查记录及分析文件-第一章.docx');s=Document(next((base/'01_正式来源').glob('2 *.docx')))
        p=next(p for p in figure_proposals(d,[s]) if p['start']==540)
        self.assertEqual('accept',p['decision']);self.assertEqual([577],p['candidates'][0]['indices'])
        c=p['candidates'][0]
        self.assertEqual('be78381d56dbb466313f5be22fa7378a5b90379818188bc631f42bde62043148',c['figure_evidence']['source_media'][0]['sha256'])
        self.assertEqual('word/media/image41.png',c['figure_evidence']['source_media'][0]['part'])
        self.assertEqual('rId60',c['figure_evidence']['source_media'][0]['relationship_id'])


if __name__=='__main__':unittest.main()

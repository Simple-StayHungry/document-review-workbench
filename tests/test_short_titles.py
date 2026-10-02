"""Circled labels are retained headings; complete circled clauses remain body."""
import copy
import tempfile
from pathlib import Path
from types import SimpleNamespace
import unittest
from xml.etree import ElementTree as E

from workbench.docxio import w
from workbench.model import Block,Unit,Document,heading_level,is_bold_numbered_title
from workbench.precision import augment_sources,is_short_numbered_title
from workbench.matching import Index


PATH=['（2）基金管理','1）科技金融业务板块']


def fixture(value='⑤记账方式',path=None,**flags):
    path=list(PATH if path is None else path)
    el=E.Element(w('p'));run=E.SubElement(el,w('r'));E.SubElement(run,w('t')).text=value
    block=Block(0,el,'paragraph',value,path=path,section='situ',**flags)
    source=SimpleNamespace(hash='current-source',name='当前募集说明书.docx',profile={'kind':'prospectus'},
                           blocks=[block],source_units=[],parents={})
    return source,Unit('target',0,1,[block],path[-1] if path else '',path,'1-1')


class ShortTitleTests(unittest.TestCase):
    def test_exact_label_is_not_a_copy_candidate_even_with_matching_parents(self):
        source,_=fixture();_,target=fixture(path=['二、核查情况']+PATH)
        augment_sources(source)
        self.assertEqual([],source.source_units)
        self.assertEqual([],Index([source]).candidates(target))

    def test_same_leaf_with_different_parent_has_no_candidate(self):
        source,_=fixture(path=['（2）融资租赁',PATH[-1]]);_,target=fixture()
        augment_sources(source)
        self.assertEqual([],Index([source]).candidates(target))

    def test_only_one_informative_parent_is_insufficient(self):
        source,_=fixture(path=['二、核查情况',PATH[-1]]);_,target=fixture(path=['二、核查情况',PATH[-1]])
        augment_sources(source)
        self.assertEqual([],Index([source]).candidates(target))

    def test_same_circled_number_with_different_title_never_fuzzy_matches(self):
        source,_=fixture('⑤记账方法');_,target=fixture()
        augment_sources(source)
        self.assertEqual([],Index([source]).candidates(target))

    def test_short_title_never_uses_long_neighboring_heading(self):
        source,_=fixture('④利益分配及退出方式');_,target=fixture()
        augment_sources(source)
        self.assertEqual([],Index([source]).candidates(target))

    def test_unmarked_short_text_does_not_gain_an_atom(self):
        for text in ('记账方式','⑤方法','⑤记账方式。'):
            with self.subTest(text=text):
                source,_=fixture(text);augment_sources(source);self.assertEqual([],source.source_units)

    def test_procedure_toc_images_and_complex_sources_are_excluded(self):
        for flag in ('proc','is_toc','images','protected','formula'):
            with self.subTest(flag=flag):
                source,_=fixture();block=source.blocks[0]
                if flag=='proc':block.section='proc'
                elif flag=='formula':E.SubElement(block.el,'{http://schemas.openxmlformats.org/officeDocument/2006/math}oMath')
                else:setattr(block,flag,True)
                augment_sources(source);self.assertEqual([],source.source_units)

    def test_source_warning_still_prevents_auto_copy(self):
        source,target=fixture();augment_sources(source)
        flags=[{'file_id':source.hash,'blocks':[0],'title':'来源一致性提示'}]
        self.assertEqual([],Index([source],flags).candidates(target))

    def test_guarantor_scope_guard_survives_matching_parent_suffix(self):
        source,_=fixture(path=['保证人基本情况']+PATH);_,target=fixture()
        augment_sources(source)
        self.assertEqual([],Index([source]).candidates(target))


# Verbatim labels and complete circled clauses from Xiaolan chapters 8 and 10.
REAL_TITLES=['①实现可行性分析','②无法控制因素分析','①环境效益定性分析','②环境效益定量分析']
FORMULA_TITLES=['分布式光伏项目二氧化碳当量减排量','储能项目二氧化碳减排量','虚拟电厂二氧化碳减排量','节能量']
REAL_BODY=[
    '①可测算：发行人可以通过光伏、储能、虚拟电厂建设竣工验收报告、并网验收意见或其他具有同等效力文件载明的装机规模信息进行统计测度，因此本次债券的关键绩效指标可以被客观计算与量化。',
    '②可比较：作为定量指标，可进行不同时期比较，亦可与同行业其他企业的同类指标进行比较，因此本次债券的关键绩效指标可以设置明确的基准值与目标值。',
    '③可验证：本次债券的关键绩效指标可被权威第三方机构事后校验和重复验算，具有可靠性。',
    '④可持续：指标体现了行业绿色低碳转型发展趋势，与发行人整体业务及战略规划紧密相关，可以体现发行人在一个时间阶段内的成效并具备可持续性。',
    '①季度财务报表、年度报告及半年度报告由财务部提交董事会及信息披露事务负责人审议；',
    '②以上报告审议后由办公室进行披露。',
    '①办公室负责关注、收集作为临时报告进行披露的有关信息，并编制临时报告草案；',
    '②办公室负责临时报告草案的审核流程，其中，以董事会名义发布的须提交董事长审核批准；涉及全资子公司、控股子公司、参股子公司的重大经营事项须先提交公司派出的该控股公司董事长或该参股公司董事审核批准，再提交公司董事长审核批准，并以公司名义发布。',
    '③以上审核流程完成后，由办公室提交信息披露事务负责人审核，审核通过后进行披露。',
]


class RealCircledHeadingTests(unittest.TestCase):
    def setUp(self):
        from test_engine_boundaries import make_docx,FACT,COMPANY
        from test_copy_retention import rewrite_document
        from test_source_coverage import paragraph
        from workbench.docxio import text
        from workbench.engine import Engine
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup);self.base=Path(tmp.name)
        self.files=[make_docx(self.base/'原始.docx'),make_docx(self.base/'当前募集说明书.docx',source=True)]
        for f in self.files:
            def edit(body,f=f):
                anchor=next(p for p in body if p.tag==w('p') and text(p)==FACT)
                at=list(body).index(anchor)
                values=REAL_BODY+[value for title in REAL_TITLES+FORMULA_TITLES for value in (title,FACT)]
                for i,value in enumerate(values):
                    p=paragraph(value,f['role']=='source' or value in FORMULA_TITLES)
                    if value in FORMULA_TITLES:
                        pr=E.Element(w('pPr'));p.insert(0,pr)
                        num=E.SubElement(pr,w('numPr'));E.SubElement(num,w('numId'),{w('val'):'63'})
                    body.insert(at+i,p)
            rewrite_document(f,edit)
        self.engine=Engine();self.result=self.engine.analyze(self.files,{'issuer':COMPANY,'author':'柒'})

    def test_four_actual_titles_retained_and_nine_complete_clauses_copied(self):
        from workbench.source_policy import inventory
        from workbench.docxio import resolve_revisions,structural_digest_content
        from test_copy_retention import read_root,paragraphs
        td=self.engine.load(self.files[0]);ps=self.result['proposals']
        objects={x['block']:x for x in inventory(td,ps)}
        for b in td.blocks:
            if b.text in REAL_TITLES:
                self.assertTrue(b.heading);self.assertTrue(is_short_numbered_title(b.text))
                self.assertFalse(any(p['start']<=b.index<p['end'] for p in ps))
                self.assertEqual('protected',objects[b.index]['status']);self.assertEqual('framework',objects[b.index]['content_class'])
            elif b.text in REAL_BODY:
                self.assertFalse(b.heading);self.assertFalse(is_short_numbered_title(b.text))
                self.assertTrue(any(p['start']<=b.index<p['end'] and p['decision'] in ('accept','same') for p in ps))
        out=self.base/'output';export=self.engine.export(self.result,self.files,out)
        self.assertTrue(export['checks'][0]['independent_output_audit']['passed'])
        root=read_root(out/export['checks'][0]['file'])
        for title in REAL_TITLES:
            kept=paragraphs(root,title)[0];self.assertIsNone(kept.find('.//'+w('ins')));self.assertIsNone(kept.find('.//'+w('b')))
        for body in REAL_BODY:
            pasted=paragraphs(root,body)[0];self.assertIsNotNone(pasted.find('.//'+w('ins')));self.assertIsNone(pasted.find('.//'+w('b')))
        resolve_revisions(root,False)
        self.assertEqual(structural_digest_content(read_root(self.files[0]['path'])),structural_digest_content(root))

    def test_stale_plan_cannot_copy_circled_target_or_source_heading(self):
        td,sd=map(self.engine.load,self.files)
        p=next(p for p in self.result['proposals'] if p['kind']=='material' and p['decision']=='accept')
        for side,title in ((side,title) for side in ('target','source') for title in (REAL_TITLES[0],FORMULA_TITLES[0])):
            with self.subTest(side=side,title=title):
                result=copy.deepcopy(self.result);changed=next(x for x in result['proposals'] if x['id']==p['id'])
                if side=='target':
                    i=next(b.index for b in td.blocks if b.text==title);changed.update(start=i,end=i+1)
                else:
                    c=changed['candidates'][changed['selected']];i=next(b.index for b in sd.blocks if b.text==title);c['indices']=[i]+c['indices']
                with self.assertRaisesRegex(ValueError,'标题'):
                    self.engine.export(result,self.files,self.base/('invalid-'+side))

    def test_bold_numbered_formula_names_are_retained_original_headings(self):
        from workbench.source_policy import inventory
        from test_copy_retention import read_root,paragraphs
        td=self.engine.load(self.files[0]);objects={x['block']:x for x in inventory(td,self.result['proposals'])}
        out=self.base/'formula-headings';export=self.engine.export(self.result,self.files,out)
        self.assertTrue(export['checks'][0]['independent_output_audit']['passed'])
        root=read_root(out/export['checks'][0]['file'])
        for b in td.blocks:
            if b.text not in FORMULA_TITLES:continue
            self.assertTrue(b.heading);self.assertEqual('protected',objects[b.index]['status'])
            self.assertEqual('framework',objects[b.index]['content_class'])
            self.assertFalse(any(p['start']<=b.index<p['end'] for p in self.result['proposals']))
            kept=paragraphs(root,b.text)[0]
            self.assertIsNone(kept.find('.//'+w('ins')));self.assertEqual(E.tostring(b.el),E.tostring(kept))

    def test_plain_or_nonbold_labels_and_complete_clauses_are_not_heading_candidates(self):
        from test_source_coverage import paragraph
        for value in FORMULA_TITLES+['图：发行人股权结构图','单位：万元','计算公式如下：','①公司已建立健全内控制度','①公司已完成环境效益分析']:
            self.assertFalse(is_short_numbered_title(value))
            self.assertIsNone(heading_level(value,paragraph(value),{}))
        for value,bold in [(value,False) for value in FORMULA_TITLES]+[(value,True) for value in ('发行人已建立健全内控制度。','发行人已建立健全内控制度','发行人负责项目二氧化碳减排量')]:
            p=paragraph(value,bold);pr=E.Element(w('pPr'));p.insert(0,pr)
            E.SubElement(E.SubElement(pr,w('numPr')),w('numId'),{w('val'):'63'})
            self.assertFalse(is_bold_numbered_title(value,p));self.assertIsNone(heading_level(value,p,{}))
        p=paragraph(FORMULA_TITLES[0],True);pr=E.Element(w('pPr'));p.insert(0,pr)
        E.SubElement(E.SubElement(pr,w('numPr')),w('numId'),{w('val'):'0'})
        self.assertFalse(is_bold_numbered_title(FORMULA_TITLES[0],p))
        self.assertIsNone(heading_level(FORMULA_TITLES[0],p,{}))


if __name__=='__main__':unittest.main()

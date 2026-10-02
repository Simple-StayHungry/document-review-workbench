"""Regression locks distilled from the v18.9.3 real workpaper output."""
import copy
import tempfile
import unittest
from pathlib import Path
from xml.etree import ElementTree as ET

from workbench.docxio import Package, w, text, resolve_revisions, validate_package, transform_paragraph
from workbench.engine import _replacement_format_templates, _paragraph_role
from workbench.output_audit import _audit_destination_layout, audit_docx, element_paths, revision_ids
from test_source_insertion_writer import make_package, paragraph


def p(value, *, align=None, first=None, line='360', size='21', bold=False):
    node=paragraph(value, bold=bold)
    ppr=ET.Element(w('pPr'))
    if align: ET.SubElement(ppr,w('jc'),{w('val'):align})
    if line: ET.SubElement(ppr,w('spacing'),{w('line'):line,w('lineRule'):'auto'})
    if first is not None: ET.SubElement(ppr,w('ind'),{w('firstLine'):str(first)})
    node.insert(0,ppr)
    run=node.find(w('r'));rpr=run.find(w('rPr'))
    if rpr is None:rpr=ET.Element(w('rPr'));run.insert(0,rpr)
    ET.SubElement(rpr,w('rFonts'),{w('eastAsia'):'宋体',w('ascii'):'Times New Roman',w('hAnsi'):'Times New Roman'})
    ET.SubElement(rpr,w('sz'),{w('val'):size});ET.SubElement(rpr,w('szCs'),{w('val'):size})
    return node


class GoldenLayoutRegressionTests(unittest.TestCase):
    def setUp(self):
        td=tempfile.TemporaryDirectory();self.addCleanup(td.cleanup);self.base=Path(td.name)

    def test_cover_date_redlines_only_changed_digits(self):
        date=p('',align='center',line=None,size='32',bold=True)
        # Mirror the real WPS cover: changed date tokens are already isolated runs.
        for child in list(date):
            if child.tag==w('r'):date.remove(child)
        fragments=['调查日期：【','202','6','】年','【','7','】月','【','1','7','】日']
        for value in fragments:
            run=ET.Element(w('r'));rpr=ET.SubElement(run,w('rPr'))
            ET.SubElement(rpr,w('rFonts'),{w('cs'):'Times New Roman'})
            ET.SubElement(rpr,w('b'));ET.SubElement(rpr,w('sz'),{w('val'):'32'});ET.SubElement(rpr,w('szCs'),{w('val'):'32'})
            t=ET.SubElement(run,w('t'));t.text=value;date.append(run)
        target=make_package(self.base/'date_target.docx',[date]);target.save(self.base/'date_target2.docx')
        pkg=Package(self.base/'date_target2.docx');old=pkg.body[0];old_ppr=ET.tostring(old.find(w('pPr')))
        newp=copy.deepcopy(old);transform_paragraph(newp,[(text(old),'调查日期：【2026】年【9】月【26】日')])
        inserted=pkg.replace([old],[newp],whole=False)
        self.assertIs(inserted[0],old)
        self.assertEqual(ET.tostring(old.find(w('pPr'))),old_ppr)
        deleted=''.join((x.text or '') for x in old.iter(w('delText')))
        inserted_text=''.join((x.text or '') for ins in old.iter(w('ins')) for x in ins.iter(w('t')))
        self.assertEqual(deleted,'717')
        self.assertEqual(inserted_text,'926')
        # The unchanged label/year/delimiters stay outside revision wrappers.
        plain=''.join((x.text or '') for x in old.findall('./'+w('r')+'/'+w('t')))
        self.assertIn('调查日期：【',plain);self.assertIn('202',plain);self.assertIn('6',plain)
        rejected=copy.deepcopy(pkg.root);resolve_revisions(rejected,False)
        accepted=copy.deepcopy(pkg.root);resolve_revisions(accepted,True)
        self.assertIn('调查日期：【2026】年【7】月【17】日',text(rejected))
        self.assertIn('调查日期：【2026】年【9】月【26】日',text(accepted))

    def test_chinese_month_cover_redlines_only_changed_month_run(self):
        date=p('',align='center',line=None,size='32',bold=True)
        for child in list(date):
            if child.tag==w('r'):date.remove(child)
        fragments=['调查日期：二零二六年','七','月']
        for value in fragments:
            run=ET.Element(w('r'));rpr=ET.SubElement(run,w('rPr'))
            ET.SubElement(rpr,w('rFonts'),{w('eastAsia'):'宋体',w('ascii'):'Times New Roman',w('hAnsi'):'Times New Roman'})
            ET.SubElement(rpr,w('b'));ET.SubElement(rpr,w('sz'),{w('val'):'32'});ET.SubElement(rpr,w('szCs'),{w('val'):'32'})
            t=ET.SubElement(run,w('t'));t.text=value;date.append(run)
        target=make_package(self.base/'month_target.docx',[date]);target.save(self.base/'month_target2.docx')
        pkg=Package(self.base/'month_target2.docx');old=pkg.body[0];old_ppr=ET.tostring(old.find(w('pPr')))
        newp=copy.deepcopy(old);transform_paragraph(newp,[(text(old),'调查日期：二零二六年九月')])
        inserted=pkg.replace([old],[newp],whole=False)
        self.assertIs(inserted[0],old)
        self.assertEqual(ET.tostring(old.find(w('pPr'))),old_ppr)
        deleted=''.join((x.text or '') for x in old.iter(w('delText')))
        inserted_text=''.join((x.text or '') for ins in old.iter(w('ins')) for x in ins.iter(w('t')))
        self.assertEqual(deleted,'七')
        self.assertEqual(inserted_text,'九')
        plain=''.join((x.text or '') for x in old.findall('./'+w('r')+'/'+w('t')))
        self.assertIn('调查日期：二零二六年',plain);self.assertIn('月',plain)
        rejected=copy.deepcopy(pkg.root);resolve_revisions(rejected,False)
        accepted=copy.deepcopy(pkg.root);resolve_revisions(accepted,True)
        self.assertIn('调查日期：二零二六年七月',text(rejected))
        self.assertIn('调查日期：二零二六年九月',text(accepted))

    def test_cover_date_edits_inside_same_paragraph_and_keeps_shell(self):
        date=p('调查日期：二零二六年七月',align='center',line=None,size='32',bold=True)
        target=make_package(self.base/'target.docx',[p('封面',align='center'),date,p('正文',first='480')])
        target.save(self.base/'baseline.docx');pkg=Package(self.base/'baseline.docx')
        old=next(x for x in pkg.body if x.tag==w('p') and text(x).startswith('调查日期：'))
        old_ppr=ET.tostring(old.find(w('pPr')));body_count=len(pkg.body)
        new=copy.deepcopy(old);transform_paragraph(new,[(text(old),'调查日期：二零二六年九月')])
        pkg.replace([old],[new],whole=False)
        self.assertEqual(len(pkg.body),body_count)
        self.assertEqual(ET.tostring(old.find(w('pPr'))),old_ppr)
        rejected=copy.deepcopy(pkg.root);resolve_revisions(rejected,False)
        accepted=copy.deepcopy(pkg.root);resolve_revisions(accepted,True)
        self.assertIn('调查日期：二零二六年七月',text(rejected))
        self.assertIn('调查日期：二零二六年九月',text(accepted))
        out=self.base/'out.docx';pkg.save(out)
        self.assertEqual(validate_package(out),[])

    def test_count_shift_maps_unit_and_caption_only_to_same_role(self):
        old=[p('原正文',first='480'),p('单位：万元、%',align='right',first=None),p('表：原表',align='center',first=None)]
        new=[p('新正文一',first='999'),p('新正文二',first='999'),p('单位：万元、%',align='left'),p('表：新表',align='left')]
        templates=_replacement_format_templates(old,new)
        self.assertEqual([_paragraph_role(x) if x is not None else None for x in templates],['body','body','unit','table_title'])
        self.assertEqual(text(templates[2]),'单位：万元、%')
        self.assertEqual(templates[2].find('./'+w('pPr')+'/'+w('jc')).get(w('val')),'right')
        self.assertEqual(templates[3].find('./'+w('pPr')+'/'+w('jc')).get(w('val')),'center')

    def test_existing_one_to_one_refresh_keeps_target_shell_even_if_text_role_changes(self):
        # Existing-content refreshes are positional.  A formal source sentence may
        # lexically resemble a unit/caption/heading, but the target paragraph owns
        # the workpaper layout.  New insertions are governed separately by the
        # same-role insertion template logic.
        old=p('普通正文',align='both',first='480',line='360')
        templates=_replacement_format_templates([old],[p('单位：万元、%',align='right',first=None,line='240')])
        self.assertEqual(len(templates),1)
        self.assertEqual(templates[0].find('./'+w('pPr')+'/'+w('jc')).get(w('val')),'both')
        self.assertEqual(templates[0].find('./'+w('pPr')+'/'+w('ind')).get(w('firstLine')),'480')
        self.assertEqual(templates[0].find('./'+w('pPr')+'/'+w('spacing')).get(w('line')),'360')

    def test_existing_body_keeps_target_shell_even_if_source_alignment_is_foreign(self):
        old=p('原正文内容',align='both',first='480',line='360')
        new=p('更新后的正文内容',align='center',first='999',line='720')
        templates=_replacement_format_templates([old],[new])
        self.assertEqual(len(templates),1)
        self.assertEqual(templates[0].find('./'+w('pPr')+'/'+w('jc')).get(w('val')),'both')
        self.assertEqual(templates[0].find('./'+w('pPr')+'/'+w('ind')).get(w('firstLine')),'480')
        self.assertEqual(templates[0].find('./'+w('pPr')+'/'+w('spacing')).get(w('line')),'360')

    def test_count_shift_can_recover_exact_shell_from_frozen_document(self):
        doc=[p('前部正文',first='420'),p('3、毛利率情况',align='left',first=None),p('后部正文',first='480')]
        old=[p('',first=None)]
        new=[p('新增说明',first='999'),p('3、毛利率情况',align='center',first='999')]
        templates=_replacement_format_templates(old,new,document_elements=doc,anchor_index=1)
        self.assertEqual(text(templates[1]),'3、毛利率情况')
        self.assertEqual(templates[1].find('./'+w('pPr')+'/'+w('jc')).get(w('val')),'left')
        self.assertIsNone(templates[1].find('./'+w('pPr')+'/'+w('ind')))

    def test_count_shift_preserves_identical_paragraph_own_shell(self):
        # Real chapter 8 has long source ranges where an extra paragraph shifts the
        # count. Text-identical target paragraphs must keep their own exact shell,
        # not the nearest body's pStyle/alignment/indent.
        old=[p('前文',first='480'),p('n：年份，单位：年；',first='480'),p('分布式光伏项目二氧化碳当量减排量',align='left',first=None)]
        # Make each shell observably different.
        old[0].find('./'+w('pPr')+'/'+w('ind')).set(w('firstLine'),'600')
        new=[p('新增说明',first='999'),p('前文',first='999'),p('n：年份，单位：年；',first=None),p('分布式光伏项目二氧化碳当量减排量',align='center',first='999')]
        templates=_replacement_format_templates(old,new)
        self.assertEqual(text(templates[1]),'前文')
        self.assertEqual(templates[1].find('./'+w('pPr')+'/'+w('ind')).get(w('firstLine')),'600')
        self.assertEqual(text(templates[2]),'n：年份，单位：年；')
        self.assertEqual(templates[2].find('./'+w('pPr')+'/'+w('ind')).get(w('firstLine')),'480')
        self.assertEqual(text(templates[3]),'分布式光伏项目二氧化碳当量减排量')
        self.assertEqual(templates[3].find('./'+w('pPr')+'/'+w('jc')).get(w('val')),'left')

    def test_explicit_none_disables_positional_format_fallback(self):
        target=make_package(self.base/'target2.docx',[p('原正文',align='center',first='480')])
        source=make_package(self.base/'source.docx',[p('单位：万元、%',align='right',first=None,size='24')])
        target.save(self.base/'target2b.docx');target=Package(self.base/'target2b.docx')
        source.save(self.base/'sourceb.docx');source=Package(self.base/'sourceb.docx')
        old=target.body[0];src=source.body[0]
        inserted=target.replace([old],[src],source=source,whole=True,format_templates=[None])
        accepted=copy.deepcopy(inserted[0]);resolve_revisions(accepted,True)
        self.assertEqual(accepted.find('./'+w('pPr')+'/'+w('jc')).get(w('val')),'right')
        fonts=accepted.find('./'+w('r')+'/'+w('rPr')+'/'+w('rFonts'))
        self.assertEqual(fonts.get(w('eastAsia')),'宋体')
        self.assertEqual(fonts.get(w('ascii')),'Times New Roman')
        self.assertEqual(accepted.find('./'+w('r')+'/'+w('rPr')+'/'+w('sz')).get(w('val')),'24')

    def test_target_selective_bold_overrides_all_bold_source(self):
        target=p('普通说明。')
        # Add a second, selectively bold target run.
        r2=ET.Element(w('r'));rpr2=ET.SubElement(r2,w('rPr'))
        ET.SubElement(rpr2,w('b'));ET.SubElement(rpr2,w('sz'),{w('val'):'21'})
        t2=ET.SubElement(r2,w('t'));t2.text='需要重点提示。';target.append(r2)
        source=copy.deepcopy(target)
        # Deliberately corrupt the source so every run is bold, mirroring the
        # v19.8 regression seen in the real ninth-chapter output.
        for run in source.findall(w('r')):
            rpr=run.find(w('rPr'))
            if rpr is None:rpr=ET.Element(w('rPr'));run.insert(0,rpr)
            if rpr.find(w('b')) is None:ET.SubElement(rpr,w('b'))
        pkg=make_package(self.base/'bold_target.docx',[target]);pkg.save(self.base/'bold_target2.docx')
        pkg=Package(self.base/'bold_target2.docx')
        srcpkg=make_package(self.base/'bold_source.docx',[source]);srcpkg.save(self.base/'bold_source2.docx');srcpkg=Package(self.base/'bold_source2.docx')
        inserted=pkg.replace([pkg.body[0]],[srcpkg.body[0]],source=srcpkg,whole=True,format_templates=[pkg.body[0]])
        accepted=copy.deepcopy(inserted[0]);resolve_revisions(accepted,True)
        runs=[r for r in accepted.findall(w('r')) if text(r)]
        self.assertGreaterEqual(len(runs),2)
        self.assertIsNone(runs[0].find('./'+w('rPr')+'/'+w('b')))
        self.assertIsNotNone(runs[-1].find('./'+w('rPr')+'/'+w('b')))


    def test_real_export_audit_passes_when_source_text_looks_like_different_role(self):
        # Regression for the v19.12 real-project failure: the source wording itself
        # looked like a unit/caption, so role classification abandoned the target
        # shell and the independent output audit stopped export. Existing content
        # must keep the target shell regardless of the source text's lexical role.
        target_path=self.base/'role_target.docx';source_path=self.base/'role_source.docx';out=self.base/'role_out.docx'
        target_pkg=make_package(target_path,[p('原正文',align='both',first='480',line='360')]);target_pkg.save(target_path)
        source_pkg=make_package(source_path,[p('单位：万元、%',align='right',first=None,line='240',size='24')]);source_pkg.save(source_path)
        pkg=Package(target_path);src=Package(source_path)
        old=list(pkg.body)[:1];new=list(src.body)[:1]
        baseline=Package(target_path);target_paths=element_paths(baseline.root,list(baseline.body)[:1])
        templates=_replacement_format_templates(old,new)
        inserted=pkg.replace(old,new,source=src,whole=True,format_templates=templates);pkg.save(out)
        rec={'id':'a'*64,'action':'source_copy','source_file':str(source_path),'source_sha256':src.hash,'source_role':'source',
             'source_paths':element_paths(src.root,new),'target_sha256':Package(target_path).hash,'target_paths':target_paths,
             'deletion_paths':element_paths(pkg.root,old),'output_paths':element_paths(pkg.root,inserted),
             'insertion_revision_ids':revision_ids(inserted,'ins'),'deletion_revision_ids':revision_ids(old,'del'),
             'transforms':['destination_format','technical_ids','table_layout']}
        report=audit_docx(out,target_path,[rec])
        self.assertTrue(report['passed'],report['errors'])

    def test_independent_audit_rejects_paragraph_geometry_drift(self):
        old=p('普通正文',align='both',first='480',line='360')
        new=p('更新正文',align='center',first='999',line='720')
        with self.assertRaisesRegex(ValueError,'段落版式'):
            _audit_destination_layout([old],[new])

    def test_independent_audit_rejects_source_emphasis_leak(self):
        old=p('普通正文',align='both',first='480',line='360',bold=False)
        new=p('更新正文',align='both',first='480',line='360',bold=True)
        with self.assertRaisesRegex(ValueError,'加粗'):
            _audit_destination_layout([old],[new])

    def test_independent_audit_allows_archive_font_pair_change_only(self):
        old=p('普通正文',align='both',first='480',line='360',size='21')
        new=copy.deepcopy(old)
        run=new.find(w('r'));rpr=run.find(w('rPr'));fonts=rpr.find(w('rFonts'))
        fonts.set(w('eastAsia'),'宋体');fonts.set(w('ascii'),'Times New Roman');fonts.set(w('hAnsi'),'Times New Roman')
        _audit_destination_layout([old],[new])

    def test_plain_target_strips_source_bold(self):
        target=p('普通正文',first='480')
        source=p('更新后的普通正文',first='999',bold=True)
        pkg=make_package(self.base/'plain_target.docx',[target]);pkg.save(self.base/'plain_target2.docx');pkg=Package(self.base/'plain_target2.docx')
        srcpkg=make_package(self.base/'plain_source.docx',[source]);srcpkg.save(self.base/'plain_source2.docx');srcpkg=Package(self.base/'plain_source2.docx')
        inserted=pkg.replace([pkg.body[0]],[srcpkg.body[0]],source=srcpkg,whole=True,format_templates=[pkg.body[0]])
        accepted=copy.deepcopy(inserted[0]);resolve_revisions(accepted,True)
        for run in accepted.findall(w('r')):
            if text(run):self.assertIsNone(run.find('./'+w('rPr')+'/'+w('b')))

if __name__=='__main__':unittest.main()

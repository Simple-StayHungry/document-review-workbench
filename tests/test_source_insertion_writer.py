"""Pure source additions must survive Accept and disappear completely on Reject."""
import copy
import tempfile
import unittest
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

from workbench.docxio import (Package, W, R, PR, w, text, resolve_revisions,
                              structural_digest, validate_package)


def paragraph(value, bold=False):
    node = ET.Element(w('p'))
    run = ET.SubElement(node, w('r'))
    if bold:
        ET.SubElement(ET.SubElement(run, w('rPr')), w('b'))
    ET.SubElement(run, w('t')).text = value
    return node


def table(value, width=12000, floating=False):
    node = ET.Element(w('tbl'))
    pr = ET.SubElement(node, w('tblPr'))
    if floating:
        ET.SubElement(pr, w('tblpPr'), {w('vertAnchor'): 'page'})
    ET.SubElement(pr, w('tblW'), {w('type'): 'dxa', w('w'): str(width)})
    grid = ET.SubElement(node, w('tblGrid'))
    ET.SubElement(grid, w('gridCol'), {w('w'): str(width)})
    row = ET.SubElement(node, w('tr'))
    cell = ET.SubElement(row, w('tc'))
    cell_pr = ET.SubElement(cell, w('tcPr'))
    ET.SubElement(cell_pr, w('tcW'), {w('type'): 'dxa', w('w'): str(width)})
    cell.append(paragraph(value))
    return node


def make_package(path, blocks):
    root = ET.Element(w('document'))
    body = ET.SubElement(root, w('body'))
    body.extend(blocks)
    section = ET.SubElement(body, w('sectPr'))
    ET.SubElement(section, w('pgSz'), {w('w'): '12000', w('h'): '16000'})
    ET.SubElement(section, w('pgMar'), {w('left'): '2000', w('right'): '2000'})
    with zipfile.ZipFile(path, 'w') as z:
        z.writestr('[Content_Types].xml', '''<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
            <Default Extension="xml" ContentType="application/xml"/>
            <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
            <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
            </Types>''')
        z.writestr('word/document.xml', ET.tostring(root))
        z.writestr('_rels/.rels', f'<Relationships xmlns="{PR}"><Relationship Id="rId1" Type="{R}/officeDocument" Target="word/document.xml"/></Relationships>')
        z.writestr('word/_rels/document.xml.rels', f'<Relationships xmlns="{PR}"/>')
    return Package(path)


class SourceInsertionWriterTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name)
        self.target = make_package(self.base / 'target.docx', [paragraph('（一）原标题'), paragraph('原有科目内容'), paragraph('（二）下个科目')])
        # Establish the same technical paragraph identities that a normal export
        # baseline has, before proving exact Reject-All restoration.
        self.target.save(self.base / 'baseline.docx')
        self.target = Package(self.base / 'baseline.docx')
        self.source = make_package(self.base / 'source.docx', [paragraph('来源新增段落', True), paragraph('第二个新增段落')])

    def test_after_anchor_is_pure_insertion_and_reject_restores(self):
        baseline = copy.deepcopy(self.target.root)
        anchor = self.target.body[1]
        original_anchor = ET.tostring(anchor)
        inserted = self.target.insert_source(anchor, list(self.source.body)[:2], source=self.source)
        self.assertEqual(ET.tostring(anchor), original_anchor)
        self.assertEqual([text(n) for n in self.target.body[:-1]], ['（一）原标题', '原有科目内容', '来源新增段落', '第二个新增段落', '（二）下个科目'])
        self.assertIsNotNone(inserted[0].find('.//' + w('b')))
        self.assertTrue(all(n.find('.//' + w('ins')) is not None for n in inserted))
        self.assertEqual(list(self.target.root.iter(w('del'))), [])
        self.target.save(self.base / 'output.docx')
        self.assertEqual(validate_package(self.base / 'output.docx'), [])
        result = Package(self.base / 'output.docx')
        resolve_revisions(result.root, False)
        self.assertEqual(structural_digest(result.root), structural_digest(baseline))

    def test_before_anchor_and_accept_keeps_source_order(self):
        inserted = self.target.insert_source(self.target.body[2], list(self.source.body)[:2], before=True, source=self.source)
        resolve_revisions(self.target.root, True)
        self.assertEqual([text(n) for n in self.target.body[:-1]], ['（一）原标题', '原有科目内容', '来源新增段落', '第二个新增段落', '（二）下个科目'])
        self.assertEqual([text(n) for n in inserted], ['来源新增段落', '第二个新增段落'])

    def test_added_table_fits_section_and_remains_reversible(self):
        original = copy.deepcopy(self.target.root)
        source_table = table('新表数据', width=7000)
        self.source.body.insert(1, source_table)
        inserted = self.target.insert_source(self.target.body[1], [self.source.body[0], source_table], source=self.source)
        width = inserted[1].find('./' + w('tblGrid') + '/' + w('gridCol')).get(w('w'))
        self.assertEqual(width, '7000')
        self.assertIsNotNone(inserted[1].find('./' + w('tr') + '/' + w('trPr') + '/' + w('ins')))
        reject = copy.deepcopy(self.target.root)
        resolve_revisions(reject, False)
        self.assertEqual(structural_digest(reject), structural_digest(original))
        accept = copy.deepcopy(self.target.root)
        resolve_revisions(accept, True)
        self.assertIn('新表数据', text(accept))

    def test_wide_added_table_is_pasted_complete_without_geometry_changes(self):
        source_table = table('超宽来源表', width=12000)
        row=source_table.find(w('tr'));rpr=ET.Element(w('trPr'));row.insert(0,rpr)
        ET.SubElement(rpr,w('trHeight'),{w('val'):'500',w('hRule'):'exact'})
        inserted=self.target.insert_source(self.target.body[1], [source_table], source=self.source)
        got=inserted[0]
        self.assertEqual(got.find('./'+w('tblGrid')+'/'+w('gridCol')).get(w('w')), '12000')
        self.assertEqual(got.find('./'+w('tblPr')+'/'+w('tblW')).get(w('w')), '12000')
        h=got.find('.//'+w('trHeight'));self.assertEqual((h.get(w('val')),h.get(w('hRule'))),('500','exact'))
        self.assertIsNone(got.find('.//'+w('cantSplit')))
        self.assertEqual(source_table.find('./'+w('tblGrid')+'/'+w('gridCol')).get(w('w')), '12000')

    def test_floating_table_fails_before_body_mutation(self):
        before = ET.tostring(self.target.root)
        with self.assertRaisesRegex(ValueError, '浮动表'):
            self.target.insert_source(self.target.body[1], [self.source.body[0], table('浮动', floating=True)], source=self.source)
        self.assertEqual(before, ET.tostring(self.target.root))

    def test_after_section_break_is_rejected_but_before_is_allowed(self):
        anchor = self.target.body[1]
        pr = ET.Element(w('pPr'))
        pr.append(copy.deepcopy(self.target.body[-1]))
        anchor.insert(0, pr)
        with self.assertRaisesRegex(ValueError, '节分界'):
            self.target.insert_source(anchor, [self.source.body[0]], source=self.source)
        self.assertEqual(len(self.target.insert_source(anchor, [self.source.body[0]], before=True, source=self.source)), 1)

    def test_missing_source_and_nonbody_anchor_are_rejected(self):
        with self.assertRaisesRegex(ValueError, '正式来源'):
            self.target.insert_source(self.target.body[1], [self.source.body[0]])
        with self.assertRaisesRegex(ValueError, '原始正文'):
            self.target.insert_source(self.target.body[1][0], [self.source.body[0]], source=self.source)

    def test_source_block_is_cloned_without_mutating_original(self):
        source_node = self.source.body[0]
        original = ET.tostring(source_node)
        self.target.insert_source(self.target.body[1], [source_node], source=self.source)
        self.assertEqual(original, ET.tostring(source_node))

    def test_insert_source_matches_destination_format_not_source_format(self):
        anchor = self.target.body[1]
        # Destination body template: first-line indent + 10.5pt. Paragraph layout must follow it.
        ppr = ET.Element(w('pPr')); ET.SubElement(ppr, w('ind'), {w('firstLine'): '420'}); ET.SubElement(ppr, w('spacing'), {w('line'): '360', w('lineRule'): 'auto'})
        anchor.insert(0, ppr)
        tr = anchor.find(w('r')); trpr = ET.Element(w('rPr'))
        ET.SubElement(trpr, w('rFonts'), {w('eastAsia'): '仿宋', w('ascii'): 'Times New Roman', w('hAnsi'): 'Times New Roman'})
        ET.SubElement(trpr, w('sz'), {w('val'): '21'}); ET.SubElement(trpr, w('szCs'), {w('val'): '21'})
        tr.insert(0, trpr)
        # Source deliberately carries visibly wrong direct formatting.
        src = self.source.body[0]
        sr = src.find(w('r')); srpr = sr.find(w('rPr'))
        if srpr is None: srpr = ET.Element(w('rPr')); sr.insert(0, srpr)
        ET.SubElement(srpr, w('rFonts'), {w('eastAsia'): '黑体', w('ascii'): 'Arial'})
        ET.SubElement(srpr, w('sz'), {w('val'): '40'})
        inserted = self.target.insert_source(anchor, [src], source=self.source, format_templates=[anchor])
        accepted = copy.deepcopy(inserted[0]); resolve_revisions(accepted, True)
        self.assertEqual(accepted.find('./'+w('pPr')+'/'+w('ind')).get(w('firstLine')), '420')
        run = accepted.find(w('r')); fonts = run.find('./'+w('rPr')+'/'+w('rFonts'))
        self.assertEqual(fonts.get(w('eastAsia')), '宋体')
        self.assertEqual(fonts.get(w('ascii')), 'Times New Roman')
        self.assertEqual(fonts.get(w('hAnsi')), 'Times New Roman')
        self.assertEqual(run.find('./'+w('rPr')+'/'+w('sz')).get(w('val')), '21')
        # Source emphasis is formatting too; it must not override a plain target
        # paragraph.  The workpaper, not the prospectus, owns the visible style.
        self.assertIsNone(run.find('./'+w('rPr')+'/'+w('b')))



    def test_destination_style_inheritance_drops_source_visual_shell_when_target_has_no_direct_rpr(self):
        anchor = self.target.body[1]
        # Target paragraph deliberately has no direct run properties.  Its visible
        # size/colour therefore comes from the target document style hierarchy.
        ppr = ET.Element(w('pPr'))
        ET.SubElement(ppr, w('spacing'), {w('before'): '0', w('after'): '0', w('line'): '360', w('lineRule'): 'auto'})
        ET.SubElement(ppr, w('ind'), {w('firstLine'): '480'})
        anchor.insert(0, ppr)
        src = self.source.body[0]
        run = src.find(w('r'))
        srpr = run.find(w('rPr'))
        if srpr is None:
            srpr = ET.Element(w('rPr')); run.insert(0, srpr)
        ET.SubElement(srpr, w('rFonts'), {w('eastAsia'): '黑体', w('ascii'): 'Arial', w('hAnsi'): 'Arial'})
        ET.SubElement(srpr, w('sz'), {w('val'): '40'})
        ET.SubElement(srpr, w('color'), {w('val'): 'FF0000'})
        ET.SubElement(srpr, w('lang'), {w('val'): 'en-US'})
        inserted = self.target.insert_source(anchor, [src], source=self.source, format_templates=[anchor])
        accepted = copy.deepcopy(inserted[0]); resolve_revisions(accepted, True)
        out_ppr = accepted.find(w('pPr'))
        self.assertEqual(out_ppr.find(w('spacing')).attrib, ppr.find(w('spacing')).attrib)
        self.assertEqual(out_ppr.find(w('ind')).attrib, ppr.find(w('ind')).attrib)
        outrpr = accepted.find('./'+w('r')+'/'+w('rPr'))
        fonts = outrpr.find(w('rFonts'))
        self.assertEqual(fonts.get(w('eastAsia')), '宋体')
        self.assertEqual(fonts.get(w('ascii')), 'Times New Roman')
        self.assertEqual(fonts.get(w('hAnsi')), 'Times New Roman')
        # Source-only direct visuals must not survive when the destination relies
        # on its own style hierarchy.
        self.assertIsNone(outrpr.find(w('sz')))
        self.assertIsNone(outrpr.find(w('color')))
        self.assertIsNone(outrpr.find(w('lang')))

    def test_destination_projection_does_not_mutate_unrelated_workpaper_blocks(self):
        anchor = self.target.body[1]
        before_first = ET.tostring(self.target.body[0])
        before_anchor = ET.tostring(anchor)
        before_next = ET.tostring(self.target.body[2])
        ppr = ET.Element(w('pPr'))
        ET.SubElement(ppr, w('jc'), {w('val'): 'both'})
        ET.SubElement(ppr, w('ind'), {w('firstLine'): '420', w('left'): '0', w('right'): '0'})
        ET.SubElement(ppr, w('spacing'), {w('before'): '0', w('after'): '0', w('line'): '360', w('lineRule'): 'auto'})
        anchor.insert(0, ppr)
        # Refresh the byte snapshot after establishing the destination template.
        before_anchor = ET.tostring(anchor)
        inserted = self.target.insert_source(anchor, [self.source.body[0]], source=self.source, format_templates=[anchor])
        accepted = copy.deepcopy(inserted[0]); resolve_revisions(accepted, True)
        out_ppr = accepted.find(w('pPr'))
        self.assertEqual(out_ppr.find(w('jc')).get(w('val')), 'both')
        self.assertEqual(out_ppr.find(w('ind')).attrib, ppr.find(w('ind')).attrib)
        self.assertEqual(out_ppr.find(w('spacing')).attrib, ppr.find(w('spacing')).attrib)
        self.assertEqual(ET.tostring(self.target.body[0]), before_first)
        self.assertEqual(ET.tostring(anchor), before_anchor)
        self.assertEqual(ET.tostring(self.target.body[3]), before_next)

    def test_table_is_copied_without_destination_text_projection(self):
        source_table = table('来源表格', width=7000)
        srun = source_table.find('.//' + w('r'))
        srpr = ET.SubElement(srun, w('rPr'))
        ET.SubElement(srpr, w('rFonts'), {w('eastAsia'): '黑体', w('ascii'): 'Arial', w('hAnsi'): 'Arial'})
        ET.SubElement(srpr, w('sz'), {w('val'): '24'})
        spar = source_table.find('.//' + w('p'))
        sppr = ET.Element(w('pPr')); ET.SubElement(sppr, w('spacing'), {w('before'): '120', w('after'): '180', w('line'): '300'})
        spar.insert(0, sppr)
        target_table = table('目标表格')
        trun = target_table.find('.//' + w('r'))
        trpr = ET.SubElement(trun, w('rPr'))
        ET.SubElement(trpr, w('rFonts'), {w('eastAsia'): '宋体', w('ascii'): 'Times New Roman'})
        ET.SubElement(trpr, w('sz'), {w('val'): '18'})
        inserted = self.target.insert_source(self.target.body[1], [source_table], source=self.source, format_templates=[target_table])
        accepted = copy.deepcopy(inserted[0]); resolve_revisions(accepted, True)
        fonts = accepted.find('.//' + w('rPr') + '/' + w('rFonts'))
        spacing = accepted.find('.//' + w('pPr') + '/' + w('spacing'))
        self.assertEqual(fonts.get(w('eastAsia')), '黑体')
        self.assertEqual(fonts.get(w('ascii')), 'Arial')
        self.assertEqual(accepted.find('.//' + w('rPr') + '/' + w('sz')).get(w('val')), '24')
        self.assertEqual(spacing.get(w('before')), '120')
        self.assertEqual(spacing.get(w('after')), '180')

    def test_whole_replacement_uses_old_paragraph_format(self):
        old = self.target.body[1]
        ppr = ET.Element(w('pPr')); ET.SubElement(ppr, w('jc'), {w('val'): 'both'}); ET.SubElement(ppr, w('ind'), {w('firstLine'): '420'})
        old.insert(0, ppr)
        tr = old.find(w('r')); trpr = ET.Element(w('rPr')); ET.SubElement(trpr, w('rFonts'), {w('eastAsia'): '宋体'}); ET.SubElement(trpr, w('sz'), {w('val'): '21'}); tr.insert(0, trpr)
        src = self.source.body[1]
        sr = src.find(w('r')); srpr = ET.Element(w('rPr')); ET.SubElement(srpr, w('rFonts'), {w('eastAsia'): '黑体'}); ET.SubElement(srpr, w('sz'), {w('val'): '36'}); sr.insert(0, srpr)
        inserted = self.target.replace([old], [src], source=self.source, whole=True)
        accepted = copy.deepcopy(inserted[0]); resolve_revisions(accepted, True)
        self.assertEqual(accepted.find('./'+w('pPr')+'/'+w('jc')).get(w('val')), 'both')
        self.assertEqual(accepted.find('./'+w('pPr')+'/'+w('ind')).get(w('firstLine')), '420')
        run = accepted.find(w('r')); self.assertEqual(run.find('./'+w('rPr')+'/'+w('rFonts')).get(w('eastAsia')), '宋体')
        self.assertEqual(run.find('./'+w('rPr')+'/'+w('sz')).get(w('val')), '21')
        self.assertEqual(text(accepted), '第二个新增段落')

    def test_save_repairs_sequences_and_validator_rejects_unrepaired_file(self):
        run = self.target.body[1].find(w('r'))
        pr = ET.Element(w('rPr'))
        ET.SubElement(pr, w('rFonts'), {w('eastAsia'): '宋体'})
        ET.SubElement(pr, w('rStyle'), {w('val'): 'Example'})
        run.insert(0, pr)
        bad_file = self.base / 'unrepaired.docx'
        # Simulate the old writer, which serialized a malformed sequence.
        entries = dict(self.target.entries)
        entries['word/document.xml'] = ET.tostring(self.target.root)
        with zipfile.ZipFile(bad_file, 'w') as z:
            for key, value in entries.items(): z.writestr(key, value)
        self.assertTrue(any('属性子元素顺序' in issue for issue in validate_package(bad_file)))
        self.target.save(self.base / 'repaired.docx')
        self.assertEqual(validate_package(self.base / 'repaired.docx'), [])
        self.assertEqual(self.target.compatibility_fixes[0]['by_property'], {'rPr': 1})
        repaired = Package(self.base / 'repaired.docx')
        self.assertEqual(structural_digest(repaired.root), structural_digest(self.target.root))

    def test_save_also_checks_note_parts_without_rebuilding_content(self):
        raw = f'''<w:footnotes xmlns:w="{W}"><w:footnote w:id="1"><w:p><w:r>
            <w:rPr><w:rFonts w:eastAsia="宋体"/><w:rStyle w:val="FootnoteText"/></w:rPr>
            <w:t>真实脚注内容</w:t></w:r></w:p></w:footnote></w:footnotes>'''.encode()
        self.target.entries['word/footnotes.xml'] = raw
        self.target.save(self.base / 'notes.docx')
        self.assertEqual(self.target.compatibility_fixes[0]['part'], 'word/footnotes.xml')
        with zipfile.ZipFile(self.base / 'notes.docx') as z:
            note = ET.fromstring(z.read('word/footnotes.xml'))
        self.assertEqual(text(note), '真实脚注内容')
        self.assertEqual([n.tag for n in note.find('.//' + w('rPr'))], [w('rStyle'), w('rFonts')])


if __name__ == '__main__':
    unittest.main()

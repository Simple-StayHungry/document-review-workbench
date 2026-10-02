import copy
import unittest
from xml.etree import ElementTree as ET

from workbench.compatibility import (W, W14, normalize_property_order,
                                     ordered_property_children, property_order_issues,
                                     enforce_style_capacity)


def parse(body):
    return ET.fromstring(f'<w:document xmlns:w="{W}" xmlns:w14="{W14}">{body}</w:document>')


def locals_of(node):
    return [n.tag.rsplit('}', 1)[-1] for n in node]


class CompatibilityTests(unittest.TestCase):
    def test_reproduces_table_geometry_append_failure_without_changing_values(self):
        root = parse('''<w:tbl><w:tblPr><w:tblBorders><w:top w:val="single"/></w:tblBorders>
            <w:tblCellMar/><w:tblLook w:val="04A0"/><w:tblW w:w="8500" w:type="dxa"/>
            <w:jc w:val="center"/></w:tblPr><w:tblGrid/></w:tbl>''')
        properties = root[0][0]
        before = {n.tag: ET.tostring(n) for n in properties}
        self.assertTrue(property_order_issues(root))
        result = normalize_property_order(root)
        self.assertEqual(result['changed'], 1)
        self.assertEqual(result['issues'], [])
        self.assertEqual(locals_of(properties), ['tblW', 'jc', 'tblBorders', 'tblCellMar', 'tblLook'])
        self.assertEqual(before, {n.tag: ET.tostring(n) for n in properties})

    def test_reproduces_font_override_before_character_style(self):
        root = parse('''<w:p><w:r><w:rPr><w:rFonts w:eastAsia="宋体"/>
            <w:rStyle w:val="wb_Emphasis"/><w:b/><w:sz w:val="21"/></w:rPr>
            <w:t>公司发行债券</w:t></w:r></w:p>''')
        old_text = ''.join(root.itertext())
        normalize_property_order(root)
        self.assertEqual(locals_of(root[0][0][0]), ['rStyle', 'rFonts', 'b', 'sz'])
        self.assertEqual(''.join(root.itertext()), old_text)

    def test_paragraph_mark_revision_remains_before_fonts(self):
        root = parse('''<w:p><w:pPr><w:rPr><w:rFonts w:eastAsia="宋体"/>
            <w:ins w:id="7" w:author="分析人员"/><w:rStyle w:val="Emphasis"/>
            </w:rPr></w:pPr><w:ins w:id="8" w:author="分析人员"><w:r>
            <w:t>正文</w:t></w:r></w:ins></w:p>''')
        revisions = [ET.tostring(n) for n in root.iter('{' + W + '}ins')]
        normalize_property_order(root)
        self.assertEqual(locals_of(root[0][0][0]), ['ins', 'rStyle', 'rFonts'])
        self.assertEqual(revisions, [ET.tostring(n) for n in root.iter('{' + W + '}ins')])

    def test_row_choice_group_keeps_original_order(self):
        root = parse('''<w:trPr><w:trHeight w:val="123"/><w:tblHeader/><w:cantSplit/>
            <w:gridBefore w:val="1"/><w:ins w:id="1"/></w:trPr>''')
        before = ET.tostring(root)
        self.assertEqual(normalize_property_order(root)['changed'], 0)
        self.assertEqual(before, ET.tostring(root))

    def test_row_revision_moves_after_all_row_properties(self):
        root = parse('<w:trPr><w:ins w:id="1"/><w:trHeight w:val="123"/><w:cantSplit/></w:trPr>')
        self.assertTrue(property_order_issues(root))
        normalize_property_order(root)
        self.assertEqual(locals_of(root[0]), ['trHeight', 'cantSplit', 'ins'])

    def test_alternating_header_footer_references_are_legal(self):
        root = parse('''<w:sectPr><w:headerReference w:type="default"/>
            <w:footerReference w:type="default"/><w:headerReference w:type="first"/>
            <w:footerReference w:type="first"/><w:pgSz w:w="11906"/></w:sectPr>''')
        before = ET.tostring(root)
        self.assertEqual(normalize_property_order(root), {'changed': 0, 'by_property': {}, 'issues': []})
        self.assertEqual(before, ET.tostring(root))

    def test_unknown_extensions_are_not_silently_relocated(self):
        root = parse('''<w:rPr><w:rFonts/><custom:effect xmlns:custom="urn:foreign"/>
            <w:rStyle w:val="Style"/></w:rPr>''')
        before = ET.tostring(root)
        result = normalize_property_order(root)
        self.assertEqual(result['changed'], 0)
        self.assertTrue(result['issues'])
        self.assertEqual(before, ET.tostring(root))

    def test_duplicate_single_properties_are_preserved_and_flagged(self):
        root = parse('<w:tblPr><w:tblW w:w="100"/><w:tblW w:w="200"/></w:tblPr>')
        result = normalize_property_order(root)
        self.assertTrue(any('重复' in issue for issue in result['issues']))
        self.assertEqual(len(root[0]), 2)

    def test_comparison_view_is_nonmutating_and_preserves_values(self):
        root = parse('<w:rPr><w:rFonts w:eastAsia="宋体"/><w:rStyle w:val="A"/></w:rPr>')
        before = ET.tostring(root)
        ordered = ordered_property_children(root[0])
        self.assertEqual(locals_of(ordered), ['rStyle', 'rFonts'])
        self.assertEqual(before, ET.tostring(root))
        different = copy.deepcopy(root)
        different[0][1].set('{' + W + '}val', 'B')
        self.assertNotEqual([ET.tostring(n) for n in ordered],
                            [ET.tostring(n) for n in ordered_property_children(different[0])])

    def test_idempotent_and_does_not_reorder_content(self):
        root = parse('''<w:p><w:del w:id="1"><w:r><w:t>旧</w:t></w:r></w:del>
            <w:ins w:id="2"><w:r><w:rPr><w:rFonts/><w:rStyle w:val="A"/></w:rPr>
            <w:t>新</w:t></w:r></w:ins></w:p><w:p><w:r><w:t>后文</w:t></w:r></w:p>''')
        normalize_property_order(root)
        before = ET.tostring(root)
        self.assertEqual(normalize_property_order(root)['changed'], 0)
        self.assertEqual(before, ET.tostring(root))
        self.assertEqual(locals_of(root[0]), ['del', 'ins'])


class StyleCapacityTests(unittest.TestCase):
    def package(self, styles, body='', extra=None):
        parts = {'word/styles.xml': ET.fromstring(f'<w:styles xmlns:w="{W}">{styles}</w:styles>'),
                 'word/document.xml': parse(body)}
        parts.update(extra or {})
        return parts

    def style(self, sid, content='', default=False):
        flag = ' w:default="1"' if default else ''
        return f'<w:style w:styleId="{sid}" w:type="paragraph"{flag}>{content}</w:style>'

    def ids(self, parts):
        return [x.get('{' + W + '}styleId') for x in parts['word/styles.xml']]

    def test_default_direct_and_transitive_dependencies_survive_minimal_trim(self):
        styles = self.style('Default', default=True)
        styles += self.style('Used', '<w:basedOn w:val="Base"/><w:next w:val="Next"/><w:link w:val="Link"/>')
        styles += self.style('Base', '<w:basedOn w:val="Ancestor"/>')
        styles += self.style('Ancestor') + self.style('Next') + self.style('Link')
        styles += self.style('Unused1') + self.style('Unused2')
        parts = self.package(styles, '<w:p><w:pPr><w:pStyle w:val="Used"/></w:pPr><w:r><w:t>正文</w:t></w:r></w:p>')
        before = ET.tostring(parts['word/document.xml'])
        result = enforce_style_capacity(parts, limit=7)
        self.assertEqual(result['after']['word/styles.xml'], 7)
        self.assertEqual([r['style_id'] for r in result['removed']], ['Unused1'])
        self.assertEqual(ET.tostring(parts['word/document.xml']), before)
        self.assertTrue({'Default', 'Used', 'Base', 'Ancestor', 'Next', 'Link'} <= set(self.ids(parts)))

    def test_header_simple_field_alias_and_split_deleted_complex_field_protected(self):
        styles = self.style('A', '<w:name w:val="Header Name"/><w:aliases w:val="Header Alias"/>')
        styles += self.style('B', '<w:name w:val="Split Name"/>')
        styles += self.style('Unused1') + self.style('Unused2')
        header = parse('<w:fldSimple w:instr=" STYLEREF &quot;Header Alias&quot; \\l "/>' )
        body = '<w:p><w:r><w:fldChar w:fldCharType="begin"/></w:r><w:r><w:delInstrText> STYLE</w:delInstrText></w:r><w:r><w:instrText>REF "Split Name" \\l </w:instrText></w:r><w:r><w:fldChar w:fldCharType="end"/></w:r></w:p>'
        parts = self.package(styles, body, {'word/header1.xml': header})
        enforce_style_capacity(parts, limit=2)
        self.assertEqual(self.ids(parts), ['A', 'B'])

    def test_unused_incoming_dependency_is_not_left_dangling(self):
        styles = self.style('Base') + self.style('UnusedChild', '<w:basedOn w:val="Base"/>') + self.style('UnusedLeaf')
        parts = self.package(styles)
        result = enforce_style_capacity(parts, limit=2)
        self.assertEqual(result['removed'][0]['style_id'], 'UnusedChild')
        self.assertIn('Base', self.ids(parts))

    def test_numbering_custom_xml_and_style_fields_root_definitions(self):
        styles = self.style('N') + self.style('X') + self.style('T', '<w:name w:val="Table Listing"/>') + self.style('Unused')
        extra = {'word/numbering.xml': parse('<w:styleLink w:val="N"/>'),
                 'customXml/item1.xml': ET.fromstring('<item customStyle="X"/>')}
        parts = self.package(styles, '<w:fldSimple w:instr="TOC \\t &quot;Table Listing,1&quot;"/>', extra)
        enforce_style_capacity(parts, limit=3)
        self.assertEqual(self.ids(parts), ['N', 'X', 'T'])

    def test_under_limit_is_byte_preserving_and_idempotent(self):
        parts = self.package(self.style('A') + self.style('Unused'))
        before = {k: ET.tostring(v) for k, v in parts.items()}
        self.assertEqual(enforce_style_capacity(parts, limit=2)['removed'], [])
        self.assertEqual(before, {k: ET.tostring(v) for k, v in parts.items()})

    def test_unknown_style_field_and_no_safe_capacity_fail_before_mutation(self):
        for body in ['<w:fldSimple w:instr="STYLEREF &quot;Missing Name&quot;"/>',
                     '<w:p><w:r><w:instrText>STYLEREF "A"</w:instrText></w:r></w:p>']:
            parts = self.package(self.style('A') + self.style('B'), body)
            before = ET.tostring(parts['word/styles.xml'])
            with self.assertRaises(ValueError):
                enforce_style_capacity(parts, limit=1)
            self.assertEqual(before, ET.tostring(parts['word/styles.xml']))
        parts = self.package(self.style('A', '<w:link w:val="B"/>') + self.style('B', '<w:link w:val="A"/>'))
        with self.assertRaises(ValueError):
            enforce_style_capacity(parts, limit=1)

    def test_styles_with_effects_keeps_identical_removed_ids(self):
        styles = self.style('A', default=True) + self.style('Unused')
        parts = self.package(styles)
        parts['word/stylesWithEffects.xml'] = copy.deepcopy(parts['word/styles.xml'])
        report = enforce_style_capacity(parts, limit=1)
        self.assertEqual(report['after'], {'word/styles.xml': 1, 'word/stylesWithEffects.xml': 1})
        self.assertEqual(ET.tostring(parts['word/styles.xml']), ET.tostring(parts['word/stylesWithEffects.xml']))

    def test_unreachable_linked_pair_is_removed_together(self):
        styles = self.style('Default', default=True)
        styles += self.style('A', '<w:link w:val="B"/>') + self.style('B', '<w:link w:val="A"/>')
        parts = self.package(styles)
        result = enforce_style_capacity(parts, limit=1)
        self.assertEqual({r['style_id'] for r in result['removed']}, {'A', 'B'})
        self.assertEqual(self.ids(parts), ['Default'])

    def test_incoming_unused_style_keeps_linked_pair_until_whole_group_safe(self):
        styles = self.style('A', '<w:link w:val="B"/>') + self.style('B', '<w:link w:val="A"/>')
        styles += self.style('Referencer', '<w:basedOn w:val="A"/>')
        parts = self.package(styles)
        result = enforce_style_capacity(parts, limit=2)
        self.assertEqual([r['style_id'] for r in result['removed']], ['Referencer'])
        self.assertEqual(self.ids(parts), ['A', 'B'])

    def test_exact_removal_selects_pair_instead_of_splitting_larger_cycle(self):
        styles = self.style('Keep', default=True)
        styles += self.style('A', '<w:link w:val="B"/>') + self.style('B', '<w:link w:val="C"/>') + self.style('C', '<w:link w:val="A"/>')
        styles += self.style('X', '<w:link w:val="Y"/>') + self.style('Y', '<w:link w:val="X"/>')
        parts = self.package(styles)
        result = enforce_style_capacity(parts, limit=4)
        self.assertEqual({r['style_id'] for r in result['removed']}, {'X', 'Y'})

    def test_numeric_revision_ids_are_not_style_references_in_fallback(self):
        styles = self.style('1', default=True)
        styles += self.style('2', '<w:link w:val="3"/>') + self.style('3', '<w:link w:val="2"/>')
        body = '<w:p><w:del w:id="2"><w:r><w:t>旧文</w:t></w:r></w:del><w:ins w:id="3"><w:r><w:t>新文</w:t></w:r></w:ins></w:p>'
        parts = self.package(styles, body)
        before = ET.tostring(parts['word/document.xml'])
        result = enforce_style_capacity(parts, limit=1)
        self.assertEqual({r['style_id'] for r in result['removed']}, {'2', '3'})
        self.assertEqual(ET.tostring(parts['word/document.xml']), before)
        self.assertEqual(result['reference_mode'], 'semantic_word_refs_and_conservative_extensions')


if __name__ == '__main__':
    unittest.main()

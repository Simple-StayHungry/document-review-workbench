import copy
import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from xml.etree import ElementTree as E

from workbench.layout_policy import q, table_layout_risks, preserved_native_table_layout


def table(grid, values, spans=None, size=21, margin=None):
    t = E.Element(q('tbl')); g = E.SubElement(t, q('tblGrid'))
    for width in grid:
        E.SubElement(g, q('gridCol'), {q('w'): str(width)})
    row = E.SubElement(t, q('tr'))
    for i, value in enumerate(values):
        cell = E.SubElement(row, q('tc')); cp = E.SubElement(cell, q('tcPr'))
        if spans and spans[i] != 1:
            E.SubElement(cp, q('gridSpan'), {q('val'): str(spans[i])})
        if margin is not None:
            cm = E.SubElement(cp, q('tcMar'))
            for side in ('left', 'right'):
                E.SubElement(cm, q(side), {q('type'): 'dxa', q('w'): str(margin)})
        p = E.SubElement(cell, q('p')); r = E.SubElement(p, q('r'))
        if size is not None:
            E.SubElement(E.SubElement(r, q('rPr')), q('sz'), {q('val'): str(size)})
        E.SubElement(r, q('t')).text = value
    return t


def fixture(old, source, width):
    root = E.Element(q('document')); body = E.SubElement(root, q('body')); body.append(old)
    sect = E.SubElement(body, q('sectPr'))
    E.SubElement(sect, q('pgSz'), {q('w'): str(width + 2000), q('h'): '16838'})
    E.SubElement(sect, q('pgMar'), {q('left'): '1000', q('right'): '1000'})
    target = SimpleNamespace(root=root, entries={}); src = SimpleNamespace(entries={})
    return target, [old], src, [source]


def fixed_geometry(t, floating=False):
    pr = E.Element(q('tblPr')); t.insert(0, pr)
    total = sum(int(c.get(q('w'))) for c in t.find(q('tblGrid')))
    E.SubElement(pr, q('tblW'), {q('type'): 'dxa', q('w'): str(total)})
    E.SubElement(pr, q('tblLayout'), {q('type'): 'fixed'})
    if floating:
        E.SubElement(pr, q('tblpPr'), {q('horzAnchor'): 'margin', q('tblpXSpec'): 'center',
                     q('vertAnchor'): 'text', q('tblpY'): '289', q('leftFromText'): '180', q('rightFromText'): '180'})
    else:
        E.SubElement(pr, q('jc'), {q('val'): 'center'})
    return t


class LayoutPolicyTests(unittest.TestCase):
    def test_wide_source_table_is_allowed_without_silent_shrink(self):
        args = fixture(table([1600, 1600], ['12', '34']), table([1600, 1600], ['12', '34']), 2200)
        before = E.tostring(args[0].root), E.tostring(args[3][0])
        self.assertEqual(table_layout_risks(*args), [])
        self.assertEqual((E.tostring(args[0].root), E.tostring(args[3][0])), before)
        self.assertEqual([int(c.get(q('w'))) for c in args[3][0].find(q('tblGrid'))], [1600,1600])

    def test_no_shrink_does_not_require_font_estimation(self):
        args = fixture(table([1600, 1600], ['1', '2']), table([1600, 1600], ['250,401.38', '2026-09-22'], size=None), 4000)
        self.assertEqual(table_layout_risks(*args), [])

    def test_merged_table_keeps_complete_source_geometry(self):
        args = fixture(table([1000, 1000], ['123456.78'], spans=[2]), table([1000, 1000], ['123456.78'], spans=[2]), 1400)
        self.assertEqual(table_layout_risks(*args), [])
        self.assertEqual([int(c.get(q('w'))) for c in args[3][0].find(q('tblGrid'))], [1000,1000])
        self.assertEqual(args[3][0].find('.//'+q('gridSpan')).get(q('val')), '2')

    def test_same_columns_do_not_borrow_old_grid(self):
        args = fixture(table([1400, 1600], ['25,532.23', '1']), table([2400, 600], ['25,532.23', '1']), 2200)
        self.assertEqual(table_layout_risks(*args), [])
        self.assertEqual([int(c.get(q('w'))) for c in args[3][0].find(q('tblGrid'))], [2400,600])

    def test_cell_margins_do_not_trigger_geometry_rewrite(self):
        args = fixture(table([1600, 1600], ['1234', '1']), table([1600, 1600], ['1234', '1'], margin=400), 2200)
        self.assertEqual(table_layout_risks(*args), [])
        cm=args[3][0].find('.//'+q('tcMar'))
        self.assertEqual(cm.find(q('left')).get(q('w')), '400')
        self.assertEqual(cm.find(q('right')).get(q('w')), '400')

    def test_unknown_font_size_is_irrelevant_to_complete_table_copy(self):
        args = fixture(table([1600, 1600], ['1', '2']), table([1600, 1600], ['250,401.38', '1'], size=None), 2200)
        self.assertEqual(table_layout_risks(*args), [])

    def test_table_text_tokens_never_drive_column_resizing(self):
        args = fixture(table([1600, 1600], ['2026-09-22', '1']), table([1600, 1600], ['2026-09-22', '1']), 1900)
        self.assertEqual(table_layout_risks(*args), [])
        self.assertEqual([int(c.get(q('w'))) for c in args[3][0].find(q('tblGrid'))], [1600,1600])

    def test_identical_centered_native_grid_can_use_safe_paper_margins(self):
        args = fixture(fixed_geometry(table([1400, 1600], ['25,532.23', '1'])),
                       fixed_geometry(table([1400, 1600], ['25,532.23', '1'])), 2200)
        evidence = preserved_native_table_layout(args[0].root, args[1][0], args[3][0])
        self.assertEqual(evidence['table_left_twips'], 600)
        self.assertEqual(table_layout_risks(*args), [])

    def test_identical_floating_geometry_includes_text_clearance(self):
        args = fixture(fixed_geometry(table([1400, 1600], ['25,532.23', '1']), True),
                       fixed_geometry(table([1400, 1600], ['25,532.23', '1']), True), 2200)
        self.assertIsNone(preserved_native_table_layout(args[0].root, args[1][0], args[3][0]))
        self.assertTrue(preserved_native_table_layout(args[0].root, args[1][0], args[3][0], allow_floating=True)['floating'])
        for t in (args[1][0], args[3][0]):
            t.find('./' + q('tblPr') + '/' + q('tblpPr')).set(q('leftFromText'), '300')
        self.assertIsNone(preserved_native_table_layout(args[0].root, args[1][0], args[3][0], allow_floating=True))
        self.assertTrue(table_layout_risks(*args))

    def test_changed_grid_cannot_borrow_original_margin_exception(self):
        args = fixture(fixed_geometry(table([1400, 1600], ['25,532.23', '1'])),
                       fixed_geometry(table([1500, 1500], ['25,532.23', '1'])), 2200)
        self.assertIsNone(preserved_native_table_layout(args[0].root, args[1][0], args[3][0]))
        # Direct paste no longer needs the old/native-grid exception.
        self.assertEqual(table_layout_risks(*args), [])

    def test_short_floating_tables_cannot_bypass_anchor_gate(self):
        for anchor, with_paragraph, width in [('text', False, 2200), ('text', False, 4000),
                                               ('page', True, 2200), ('margin', True, 2200)]:
            with self.subTest(anchor=anchor, with_paragraph=with_paragraph, width=width):
                args = fixture(fixed_geometry(table([1400, 1600], ['1', '2']), True),
                               fixed_geometry(table([1400, 1600], ['1', '2']), True), width)
                old, source = args[1][0], args[3][0]
                for t in (old, source):
                    t.find('./' + q('tblPr') + '/' + q('tblpPr')).set(q('vertAnchor'), anchor)
                if with_paragraph:
                    for elements in (args[1], args[3]):
                        p = E.Element(q('p')); E.SubElement(E.SubElement(p, q('r')), q('t')).text = '单位：万元'
                        elements.insert(0, p)
                    args[0].root.find(q('body')).insert(0, args[1][0])
                    self.assertIsNone(preserved_native_table_layout(args[0].root, old, source, allow_floating=True))
                before = E.tostring(args[0].root)
                risks = table_layout_risks(*args)
                self.assertEqual(risks[0]['status'], 'layout_pending')
                self.assertIn('浮动表', risks[0]['reason'])
                self.assertEqual(E.tostring(args[0].root), before)

    def test_native_table_too_close_to_paper_edge_stays_pending(self):
        args = fixture(fixed_geometry(table([1800, 1800], ['250,401.38', '1'])),
                       fixed_geometry(table([1800, 1800], ['250,401.38', '1'])), 2200)
        self.assertIsNone(preserved_native_table_layout(args[0].root, args[1][0], args[3][0]))
        self.assertEqual(table_layout_risks(*args), [])

    def test_multicolumn_and_nested_context_cannot_use_paper_margin_exception(self):
        args = fixture(fixed_geometry(table([1400, 1600], ['25,532.23', '1'])),
                       fixed_geometry(table([1400, 1600], ['25,532.23', '1'])), 2200)
        section = args[0].root.find('./' + q('body') + '/' + q('sectPr'))
        cols = E.SubElement(section, q('cols'), {q('num'): '2'})
        self.assertIsNone(preserved_native_table_layout(args[0].root, args[1][0], args[3][0]))
        section.remove(cols)
        args[1][0].find('.//' + q('tc')).append(table([1000], ['1']))
        self.assertIsNone(preserved_native_table_layout(args[0].root, args[1][0], args[3][0]))

    def test_percentage_keeps_source_grid_and_reports_rounding_honestly(self):
        args = fixture(fixed_geometry(table([1400, 1600], ['25,532.23', '1'])),
                       fixed_geometry(table([1399, 1599], ['25,532.23', '1'])), 2200)
        for t in (args[1][0], args[3][0]):
            t.find('./' + q('tblPr') + '/' + q('tblW')).attrib.update({q('type'): 'pct', q('w'): '6000'})
        evidence = preserved_native_table_layout(args[0].root, args[1][0], args[3][0])
        self.assertTrue(evidence['copy_source_grid'])
        self.assertEqual(evidence['grid_width_twips'], 2998)
        self.assertEqual(evidence['preferred_width_twips'], 2640)
        self.assertEqual(evidence['source_grid_rounding_differences_twips'], [-1, -1])
        self.assertEqual(table_layout_risks(*args), [])

    def test_percentage_change_or_non_rounding_grid_difference_not_allowed(self):
        args = fixture(fixed_geometry(table([1400, 1600], ['25,532.23', '1'])),
                       fixed_geometry(table([1397, 1600], ['25,532.23', '1'])), 2200)
        for t in (args[1][0], args[3][0]):
            t.find('./' + q('tblPr') + '/' + q('tblW')).attrib.update({q('type'): 'pct', q('w'): '6000'})
        self.assertIsNone(preserved_native_table_layout(args[0].root, args[1][0], args[3][0]))
        args[3][0].find(q('tblGrid'))[0].set(q('w'), '1400')
        args[3][0].find('./' + q('tblPr') + '/' + q('tblW')).set(q('w'), '6001')
        self.assertIsNone(preserved_native_table_layout(args[0].root, args[1][0], args[3][0]))

    def test_percentage_preferred_extent_must_also_clear_paper_edge(self):
        args = fixture(fixed_geometry(table([1400, 1600], ['25,532.23', '1'])),
                       fixed_geometry(table([1400, 1600], ['25,532.23', '1'])), 2200)
        for t in (args[1][0], args[3][0]):
            t.find('./' + q('tblPr') + '/' + q('tblW')).attrib.update({q('type'): 'pct', q('w'): '9000'})
        self.assertIsNone(preserved_native_table_layout(args[0].root, args[1][0], args[3][0]))

    def test_real_second_chapter_scope214_uses_identical_native_geometry(self):
        validation = Path(__file__).resolve().parents[2] / '验证' / 'packaged_final' / 'xiaolan'
        if not (validation/'analysis.json').is_file():
            self.skipTest('real handoff corpus is not beside this program checkout')
        from workbench.engine import Engine
        from workbench.plans import freeze
        analysis = json.loads((validation/'analysis.json').read_text())
        files = json.loads((validation/'inputs.json').read_text())
        for record in files:
            record['id'] = record.get('id', record.get('sha256'))
        proposal = copy.deepcopy(next(p for p in analysis['proposals'] if p['id'].endswith(':scope214') and p['target_name'].startswith('第二章')))
        proposal.update(decision='accept', status='auto', selected=0)
        engine = Engine()
        freeze(engine, proposal, files)
        self.assertEqual(proposal['decision'], 'accept')
        self.assertEqual(proposal['selected'], 0)
        candidate = proposal['candidates'][0]
        self.assertEqual(candidate['blocked_reason'], '')
        self.assertTrue(candidate.get('planned_text'))
        self.assertTrue(candidate.get('replacement_digest'))
        self.assertEqual(candidate['layout_risks'], [])


if __name__ == '__main__':
    unittest.main()

"""Missing sibling items are discovered only; the detector never writes anything.

Stage one of the missing-item work deliberately adds nothing to the existing
synchronisation path.  ``detect_missing_items`` receives only reading structures
and returns brand new dictionaries, so it cannot mutate proposals by construction.
"""
import copy
import tempfile
import unittest
from pathlib import Path
from xml.etree import ElementTree as E

from workbench.docxio import w
from workbench.model import Document
from workbench.section_items import detect_missing_items
from test_engine_boundaries import COMPANY, make_docx
from test_copy_retention import rewrite_document
from test_source_coverage import paragraph

RISK = '1、财务风险'
ITEM_A = '报告期内投资活动现金流净额持续为负的风险'
ITEM_B = '报告期内筹资活动现金流净额波动较大的风险'
ITEM_C = '经营活动现金流净额有所下滑的风险'
ITEM_D = '应收账款回收的风险'
BODY_A = '2024-2025年度及2026年1-3月，发行人投资活动产生的现金流量净额分别为-98,000.00万元、-88,000.00万元和-16,000.00万元，持续为负且规模较大，对发行人资金安排形成一定压力。'
BODY_B = '近两年及一期，发行人筹资活动现金流量净额分别为9,007.75万元、-56,000.00万元和12,000.00万元，波动幅度较大，主要受借款取得与偿还节奏变化影响。'
BODY_C = '2024-2025年度及2026年1-3月，公司经营活动产生的现金流量净额分别为19,698.50万元、7,679.25万元和2,257.92万元，呈逐年下降趋势，需要关注经营性现金流的持续性。'
BODY_D = '截至2024-2025年末以及2026年3月末，发行人应收账款账面价值分别为22,146.46万元、13,050.33万元和13,305.15万元，占总资产比例分别为4.29%、2.58%和2.48%，回收情况需要持续关注。'


class MissingItemDetectorTests(unittest.TestCase):
    def setUp(self):
        t = tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        self.base = Path(t.name)

    def build(self, name, source, blocks):
        record = make_docx(self.base / name, source=source)
        values = [COMPANY, '2026年公司债券', '募集说明书' if source else '核查记录及分析文件']
        values.append('第一章 发行人基本情况' if source else '1-1-1 基本情况核查记录')
        values.extend(blocks)

        def rewrite(body):
            sect = body.find(w('sectPr'))
            body[:] = [copy.deepcopy(paragraph(v)) for v in values]
            if sect is not None:
                body.append(sect)
        rewrite_document(record, rewrite)
        return Document(record['path'])

    def target_with_triple(self, extra=()):
        blocks = ['二、核查情况', RISK,
                  '（1）' + ITEM_A, BODY_A,
                  '（2）' + ITEM_B, BODY_B,
                  '（3）' + ITEM_C, BODY_C,
                  '2、经营风险', '发行人主营业务覆盖多个板块，经营情况与区域经济发展水平密切相关，业务开展保持稳定。']
        blocks.extend(extra)
        return self.build('核查记录.docx', False, blocks)

    def source_with_quadruple(self):
        return self.build('募集说明书.docx', True, [
            '（一）财务风险',
            '1、' + ITEM_A, BODY_A,
            '2、' + ITEM_B, BODY_B,
            '3、' + ITEM_C, BODY_C,
            '4、' + ITEM_D, BODY_D,
            '（二）经营风险', '发行人主营业务覆盖多个板块，经营情况与区域经济发展水平密切相关，业务开展保持稳定。'])

    def props(self, td, sd, value):
        b = next(b for b in td.blocks if b.text == value)
        s = next(b for b in sd.blocks if b.text == value)
        return {'id': td.hash + ':' + str(b.index), 'target_id': td.hash, 'start': b.index, 'end': b.index + 1,
                'kind': 'material', 'decision': 'accept', 'selected': 0,
                'candidates': [{'doc_hash': sd.hash, 'start': s.index, 'end': s.index + 1, 'indices': [s.index], 'text': value}]}

    def test_trailing_sibling_missing_in_target_is_reported(self):
        td = self.target_with_triple()
        sd = self.source_with_quadruple()
        found = detect_missing_items(td, [sd])
        self.assertEqual(1, len(found))
        row = found[0]
        self.assertEqual('4、' + ITEM_D, row['source_item_heading'])
        self.assertEqual(RISK, row['target_parent_heading'])
        self.assertEqual('（3）' + ITEM_C, row['previous_sibling_heading'])
        self.assertEqual('2、经营风险', row['next_boundary_heading'])
        self.assertEqual([BODY_D], [sd.blocks[i].text for i in range(*row['source_body_range'])])
        self.assertTrue(row['candidate_id'])
        self.assertTrue(row['previous_sibling_fingerprint'])
        self.assertTrue(row['next_boundary_fingerprint'])

    def test_duplicate_body_in_another_section_does_not_hide_the_missing_item(self):
        """A repeated body elsewhere in the target is evidence, never existence."""
        td = self.target_with_triple(extra=['三、其他事项', BODY_D])
        sd = self.source_with_quadruple()
        found = detect_missing_items(td, [sd])
        self.assertEqual(1, len(found))
        self.assertEqual('4、' + ITEM_D, found[0]['source_item_heading'])

    def test_detector_cannot_touch_proposals_or_the_document(self):
        td = self.target_with_triple()
        sd = self.source_with_quadruple()
        ps = [self.props(td, sd, v) for v in (BODY_A, BODY_B, BODY_C)]
        frozen = copy.deepcopy(ps)
        shape = [(b.index, b.heading, b.level, b.text) for b in td.blocks]
        detect_missing_items(td, [sd])
        self.assertEqual(frozen, ps)
        self.assertEqual(shape, [(b.index, b.heading, b.level, b.text) for b in td.blocks])

    def test_item_present_in_the_same_parent_is_not_reported(self):
        td = self.build('核查记录.docx', False, [
            '二、核查情况', '（一）财务风险',
            '1、' + ITEM_A, BODY_A,
            '2、' + ITEM_B, BODY_B,
            '3、' + ITEM_C, BODY_C,
            '4、' + ITEM_D, BODY_D])
        sd = self.source_with_quadruple()
        self.assertEqual([], detect_missing_items(td, [sd]))

    def test_parent_is_chosen_by_child_overlap_not_by_title_alone(self):
        """Two target headings share the key; only the one with real overlap wins."""
        td = self.build('核查记录.docx', False, [
            '二、核查情况',
            '1、财务风险', '（1）与本次缺项无关的一项内容', '该处在以前版本中另有独立安排，内容与本次对照的风险清单并不相同，保持原样。',
            '三、其他事项', '1、财务风险',
            '（1）' + ITEM_A, BODY_A,
            '（2）' + ITEM_B, BODY_B,
            '（3）' + ITEM_C, BODY_C,
            '2、经营风险', '发行人主营业务覆盖多个板块，经营情况与区域经济发展水平密切相关，业务开展保持稳定。'])
        sd = self.source_with_quadruple()
        found = detect_missing_items(td, [sd])
        self.assertEqual(1, len(found))
        self.assertEqual(3, found[0]['matched_sibling_count'])
        self.assertEqual('（3）' + ITEM_C, found[0]['previous_sibling_heading'])

    def test_unrelated_source_section_without_overlap_is_not_paired(self):
        td = self.build('核查记录.docx', False, [
            '二、核查情况', '1、财务风险',
            '（1）与本次缺项无关的一项内容', '该处在以前版本中另有独立安排，内容与本次对照的风险清单并不相同，保持原样。'])
        sd = self.source_with_quadruple()
        self.assertEqual([], detect_missing_items(td, [sd]))


if __name__ == '__main__':
    unittest.main()

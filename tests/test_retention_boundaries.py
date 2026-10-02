"""Distinguish unrelated short retrieval leads from actual copy vetoes."""
import copy
from types import SimpleNamespace
import unittest
from xml.etree import ElementTree as ET

from workbench.docxio import w
from workbench.matching import Index
from workbench.model import Block, Unit
from workbench.source_policy import retain_unmatched


OLD = (
    '2025年9月6日，测试证券股份有限公司收到监管机构出具的警示函，原因是在一项企业首次公开发行项目中，'
    '未充分识别销售费用内部控制不规范的情形，在审核回复中发表的意见与企业实际情况不符。'
    '对此，测试证券股份有限公司认真吸取教训，持续规范尽职调查程序，加强合规风控培训，完善业务复核机制，'
    '并组织专项整改与责任追究，提升投资银行业务执业质量。'
)
CONTACT = (
    '名称：测试证券股份有限公司。住所：测试市测试路。法定代表人：张三。有关经办人员：李四、王五。'
    '联系地址：测试市中心区。电话号码：010-12345678。'
)


def unit(identity, value):
    paragraph = ET.Element(w('p'))
    ET.SubElement(ET.SubElement(paragraph, w('r')), w('t')).text = value
    block = Block(0, paragraph, 'paragraph', value, path=['主承销商机构信息'], section='situ')
    return Unit(identity, 0, 1, [block], block.path[-1], block.path, '')


def short_unrelated_proposal():
    source = SimpleNamespace(hash='current-source', name='当前募集说明书.docx',
                             profile={'kind': 'prospectus'}, source_units=[unit('contact', CONTACT)])
    status, candidates, reason = Index([source]).assess(unit('regulatory-record', OLD))
    return {'status': status, 'decision': 'pending', 'reason': reason,
            'old_text': OLD, 'has_table': False, 'kind': 'material', 'selected': None,
            'content_class': 'source_missing_or_ambiguous', 'candidates': candidates}


class RetentionBoundaryTests(unittest.TestCase):
    def test_index_length_veto_for_unrelated_contact_becomes_retention(self):
        proposal = short_unrelated_proposal()
        self.assertEqual('review', proposal['status'])
        self.assertEqual('来源明显短于目标，可能遗漏原段落中的事实或子事项。', proposal['reason'])
        self.assertTrue(proposal['candidates'])
        self.assertTrue(all(c['score'] < .52 for c in proposal['candidates']))
        self.assertEqual(CONTACT, proposal['candidates'][0]['text'])
        before = copy.deepcopy(proposal)
        retain_unmatched(proposal)
        self.assertEqual(('missing', 'keep', 'no_correspondence_retained'),
                         (proposal['status'], proposal['decision'], proposal['content_class']))
        self.assertEqual([], proposal['candidates'])
        self.assertEqual(OLD, proposal['old_text'])
        self.assertEqual(before['reason'], proposal['correspondence_search']['reason'])
        self.assertEqual(before['candidates'][0]['score'], proposal['correspondence_search']['candidates'][0]['score'])
        retained = copy.deepcopy(proposal)
        retain_unmatched(proposal)
        self.assertEqual(retained, proposal)

    def test_concrete_coverage_and_source_risks_do_not_become_retention(self):
        mutations = [
            ('credible candidate', lambda p: p['candidates'][0].update(score=.52)),
            ('credible alternative', lambda p: p['candidates'].append(dict(p['candidates'][0], score=.70))),
            ('table', lambda p: p.update(has_table=True)),
            ('figure', lambda p: p.update(figure=True)),
            ('source conflict', lambda p: p['candidates'][0].update(source_warnings=['来源不一致'])),
            ('native object', lambda p: p['candidates'][0].update(unsupported='原生公式')),
            ('proven correspondence', lambda p: p['candidates'][0].update(scope_verified=True)),
            ('layout', lambda p: p['candidates'][0].update(layout_risks=[{'reason': '浮动表锚点不安全'}])),
            ('explicit coverage loss', lambda p: p.update(reason=p['reason'] + '；来源未覆盖目标中明确列示的发行人本部，不能自动删除业务主体。')),
            ('frozen hard veto', lambda p: p['candidates'][0].update(blocked_reason='来源缺少原稿的持股比例。')),
        ]
        for name, mutate in mutations:
            with self.subTest(name=name):
                proposal = short_unrelated_proposal()
                mutate(proposal)
                before = copy.deepcopy(proposal)
                retain_unmatched(proposal)
                self.assertEqual(before, proposal)

    def test_existing_frozen_weak_only_candidate_can_be_retained(self):
        proposal = short_unrelated_proposal()
        proposal['candidates'][0]['blocked_reason'] = '此候选只有弱相关性，不支持直接覆盖。'
        retain_unmatched(proposal)
        self.assertEqual('keep', proposal['decision'])


if __name__ == '__main__':
    unittest.main()

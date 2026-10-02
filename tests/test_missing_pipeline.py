"""Approved missing items reach Word only through the ordinary insert channel."""
import copy
import tempfile
import unittest
from pathlib import Path
from xml.etree import ElementTree as E

from workbench.docxio import w, text
from workbench.model import Document
from workbench.precision import fingerprint
from workbench.section_items import detect_missing_items
from workbench.missing_pipeline import (derive_new_item_title, decision_fields, validate,
                                        build_proposal, approved_inserts, primary_evidence,
                                        converge_pending)
from test_engine_boundaries import COMPANY, make_docx
from test_copy_retention import rewrite_document
from test_source_coverage import paragraph

RISK = '1、财务风险'
ITEM_A = '报告期内投资活动现金流净额持续为负的风险'
ITEM_B = '报告期内筹资活动现金流净额波动较大的风险'
ITEM_C = '经营活动现金流净额有所下滑的风险'
ITEM_D = '应收账款回收的风险'
BODY_A = '2024-2025年度及2026年1-3月，发行人投资活动产生的现金流量净额分别为-98,000.00万元、-88,000.00万元和-16,000.00万元，持续为负且规模较大。'
BODY_B = '近两年及一期，发行人筹资活动现金流量净额分别为9,007.75万元、-56,000.00万元和12,000.00万元，波动幅度较大，受借款节奏变化影响。'
BODY_C = '2024-2025年度及2026年1-3月，公司经营活动产生的现金流量净额分别为19,698.50万元、7,679.25万元和2,257.92万元，呈逐年下降趋势。'
BODY_D = '截至2024-2025年末以及2026年3月末，发行人应收账款账面价值分别为22,146.46万元、13,050.33万元和13,305.15万元，占总资产比例分别为4.29%和2.58%。'


class MissingPipelineTests(unittest.TestCase):
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

    def pair(self, extra=()):
        blocks = ['二、核查情况', RISK,
                  '（1）' + ITEM_A, BODY_A,
                  '（2）' + ITEM_B, BODY_B,
                  '（3）' + ITEM_C, BODY_C,
                  '2、经营风险', '发行人主营业务覆盖多个板块，经营情况与区域经济发展水平密切相关，业务保持稳定。']
        blocks.extend(extra)
        target = self.build('核查记录.docx', False, blocks)
        source = self.build('募集说明书.docx', True, [
            '（一）财务风险',
            '1、' + ITEM_A, BODY_A,
            '2、' + ITEM_B, BODY_B,
            '3、' + ITEM_C, BODY_C,
            '4、' + ITEM_D, BODY_D,
            '（二）经营风险', '发行人主营业务覆盖多个板块，经营情况与区域经济发展水平密切相关，业务保持稳定。'])
        return target, source

    def candidate(self, extra=()):
        target, source = self.pair(extra)
        found = detect_missing_items(target, [source])
        self.assertEqual(1, len(found))
        return target, source, found[0]

    def test_title_follows_the_target_numbering_style(self):
        self.assertEqual('（10）' + ITEM_D,
                         derive_new_item_title('（9）不动产权证存在瑕疵的风险', '10、' + ITEM_D))

    def test_title_is_not_guessed_without_a_sibling_pattern(self):
        self.assertIsNone(derive_new_item_title('没有编号的相邻事项', '10、' + ITEM_D))
        self.assertIsNone(derive_new_item_title('', '10、' + ITEM_D))

    def test_unapproved_candidate_produces_no_plan(self):
        target, source, cand = self.candidate()
        plans, stale = approved_inserts({'documents': [{'id': target.hash,
                                                       'missing_candidates': [cand]}]},
                                        [], {}, None)
        self.assertEqual([], plans)
        self.assertEqual([], stale)

    def test_declined_candidate_produces_no_plan(self):
        target, source, cand = self.candidate()
        decisions = {cand['candidate_id']: dict(decision_fields(cand), decision='skip')}
        plans, _ = approved_inserts({'documents': [{'id': target.hash,
                                                   'missing_candidates': [cand]}]},
                                    [], decisions, None)
        self.assertEqual([], plans)

    def test_approved_candidate_builds_one_insert_with_the_derived_title(self):
        target, source, cand = self.candidate()
        before = [(b.index, b.text) for b in target.blocks]
        plan = build_proposal(cand, decision_fields(cand), target, source)
        self.assertEqual('source_insert', plan['action'])
        self.assertEqual(plan['start'], plan['end'])
        # The target numbers its siblings （1）（2）（3）, so the new item is （4）.
        self.assertEqual('（4）' + ITEM_D, plan['suggested_title'])
        self.assertIn(BODY_D, plan['candidates'][0]['text'])
        # The copied source range stays heading-free, so the export guard that
        # forbids headings inside a source range keeps applying untouched.
        self.assertFalse(any(source.blocks[i].heading for i in plan['candidates'][0]['indices']))
        # The heading paragraph is created on the target side from the target's
        # own sibling shell; its visible text is exactly the approved title.
        self.assertIsNotNone(plan['missing_heading'])
        self.assertEqual('（4）' + ITEM_D, text(plan['missing_heading']))
        shell_props = plan['missing_heading'].find(w('pPr'))
        sibling = next(b for b in target.blocks if b.text == '（3）' + ITEM_C)
        sibling_props = sibling.el.find(w('pPr'))
        if sibling_props is None:
            self.assertIsNone(shell_props)
        else:
            self.assertEqual(E.tostring(sibling_props), E.tostring(shell_props))
        # Building a plan must not touch the workpaper or its existing headings.
        self.assertEqual(before, [(b.index, b.text) for b in target.blocks])
        self.assertEqual('approved_missing_item', plan['content_class'])
        # Building a plan must not touch the workpaper or its existing headings.
        self.assertEqual(before, [(b.index, b.text) for b in target.blocks])

    def test_repeated_approval_stays_idempotent(self):
        target, source, cand = self.candidate()
        first = build_proposal(cand, decision_fields(cand), target, source)
        second = build_proposal(cand, decision_fields(cand), target, source)
        self.assertEqual(first['id'], second['id'])

    def test_changed_source_body_forces_reconfirmation(self):
        target, source, cand = self.candidate()
        decision = dict(decision_fields(cand), decision='add')
        decision['source_body_hash'] = 'changed'
        self.assertTrue(validate(cand, decision, target))

    def test_moved_anchor_forces_reconfirmation(self):
        target, source, cand = self.candidate()
        decision = dict(decision_fields(cand), decision='add')
        decision['previous_sibling_fingerprint'] = fingerprint([target.blocks[0].el])
        why = validate(cand, decision, target)
        self.assertTrue(why)
        self.assertIn('重新确认', ''.join(why))

    def test_frozen_evidence_passes_validation(self):
        target, source, cand = self.candidate()
        decision = dict(decision_fields(cand), decision='add')
        self.assertEqual([], validate(cand, decision, target))

    def test_duplicate_body_elsewhere_is_safe_default_auto_excluded(self):
        target, source, cand = self.candidate(extra=['三、其他事项', BODY_D])
        self.assertEqual('4、' + ITEM_D, cand['source_item_heading'])
        self.assertTrue(cand['evidence']['body_exists_elsewhere_in_target'])
        self.assertTrue(cand['evidence']['auto_excluded'])
        self.assertIn('不重复新增', cand['evidence']['auto_exclude_reason'])
        self.assertEqual('（4）' + ITEM_D,
                         build_proposal(cand, decision_fields(cand), target, source)['suggested_title'])

    def test_initial_manual_excludes_missing_body_already_elsewhere(self):
        target, source, cand = self.candidate(extra=['三、其他事项', BODY_D])
        self.assertTrue(cand['evidence']['auto_excluded'])
        result={'proposals':[], 'documents':[{'id':target.hash,'missing_candidates':[cand]}]}
        converge_pending(result)
        self.assertEqual(0, result['initial_manual'])

    def test_copied_body_drops_the_source_paragraph_style(self):
        """An imported pStyle would drag the source's Normal into the target."""
        from workbench.plans import drop_imported_paragraph_style
        para = E.Element(w('p'))
        props = E.SubElement(para, w('pPr'))
        E.SubElement(props, w('pStyle'), {w('val'): 'zhengwen'})
        E.SubElement(props, w('ind'), {w('firstLine'): '480'})
        run = E.SubElement(para, w('r'))
        E.SubElement(run, w('t')).text = '正文内容保持原样'
        drop_imported_paragraph_style(para)
        self.assertIsNone(props.find(w('pStyle')))
        self.assertIsNotNone(props.find(w('ind')))
        self.assertEqual('正文内容保持原样', ''.join(t.text or '' for t in para.iter(w('t'))))

    def test_drop_style_reaches_paragraphs_inside_copied_tables(self):
        from workbench.plans import drop_imported_paragraph_style
        table = E.Element(w('tbl'))
        row = E.SubElement(table, w('tr'))
        cell = E.SubElement(row, w('tc'))
        para = E.SubElement(cell, w('p'))
        props = E.SubElement(para, w('pPr'))
        E.SubElement(props, w('pStyle'), {w('val'): 'zhengwen'})
        drop_imported_paragraph_style(table)
        self.assertIsNone(props.find(w('pStyle')))

    def test_same_item_in_two_workpapers_gets_distinct_identities(self):
        """同名事项跨底稿必须是两个决定单位，且身份里不出现来源或版本。"""
        target_a, source = self.pair()
        target_b = self.build('第十章 分析及核查记录.docx', False, [
            '二、核查情况', RISK,
            '（1）' + ITEM_A, BODY_A,
            '（2）' + ITEM_B, BODY_B,
            '（3）' + ITEM_C, BODY_C,
            '2、经营风险', '发行人主营业务覆盖多个板块，经营情况与区域经济发展水平密切相关，业务保持稳定。'])
        a = detect_missing_items(target_a, [source])
        b = detect_missing_items(target_b, [source])
        self.assertEqual(1, len(a))
        self.assertEqual(1, len(b))
        self.assertNotEqual(a[0]['candidate_id'], b[0]['candidate_id'])
        self.assertIn(target_a.name, a[0]['candidate_id'])
        self.assertNotIn(source.hash, a[0]['candidate_id'])
        self.assertNotIn(source.hash, b[0]['candidate_id'])

    def test_identity_is_stable_when_the_source_file_changes_version(self):
        """身份只由"在哪里、是什么"决定；来源版本变化不改变身份。"""
        target, source, cand = self.candidate()
        same = dict(cand)
        same['source_hash'] = 'a-different-source-version'
        self.assertEqual(cand['candidate_id'], same['candidate_id'])
        self.assertEqual({k: cand[k] for k in ('target_doc_key', 'target_parent_key', 'source_item_key')},
                         {k: same[k] for k in ('target_doc_key', 'target_parent_key', 'source_item_key')})

    def test_sources_reporting_one_item_share_a_single_identity(self):
        from workbench.engine import Engine
        target, source = self.pair()
        opinion = self.build('核查意见.docx', True, [
            '（一）财务风险',
            '1、' + ITEM_A, BODY_A,
            '2、' + ITEM_B, BODY_B,
            '3、' + ITEM_C, BODY_C,
            '4、' + ITEM_D, '经核查，' + BODY_D,
            '（二）经营风险', '发行人主营业务覆盖多个板块，经营情况与区域经济发展水平密切相关，业务保持稳定。'])
        opinion.profile['kind'] = 'opinion'
        cands = detect_missing_items(target, [source, opinion])
        self.assertEqual(2, len(cands), '仍然是两条 evidence')
        self.assertEqual(1, len({c['candidate_id'] for c in cands}), '但只有一个身份')
        self.assertEqual(source.hash, primary_evidence(cands)['source_hash'])

    def test_target_parent_key_is_a_structural_path_not_just_a_title(self):
        """父项键必须是完整结构路径。

        只用最近一级标题文本作键时，同章内两个不同上级结构下的同名标题会
        撞成一个身份，人工决定就会从一条结构路径串到另一条。
        """
        target, source, cand = self.candidate()
        path = cand['target_parent_key']
        self.assertIn('>', path, '父项键必须是结构路径，而不是单个标题')
        self.assertGreater(len(path.split('>')), 1)
        self.assertTrue(path.endswith('财务风险'), path)
        self.assertEqual(cand['candidate_id'],
                         '%s|%s|%s' % (cand['target_doc_key'], path, cand['source_item_key']))

    def test_same_parent_title_in_another_structure_gets_a_different_key(self):
        """两个上级结构下的同名父项，父项键必须不同。"""
        target = self.build('核查记录.docx', False, [
            '二、核查情况',
            '（一）与发行人相关的风险', '1、财务风险',
            '（1）' + ITEM_A, BODY_A, '（2）' + ITEM_B, BODY_B, '（3）' + ITEM_C, BODY_C,
            '（二）其他事项', '1、财务风险',
            '（1）' + ITEM_A, BODY_A, '（2）' + ITEM_B, BODY_B, '（3）' + ITEM_C, BODY_C])
        source = self.build('募集说明书.docx', True, [
            '（一）财务风险',
            '1、' + ITEM_A, BODY_A, '2、' + ITEM_B, BODY_B,
            '3、' + ITEM_C, BODY_C, '4、' + ITEM_D, BODY_D])
        found = detect_missing_items(target, [source])
        self.assertTrue(found, '至少检出第一个结构下的缺项')
        keys = {f['target_parent_key'] for f in found}
        self.assertEqual(1, len(keys))
        # 键里带有上级结构，这正是它与另一个同名结构区分开的原因。
        self.assertIn('相关的风险', next(iter(keys)))

    def test_skip_in_one_workpaper_leaves_the_other_pending(self):
        """九章跳过「流动资产变现」，十章的同一标题必须仍然待确认。"""
        target_a, source = self.pair()
        target_b = self.build('第十章 分析及核查记录.docx', False, [
            '二、核查情况', RISK,
            '（1）' + ITEM_A, BODY_A,
            '（2）' + ITEM_B, BODY_B,
            '（3）' + ITEM_C, BODY_C,
            '2、经营风险', '发行人主营业务覆盖多个板块，经营情况与区域经济发展水平密切相关，业务保持稳定。'])
        a = detect_missing_items(target_a, [source])[0]
        b = detect_missing_items(target_b, [source])[0]
        decisions = {a['candidate_id']: dict(decision_fields(a), decision='skip')}
        self.assertNotEqual(a['candidate_id'], b['candidate_id'])
        self.assertNotIn(b['candidate_id'], decisions)
        # The stored skip must not validate against the other chapter's item.
        self.assertTrue(validate(b, decisions[a['candidate_id']], target_b))

    def test_drift_returns_only_its_own_candidate(self):
        """一个事项的指纹漂移，另一底稿的同名事项不受影响。"""
        target_a, source = self.pair()
        target_b = self.build('第十章 分析及核查记录.docx', False, [
            '二、核查情况', RISK,
            '（1）' + ITEM_A, BODY_A,
            '（2）' + ITEM_B, BODY_B,
            '（3）' + ITEM_C, BODY_C,
            '2、经营风险', '发行人主营业务覆盖多个板块，经营情况与区域经济发展水平密切相关，业务保持稳定。'])
        a = detect_missing_items(target_a, [source])[0]
        b = detect_missing_items(target_b, [source])[0]
        drifted = dict(decision_fields(a), decision='skip')
        drifted['parent_fingerprint'] = 'moved-elsewhere'
        self.assertTrue(validate(a, drifted, target_a), '漂移的那个要重新确认')
        self.assertEqual([], validate(b, dict(decision_fields(b), decision='skip'), target_b),
                         '未漂移的那个必须保持有效')

    def test_batch_writes_one_decision_per_logical_candidate(self):
        """批量只按 logical candidate 计数：两条 evidence 仍是一个决定。"""
        from workbench.engine import Engine
        target, source = self.pair()
        opinion = self.build('核查意见.docx', True, [
            '（一）财务风险',
            '1、' + ITEM_A, BODY_A,
            '2、' + ITEM_B, BODY_B,
            '3、' + ITEM_C, BODY_C,
            '4、' + ITEM_D, '经核查，' + BODY_D,
            '（二）经营风险', '发行人主营业务覆盖多个板块，经营情况与区域经济发展水平密切相关，业务保持稳定。'])
        opinion.profile['kind'] = 'opinion'
        cands = detect_missing_items(target, [source, opinion])
        self.assertEqual(2, len(cands), '两条 evidence')
        unit = {c['candidate_id'] for c in cands}
        self.assertEqual(1, len(unit), '批量只应写 1 条决定')
        files = [{'id': target.hash, 'role': 'target', 'path': target.path, 'name': '核查记录.docx'},
                 {'id': source.hash, 'role': 'source', 'path': source.path, 'name': '募集说明书.docx'},
                 {'id': opinion.hash, 'role': 'source', 'path': opinion.path, 'name': '核查意见.docx'}]
        decisions = {unit.pop(): dict(decision_fields(primary_evidence(cands)), decision='skip')}
        plans, stale = approved_inserts(
            {'documents': [{'id': target.hash, 'missing_candidates': cands}]},
            files, decisions, Engine())
        self.assertEqual([], plans, 'skip 不产生插入计划')
        self.assertEqual([], stale)

    def test_converge_build_failure_uses_safe_keep_not_human_queue(self):
        """等价比较异常时不得假装可改；95分策略应安全保留原文而非转嫁人工。"""
        from workbench import missing_pipeline as mp
        p = {'id': 'x:1', 'decision': 'pending', 'content_class': 'material',
             'reason': '存在候选，但相似度或候选差距不足；请核对一次后决定。',
             'candidates': [{'text': 'A'}, {'text': 'B'}], 'selected': 0,
             'kind': 'material', 'heading': '测试', 'target_name': 'x', 'start': 0, 'end': 1}
        result = {'proposals': [p]}
        original = mp.build
        def boom(*a, **k):
            raise RuntimeError('boom')
        mp.build = boom
        try:
            mp.converge_pending(result, engine=object(), files=[])
        finally:
            mp.build = original
        self.assertEqual('keep', p['decision'], '构建失败必须安全保留原文')
        self.assertTrue(p.get('auto'), '安全默认应从普通人工队列移除')
        self.assertIn('保留原文', p.get('auto_note', ''))

    def test_converge_fact_and_table_risks_auto_keep(self):
        """事实、完整性和表格迁移风险都有安全默认，不应要求用户逐项确认。"""
        from workbench import missing_pipeline as mp
        rows = [
            {'id':'fact','decision':'pending','content_class':'material','reason':'来源段落未包含原稿中的持股比例；不能删去该事实。',
             'candidates':[{'blocked_reason':'来源缺少原稿的持股比例。'}],'selected':0,'kind':'material','has_table':False},
            {'id':'table','decision':'pending','content_class':'material','reason':'来源表格缺少原稿部分行项目',
             'candidates':[{}],'selected':0,'kind':'material','has_table':True},
            {'id':'weak','decision':'pending','content_class':'material','reason':'标题接近但不是完全一致；请确认整个事项是否对应后再采用。',
             'candidates':[{}],'selected':0,'kind':'material','has_table':False},
        ]
        result={'proposals':rows,'documents':[]}
        mp.converge_pending(result)
        self.assertEqual(['keep','keep','keep'], [x['decision'] for x in rows])
        self.assertTrue(all(x.get('auto') for x in rows))
        self.assertEqual(0, result['initial_manual'])

    def test_converge_true_source_version_conflict_stays_human(self):
        """只有不同正式/current来源版本实质冲突才保留人工。"""
        from workbench import missing_pipeline as mp
        p={'id':'conflict','decision':'pending','content_class':'material',
           'reason':'不同位置或版本给出高度相似但文字、数字不同的来源；请先确定适用版本，未自动采用。',
           'candidates':[{'text':'A'},{'text':'B'}],'selected':None,'kind':'material','has_table':False}
        result={'proposals':[p],'documents':[]}
        mp.converge_pending(result)
        self.assertEqual('pending', p['decision'])
        self.assertFalse(p.get('auto',False))
        self.assertEqual(1, result['initial_manual'])

    def test_statistics_consistency_after_converge(self):
        """proposal 总数 = 自动 + 人工；initial_manual = 人工 + 缺项未确认。"""
        from workbench.engine import Engine
        target, source = self.pair()
        opinion = self.build('核查意见.docx', True, [
            '（一）财务风险',
            '1、' + ITEM_A, BODY_A, '2、' + ITEM_B, BODY_B, '3、' + ITEM_C, BODY_C,
            '4、' + ITEM_D, BODY_D,
            '（二）经营风险', '发行人主营业务覆盖多个板块，经营情况与区域经济发展水平密切相关，业务保持稳定。'])
        opinion.profile['kind'] = 'opinion'
        result = Engine().analyze(
            [{'id': target.hash, 'role': 'target', 'path': target.path, 'name': '核查记录.docx'},
             {'id': source.hash, 'role': 'source', 'path': source.path, 'name': '募集说明书.docx'},
             {'id': opinion.hash, 'role': 'source', 'path': opinion.path, 'name': '核查意见.docx'}],
            {'issuer': '', 'author': '', 'bond': '', 'period': '', 'date': ''}, lambda *a: None)
        converge_pending(result, Engine(),
                         [target, source, opinion])
        ps = result['proposals']
        pending = [x for x in ps if x.get('decision') == 'pending'
                   and x.get('content_class') != 'no_correspondence_retained']
        auto = [x for x in ps if x.get('auto')]
        self.assertEqual(len(ps), len(auto) + len(pending),
                         '非 auto 项必须恰为待处理项（分类完备，无第三态）')
        self.assertEqual(result['initial_manual'],
                         len(pending) + len({c['candidate_id'] for d in result['documents']
                                             for c in d.get('missing_candidates', [])}),
                         'initial_manual 必须等于首次分析后的真实待处理数')

    def test_dual_source_group_inserts_only_the_prospectus_copy(self):
        """One item, two formal sources: the human decides once, one plan runs.

        The reverse guarantee that a hand-built plan whose source range contains
        a heading is still refused stays covered by test_source_coverage.
        """
        from workbench.engine import Engine
        target, source = self.pair()
        opinion = self.build('核查意见.docx', True, [
            '（一）财务风险',
            '1、' + ITEM_A, BODY_A,
            '2、' + ITEM_B, BODY_B,
            '3、' + ITEM_C, BODY_C,
            '4、' + ITEM_D, '经核查，' + BODY_D,
            '（二）经营风险', '发行人主营业务覆盖多个板块，经营情况与区域经济发展水平密切相关，业务保持稳定。'])
        opinion.profile['kind'] = 'opinion'
        cands = detect_missing_items(target, [source, opinion])
        self.assertEqual(2, len(cands))
        files = [{'id': target.hash, 'role': 'target', 'path': target.path, 'name': '核查记录.docx'},
                 {'id': source.hash, 'role': 'source', 'path': source.path, 'name': '募集说明书.docx'},
                 {'id': opinion.hash, 'role': 'source', 'path': opinion.path, 'name': '核查意见.docx'}]
        primary = primary_evidence(cands)
        decisions = {primary['candidate_id']: dict(decision_fields(primary), decision='add')}
        plans, stale = approved_inserts(
            {'documents': [{'id': target.hash, 'missing_candidates': cands}]},
            files, decisions, Engine())
        self.assertEqual(1, len(plans))
        self.assertEqual(source.hash, plans[0]['candidates'][0]['doc_hash'])
        self.assertEqual([], stale)
        self.assertEqual(1, len({p['start'] for p in plans}))


if __name__ == '__main__':
    unittest.main()

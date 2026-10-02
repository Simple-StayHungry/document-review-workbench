"""Regression locks for the decision-first review surface.

These are intentionally dependency-free. Browser-level geometry is verified by the
release QA script; this module prevents the most damaging UI regressions from
quietly returning in ordinary unit-test runs.
"""
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / 'web' / 'index.html').read_text(encoding='utf-8')
JS = (ROOT / 'web' / 'app.js').read_text(encoding='utf-8')
CSS = (ROOT / 'web' / 'style.css').read_text(encoding='utf-8')


class ReviewUIContractTests(unittest.TestCase):
    def test_review_surface_does_not_restore_dashboard_stats_or_filter_toolbar(self):
        review = HTML.split('<section id="reviewPanel"', 1)[1].split('</section>', 1)[0]
        self.assertNotIn('stats-grid', review)
        self.assertNotIn('reviewFilters', review)
        self.assertNotIn('reviewSearch', review)
        self.assertNotIn('docFilter', review)
        self.assertIn('reviewProgress', review)
        self.assertIn('changeList', review)
        self.assertIn('changeDetail', review)

    def test_workflow_labels_are_flat_single_line_text(self):
        block = JS.split('function renderSteps(){', 1)[1].split('\n}', 1)[0]
        self.assertIn("b.textContent=labels", block)
        self.assertNotIn('step-state', block)
        self.assertRegex(CSS, r'\.topbar \.step\{[^}]*white-space:nowrap')
        self.assertIn('.topbar .step span,.topbar .step-state{display:none!important}', CSS)

    def test_review_is_before_after_with_persistent_decision_actions(self):
        self.assertIn('处理前', JS)
        self.assertIn('处理后', JS)
        self.assertIn('应用修改', JS)
        self.assertIn('保持原文', JS)
        self.assertIn('查看依据', JS)
        self.assertRegex(CSS, r'\.review-actionbar\{[^}]*position:sticky')
        self.assertIn('.decision-compare{display:grid', CSS)

    def test_only_manual_decisions_enter_normal_review_units(self):
        block = JS.split('function reviewUnits(){', 1)[1].split('\n}', 1)[0]
        self.assertIn('ps.filter(needsReview)', block)
        self.assertIn('evidence?.auto_excluded', block)
        self.assertNotIn('object_inventory', block)

    def test_decision_advance_is_computed_before_mutation(self):
        self.assertIn('function nextPendingAfter(id)', JS)
        missing = JS.split('async function decideMissing(group,act){', 1)[1].split('\n}', 1)[0]
        proposal = JS.split('async function saveDecision(p,decision){', 1)[1].split('\n}', 1)[0]
        self.assertIn("nextId=nextPendingAfter(consumed)", missing)
        self.assertIn("state.selected=nextId", missing)
        self.assertIn("nextId=decision==='pending'?p.id:nextPendingAfter(p.id)", proposal)
        self.assertIn("state.selected=nextId", proposal)

    def test_narrow_window_keeps_compare_and_actions_in_review_workspace(self):
        marker = '/* v19.5 narrow-window QA:'
        self.assertIn(marker, CSS)
        narrow = CSS.split(marker, 1)[1]
        self.assertIn('grid-template-rows:112px minmax(0,1fr)', narrow)
        self.assertIn('.decision-compare{grid-template-columns:minmax(0,1fr) minmax(0,1fr)', narrow)
        self.assertIn('.review-actionbar{margin:8px -12px 0', narrow)


    def test_start_analysis_switches_to_review_before_waiting_for_server(self):
        block = JS.split('async function analyze(){', 1)[1].split('\n}', 1)[0]
        show = block.index("showStep('review')")
        start = block.index("await api(`/api/projects/${state.project.id}/analyze`")
        self.assertLess(show, start)
        self.assertIn("message:'正在准备核查'", block)

    def test_manual_queue_does_not_show_generic_risk_labels(self):
        block = JS.split('function proposalReasonLabel', 1)[1].split('function proposalOneLine', 1)[0]
        self.assertIn('需要判断', block)
        for label in ('事实风险','表格风险','定位风险'):
            self.assertNotIn(label, block)

    def test_processed_decisions_disappear_from_main_list(self):
        block = JS.split('function renderChangeList(){', 1)[1].split('\n}', 1)[0]
        self.assertIn('const q=reviewUnits(),pending=q.pending', block)
        self.assertNotIn('q.done.map', block)
        self.assertNotIn('done-group', block)
        self.assertIn("state.selected=pending[0]?.id||null", block)

    def test_review_header_shows_selected_target_chapter(self):
        review = HTML.split('<section id="reviewPanel"', 1)[1].split('</section>', 1)[0]
        self.assertIn('id="reviewChapter"', review)
        self.assertIn('function chapterFromTargetName', JS)
        self.assertIn('修改位置 · ${chapter}', JS)
        self.assertIn("setReviewChapter(c._doc||'')", JS)
        self.assertIn("setReviewChapter(p.target_name||'')", JS)
        self.assertRegex(CSS, r'\.review-chapter\{[^}]*left:50%')

    def test_review_header_uses_remaining_count_not_historical_fraction(self):
        steps = JS.split('function renderSteps(){', 1)[1].split('\n}', 1)[0]
        review = JS.split('function renderReview(){', 1)[1].split('\n}', 1)[0]
        self.assertIn("pending?('核对 '+pending):'核对 完成'", steps)
        self.assertIn('剩余 ${q.pending.length} 项需要你决定', review)
        self.assertNotIn('第 ${current} / ${total}', review)

    def test_multi_source_missing_decision_uses_one_representative_and_disappears(self):
        grouping = JS.split('function primaryMissingEvidence', 1)[1].split('function suggestMissingTitle', 1)[0]
        self.assertIn("rows.find(r=>r.source_kind==='prospectus')||rows[0]", grouping)
        self.assertIn('decision=decisions[key]', grouping)
        self.assertIn('missingFingerprintsMatch(decision,representative)', grouping)
        self.assertNotIn('valid.length===g.rows.length', grouping)

        decide = JS.split('async function decideMissing(group,act){', 1)[1].split('\n}', 1)[0]
        self.assertIn('const c=primaryMissingEvidence(group.rows)||group.item', decide)
        self.assertEqual(1, decide.count("await api(`/api/projects/${state.project.id}/missing`"),
                         '一个逻辑缺项只能提交一次决定，不能按 evidence 重复 POST')
        self.assertNotIn('for(const c of group.rows)', decide)
        self.assertIn('已从待处理列表移除', decide)

    def test_export_page_hides_non_actionable_internal_warning_counts(self):
        block = JS.split('function renderExport(){', 1)[1].split('function openModal', 1)[0]
        self.assertNotIn('条结构或口径风险提示', block)
        self.assertNotIn('项来源一致性提示', block)
        self.assertIn('仍有 ${manualPending} 项需要确认', block)



if __name__ == '__main__':
    unittest.main()

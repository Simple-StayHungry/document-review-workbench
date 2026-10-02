"""Turn one human-approved missing item into parameters for the existing insert.

This is deliberately the narrowest possible bridge.  It invents no Word writing
logic: it validates the frozen fingerprints, derives a title in the target's own
numbering style, and returns a proposal shaped exactly like the ones
``recover_source_additions`` already produces, so the ordinary ``source_insert``
channel does the actual writing.

``derive_new_item_title`` may only be reached from here.  No existing heading is
ever re-numbered, re-derived or normalised: this module never looks at items
that already exist in the workpaper.
"""
from __future__ import annotations
import copy
import re
import sys
from .docxio import w
from .matching import title_key
from .model import strip_lead
from .precision import fingerprint
from .plans import build

LEAD = re.compile(r'^\s*(?:[（(]([^）)]*)[）)]|(\d+(?:[.．]\d+)*)\s*[、.．)）])')
DIGITS = re.compile(r'\d+')


def derive_new_item_title(previous_sibling_heading, source_item_heading):
    """Number a NEW item the way the target already numbers its siblings.

    Returns None when the sibling pattern cannot be read, so the caller can fall
    back to the source wording instead of guessing a format.
    """
    if not previous_sibling_heading or not source_item_heading:
        return None
    match = LEAD.match(previous_sibling_heading)
    if not match:
        return None
    current = match.group(1) or match.group(2)
    if not current or not DIGITS.fullmatch(current.strip()):
        return None
    body = strip_lead(source_item_heading)
    if not body:
        return None
    number = str(int(current) + 1)
    if match.group(1) is not None:
        return '（%s）%s' % (number, body)
    return '%s、%s' % (number, body)


def decision_fields(candidate):
    """The fingerprints a stored human decision is bound to."""
    return {'candidate_id': candidate.get('candidate_id', ''),
            'source_hash': candidate.get('source_hash', ''),
            'source_body_hash': candidate.get('source_body_hash', ''),
            'parent_fingerprint': candidate.get('parent_fingerprint', ''),
            'previous_sibling_fingerprint': candidate.get('previous_sibling_fingerprint', ''),
            'next_boundary_fingerprint': candidate.get('next_boundary_fingerprint', '')}


def validate(candidate, decision, doc=None):
    """Refuse to reuse a decision once the source or the anchors moved.

    The candidate is re-detected on every run, so it carries the fingerprints of
    the workpaper as it stands now.  A stored decision is compared against that,
    not merely searched for somewhere in the file: an anchor that drifted to a
    different position is a changed anchor.  Empty means the frozen evidence
    still holds; otherwise the item goes back to human review.
    """
    why = []
    if decision.get('candidate_id') != candidate.get('candidate_id'):
        why.append('候选标识与已确认记录不一致')
    for key, label in (('source_hash', '来源文件'),
                       ('source_body_hash', '来源正文'),
                       ('parent_fingerprint', '目标父事项'),
                       ('previous_sibling_fingerprint', '前一相邻事项'),
                       ('next_boundary_fingerprint', '后一边界')):
        frozen = decision.get(key)
        if frozen and frozen != candidate.get(key):
            why.append('%s已变化，需重新确认' % label)
    return why


def create_missing_item_heading(target_doc, shell_index, approved_title):
    """Create the target-side heading paragraph for one approved missing item.

    The style shell is copied from the target's own sibling heading — paragraph
    properties, run properties, spacing, indentation — never from the source.
    Only the visible text becomes the approved title. Automatic numbering is
    stripped so the number cannot appear twice; the approved title carries it.
    """
    if not approved_title or shell_index is None:
        return None
    if not 0 <= shell_index < len(target_doc.blocks):
        return None
    shell = copy.deepcopy(target_doc.blocks[shell_index].el)
    props = shell.find(w('pPr'))
    if props is not None:
        numbering = props.find(w('numPr'))
        if numbering is not None:
            props.remove(numbering)
    texts = [t for t in shell.iter(w('t'))]
    if not texts:
        return None
    texts[0].text = approved_title
    for t in texts[1:]:
        t.text = ''
    return shell


def build_proposal(candidate, decision, doc, source):
    """One approved missing item, expressed as an ordinary source_insert plan."""
    # Only the body is copied. The export guard forbids a source range that
    # contains a heading, so the derived title stays a suggestion until a
    # separate, explicit decision allows writing one.
    start, end = candidate['source_body_range']
    body = [b for b in source.blocks[start:end] if not b.heading and not b.is_toc]
    if not body:
        return None
    title = decision.get('approved_title') or derive_new_item_title(
        candidate.get('previous_sibling_heading'), candidate.get('source_item_heading'))
    indices = [b.index for b in body]
    position = candidate['suggested_insert_position']
    if not 0 <= position <= len(doc.blocks):
        return None
    cand = {'doc_hash': source.hash, 'source_name': source.name, 'start': body[0].index,
            'end': body[-1].index + 1, 'indices': indices, 'unit_id': 'missing' + str(start),
            'text': '\n'.join(b.text for b in body), 'score': 1.0, 'literal_score': 1.0,
            'content_score': 1.0, 'heading_score': 1.0, 'table_score': 0.0, 'exact': False,
            'locator': candidate.get('source_parent_heading', ''),
            'unsupported': '', 'source_warnings': [], 'scope_verified': True,
            'source_fingerprint': fingerprint([b.el for b in body]),
            'correspondence': {'method': 'approved-missing-item', 'anchors': [],
                               'target_heading': None, 'source_heading': None}}
    return {'id': doc.hash + ':missing:' + candidate['candidate_id'],
            'target_id': doc.hash, 'target_name': doc.name,
            'start': position, 'end': position,
            'insert_before': position if position < len(doc.blocks) else None,
            'insert_after': position - 1 if position else None,
            'insert_before_fingerprint': fingerprint([doc.blocks[position].el]) if position < len(doc.blocks) else None,
            'insert_after_fingerprint': fingerprint([doc.blocks[position - 1].el]) if position else None,
            'matter': '正文', 'heading': candidate.get('target_parent_heading', ''),
            'locator': candidate.get('target_parent_heading', ''), 'old_text': '',
            'status': 'auto', 'decision': 'accept', 'selected': 0, 'candidates': [cand],
            'action': 'source_insert', 'anchor_sources': {}, 'kind': 'material',
            'content_class': 'approved_missing_item',
            # The target-side heading is created here, from the target's own
            # style shell; the copied source range stays heading-free.
            'missing_heading': create_missing_item_heading(
                doc, candidate.get('previous_sibling_index')
                if candidate.get('previous_sibling_index') is not None
                else candidate.get('next_boundary_index'), title),
            'suggested_title': title or '',
            'approved_title': title or '',
            # Frozen evidence snapshot: the export audit re-checks these against
            # the persisted human decision before accepting the extra heading.
            'decision_fingerprints': dict(decision_fields(candidate)),
            'reason': '人工确认的来源新增事项；按目标同级体例生成标题，已有标题与编号不变。',
            'has_table': False, 'table_mode': 'copy', 'sync_mode': 'block', 'identity': None, 'note': ''}


def primary_evidence(rows):
    """The one evidence whose fingerprints a decision is bound to.

    Several formal sources can report the same item. They share one identity, so
    one representative must carry the version state; the prospectus wins, matching
    the existing authority rule.
    """
    return next((r for r in rows if r.get('source_kind') == 'prospectus'), rows[0])


AUTO_KEEP_MARKS = ('组合形状或文本框', '含 OLE 嵌入对象', '图文混合段落无法安全分离',
                   '图示位于表格内', '保留完整表格等待人工核对', '无法安全分离')

# Only these are true human decisions.  Everything else has a safe default:
# preserve the current workpaper rather than asking the user to chase the last
# few percentage points of automation accuracy.
_HUMAN_CONFLICT_MARKS = (
    '不同位置或版本给出高度相似但文字、数字不同的来源',
    '同一完整事项在同类最新版来源中存在内容差异',
    '同一结构位置仍对应多个不同来源版本',
    '无法仅靠标题和上下文确定唯一来源',
    '来源自身存在一致性提示',
    '对应来源自身存在一致性提示',
    '对应来源存在一致性提示',
    '对应完整事项来源存在一致性提示',
    '完整来源事项含一致性提示',
    '已定位合段但对应来源存在一致性提示',
    '请先确定适用版本',
)


def _true_human_conflict(p):
    """Whether keeping the draft is itself an unresolved substantive choice.

    Low similarity, incomplete sources, table/layout migration limits and fact
    protection are *not* user decisions: the safe outcome is to keep the current
    draft.  Humans are asked only when two current/formal source interpretations
    remain materially incompatible, so choosing neither may preserve the wrong
    version rather than merely miss an optional automation.
    """
    reason = p.get('reason') or ''
    cands = p.get('candidates') or []
    if any(c.get('source_warnings') for c in cands):
        return True
    return any(mark in reason for mark in _HUMAN_CONFLICT_MARKS)


def _auto_keep(p, note):
    p['decision'] = 'keep'
    p['selected'] = None
    p['auto'] = True
    p['auto_note'] = note
    return p['id']


def converge_pending(result, engine=None, files=None):
    """Collapse review to only irreducible human judgement.

    Product rule: this is a 95-point workbench, not a 100-point human-review
    queue.  If the program cannot safely apply a change but can safely leave the
    current workpaper untouched, it does exactly that and records the reason in
    the audit trail.  A human sees an item only when the program cannot determine
    which materially different current/formal source version is applicable.

    Low-confidence alternatives are still allowed to auto-apply when every
    candidate builds the exact same Word result.  If they differ, the safe
    default is keep-original unless the proposal is a true formal-source conflict.
    """
    resolved = []
    for p in result.get('proposals', []):
        if p.get('decision') and p.get('decision') != 'pending':
            p['auto'] = True
            continue
        if p.get('decision') != 'pending' or p.get('content_class') == 'no_correspondence_retained':
            continue

        reason = p.get('reason') or ''
        cands = p.get('candidates') or []

        # A formal/current-source conflict is the narrow class that remains a
        # real user decision.  Do not silently choose a version or source.
        if _true_human_conflict(p):
            continue

        # Retrieval uncertainty is not automatically a user task.  If every
        # candidate generates the same Word result we can safely apply it;
        # otherwise keeping the original is the deterministic safe default.
        if '相似度或候选差距不足' in reason and engine and files and len(cands) > 1:
            texts = []
            for i in range(len(cands)):
                try:
                    b = build(engine, dict(p, selected=i), files)
                    texts.append(''.join((x.get('text') or '') for x in b.get('new') or []))
                except Exception as ex:
                    sys.stderr.write('[converge] 等价构建失败 %s 候选%d: %s\n' % (p.get('id', '')[-16:], i, ex))
                    texts = None
                    break
            if texts and len(set(texts)) == 1:
                p['decision'] = 'accept'
                p['selected'] = p.get('selected') if isinstance(p.get('selected'), int) else 0
                p['auto_note'] = '多个候选来源产生的修改结果完全一致，已按规则自动处理。'
                p['auto'] = True
                resolved.append(p['id'])
                continue
            resolved.append(_auto_keep(
                p, '候选定位仍有不确定性；为避免误改，已按安全默认保留原文，无需人工确认。'))
            continue

        # Fact/completeness/layout/table/unsupported/complex-object risks all have
        # the same safe outcome: do not touch the current workpaper.
        chosen = cands[p.get('selected') or 0] if cands else {}
        blocked = chosen.get('blocked_reason') or chosen.get('unsupported') or ''
        if blocked:
            resolved.append(_auto_keep(
                p, '来源未通过事实或安全保护；已保留原文，不把保护性拦截转嫁给人工。'))
            continue
        if '明显短于目标' in reason or '遗漏' in reason:
            resolved.append(_auto_keep(
                p, '来源可能遗漏原稿事实；已保留原文，无需人工确认。'))
            continue
        if p.get('has_table') or '表格' in reason:
            resolved.append(_auto_keep(
                p, '表格无法在当前证据下安全自动同步；已保留原表。'))
            continue
        if any(m in reason for m in AUTO_KEEP_MARKS) or p.get('content_class') in ('layout_pending', 'copy_boundary_pending'):
            resolved.append(_auto_keep(
                p, '复杂对象或版式边界无法安全迁移；已保留原文。'))
            continue

        # All other review-only uncertainty is non-critical.  Preserving the
        # draft is preferable to asking the user to inspect an 80%-confidence
        # locator just to chase theoretical completeness.
        resolved.append(_auto_keep(
            p, '自动修改把握不足；已按安全默认保留原文，无需人工确认。'))

    manual = [p for p in result.get('proposals', []) if p.get('decision') == 'pending'
              and p.get('content_class') != 'no_correspondence_retained']
    mkeys = {c['candidate_id'] for d in result.get('documents', [])
             for c in d.get('missing_candidates', [])
             if not (c.get('evidence') or {}).get('auto_excluded')}
    result['initial_manual'] = len(manual) + len(mkeys)
    return resolved


def approved_inserts(result, files, decisions, engine):
    """Proposals for every still-valid approved decision, plus the stale ones.

    Decisions are keyed by the stable candidate id, so approving an item once
    covers every source that reported it. The prospectus copy is the one written,
    and a same-level conflict is refused rather than inserted twice.
    """
    by_name = {f['id']: f for f in files}
    groups, order = {}, []
    for record in result.get('documents', []):
        doc_file = by_name.get(record.get('id'))
        if not doc_file:
            continue
        doc = engine.load(doc_file)
        for candidate in record.get('missing_candidates') or []:
            cid = candidate.get('candidate_id')
            if cid not in groups:
                groups[cid] = {'doc': doc, 'rows': []}
                order.append(cid)
            groups[cid]['rows'].append(candidate)
    plans, stale = [], []
    for cid in order:
        entry = groups[cid]
        decision = decisions.get(cid)
        if not decision or decision.get('decision') != 'add':
            continue
        rows, doc = entry['rows'], entry['doc']
        chosen = primary_evidence(rows)
        why = validate(chosen, decision, doc)
        if why:
            stale.append({'candidate_id': cid, 'target_name': doc.name, 'errors': why})
            continue
        same_level = [r for r in rows if r.get('source_kind') == chosen.get('source_kind')]
        if len(same_level) > 1:
            stale.append({'candidate_id': cid, 'target_name': doc.name,
                          'errors': ['同一新增事项存在多份同级别来源，未唯一定位，需人工核对']})
            continue
        source = next((engine.load(by_name[f['id']]) for f in files
                       if f.get('role') == 'source'
                       and engine.load(by_name[f['id']]).hash == chosen.get('source_hash')), None)
        if source is None:
            stale.append({'candidate_id': cid, 'target_name': doc.name,
                          'errors': ['来源文件已不在项目中']})
            continue
        plan = build_proposal(chosen, decision, doc, source)
        if plan is None:
            stale.append({'candidate_id': cid, 'target_name': doc.name,
                          'errors': ['无法按冻结位置生成插入计划']})
            continue
        plans.append(plan)
    plans.sort(key=lambda p: (p['start'], p['id']))
    return plans, stale

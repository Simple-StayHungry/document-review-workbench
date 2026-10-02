"""Exercise actual Engine writeback, rather than trusting match labels or previews."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
import zipfile
from xml.etree import ElementTree as ET

from workbench.docxio import resolve_revisions, structural_digest_content, text, w
from workbench.engine import Engine
from workbench.model import Document
from test_engine_boundaries import COMPANY, FACT, make_docx


UNMATCHED = (
    '古籍修复车间使用手工竹帘抄纸、浆糊调配和传统装帧工艺，'
    '纸本文献在恒温恒湿库房内分类保管，修复师逐叶记录虫蛀和霉斑状况。'
)


def rewrite_document(record, edit):
    path = Path(record['path'])
    with zipfile.ZipFile(path) as archive:
        entries = {name: archive.read(name) for name in archive.namelist()}
    root = ET.fromstring(entries['word/document.xml'])
    edit(root.find(w('body')))
    # Seed navigation identities so the assertion can compare the entire rejected
    # tree without ignoring IDs that export would otherwise add to this fixture.
    for index, node in enumerate((n for n in root.iter() if n.tag in (w('p'), w('tr'))), 1):
        node.set('{http://schemas.microsoft.com/office/word/2010/wordml}paraId', f'{index:08X}')
    entries['word/document.xml'] = ET.tostring(root, encoding='utf-8', xml_declaration=True)
    with zipfile.ZipFile(path, 'w') as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    doc = Document(path)
    record.update(id=doc.hash, profile=doc.profile)


def read_root(path):
    with zipfile.ZipFile(path) as archive:
        return ET.fromstring(archive.read('word/document.xml'))


def paragraphs(root, value):
    return [paragraph for paragraph in root.iter(w('p')) if text(paragraph) == value]


class CopyAndRetentionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='workbench-copy-retention-')
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.target = make_docx(self.base / '原始底稿.docx')
        self.source = make_docx(self.base / '当前募集说明书.docx', source=True)

        # The words are identical in both inputs. Bold exists ONLY in the source,
        # so decorating a target clone cannot satisfy the insertion assertion.
        def mark_source(body):
            paragraph = next(p for p in body.findall(w('p')) if text(p) == FACT)
            run = paragraph.find(w('r'))
            properties = ET.Element(w('rPr'))
            ET.SubElement(properties, w('b'))
            run.insert(0, properties)

        rewrite_document(self.source, mark_source)
        rewrite_document(self.target, lambda _body: None)
        self.files = [self.target, self.source]
        self.params = {'issuer': COMPANY, 'author': '柒'}

    def export(self):
        engine = Engine()
        result = engine.analyze(self.files, self.params)
        out = self.base / 'export'
        exported = engine.export(result, self.files, out)
        self.assertEqual(1, len(exported['checks']))
        check = exported['checks'][0]
        self.assertTrue(check['independent_output_audit']['passed'])
        root = read_root(out / check['file'])
        trace = json.loads(next(out.glob('*_来源追溯.json')).read_text(encoding='utf-8'))
        return result, root, trace

    def assert_current_source_was_inserted(self, result, root, trace):
        proposal = next(p for p in result['proposals'] if FACT in p['old_text'])
        self.assertIn(proposal['decision'], ('accept', 'same'))
        candidate = proposal['candidates'][proposal['selected']]
        self.assertEqual(self.source['id'], candidate['doc_hash'])

        inserted = [p for p in paragraphs(root, FACT) if p.find('.//' + w('ins')) is not None]
        self.assertEqual(1, len(inserted))
        source_runs = [r for ins in inserted[0].iter(w('ins')) for r in ins.iter(w('r'))
                       if text(r) == FACT]
        self.assertEqual(1, len(source_runs))
        # Source wording is copied, but the target workpaper owns visible format.
        self.assertIsNone(source_runs[0].find('./' + w('rPr') + '/' + w('b')))

        old_deletions = [d for d in root.iter(w('del'))
                         if ''.join(t.text or '' for t in d.iter(w('delText'))) == FACT]
        self.assertEqual(1, len(old_deletions))
        self.assertIsNone(old_deletions[0].find('.//' + w('b')))
        record = next(r for r in trace['records'] if r['id'] == proposal['id'])
        self.assertEqual('source_copy', record['action'])
        self.assertEqual('prospectus', record['source_role'])
        self.assertEqual(self.source['id'], record['source_sha256'])
        self.assertTrue(record['insertion_revision_ids'])
        self.assertTrue(record['deletion_revision_ids'])

    def test_identical_words_are_copied_from_current_source_and_reversible(self):
        original = read_root(self.target['path'])
        original_paragraph = paragraphs(original, FACT)[0]
        source_paragraph = paragraphs(read_root(self.source['path']), FACT)[0]
        self.assertEqual(text(original_paragraph), text(source_paragraph))
        self.assertIsNone(original_paragraph.find('.//' + w('b')))
        self.assertIsNotNone(source_paragraph.find('.//' + w('b')))

        result, root, trace = self.export()
        self.assert_current_source_was_inserted(result, root, trace)
        accepted = copy.deepcopy(root)
        resolve_revisions(accepted, True)
        self.assertEqual(1, len(paragraphs(accepted, FACT)))
        self.assertIsNone(paragraphs(accepted, FACT)[0].find('.//' + w('b')))
        rejected = copy.deepcopy(root)
        resolve_revisions(rejected, False)
        self.assertEqual(structural_digest_content(original), structural_digest_content(rejected))
        self.assertIsNone(paragraphs(rejected, FACT)[0].find('.//' + w('b')))

    def test_unmatched_body_is_kept_without_revisions_while_matched_body_is_copied(self):
        def add_unmatched(body):
            conclusion = next(p for p in body.findall(w('p')) if text(p) == '三、核查结论')
            position = list(body).index(conclusion)
            for offset, value in enumerate(('（三）古籍修复业务情况', UNMATCHED)):
                paragraph = ET.Element(w('p'))
                run = ET.SubElement(paragraph, w('r'))
                ET.SubElement(run, w('t')).text = value
                body.insert(position + offset, paragraph)

        rewrite_document(self.target, add_unmatched)
        original = read_root(self.target['path'])
        self.assertFalse(paragraphs(read_root(self.source['path']), UNMATCHED))
        result, root, trace = self.export()
        retained = next(p for p in result['proposals'] if UNMATCHED in p['old_text'])
        self.assertEqual('keep', retained['decision'])
        self.assertEqual('no_correspondence_retained', retained['content_class'])
        retained_paragraphs = paragraphs(root, UNMATCHED)
        self.assertEqual(1, len(retained_paragraphs))
        retained_paragraph = retained_paragraphs[0]
        self.assertIsNone(retained_paragraph.find('.//' + w('ins')))
        self.assertIsNone(retained_paragraph.find('.//' + w('del')))
        self.assertEqual(structural_digest_content(paragraphs(original, UNMATCHED)[0]),
                         structural_digest_content(retained_paragraph))
        inventory = next(x for x in trace['object_inventory'] if x['text'] == UNMATCHED)
        self.assertEqual('protected', inventory['status'])
        self.assertEqual('no_correspondence_retained', inventory['content_class'])
        self.assertFalse(any(r['id'] == retained['id'] for r in trace['records']))
        self.assert_current_source_was_inserted(result, root, trace)


if __name__ == '__main__':
    unittest.main()

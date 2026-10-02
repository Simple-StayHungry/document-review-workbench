import copy
import tempfile
import unittest
import zipfile
from pathlib import Path
from xml.etree import ElementTree as E

from workbench.docxio import Package, revision_shape_issues
from workbench.output_audit import W, R, q, element_paths, revision_ids, audit_docx, Reader, allowed_style_pruning


def write_doc(path, blocks, extra=None):
    root = E.Element(q('document')); body = E.SubElement(root, q('body'))
    for b in blocks: body.append(copy.deepcopy(b))
    E.SubElement(body, q('sectPr'))
    entries = {'word/document.xml': E.tostring(root),
               '[Content_Types].xml': b'<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="xml" ContentType="application/xml"/></Types>'}
    entries.update(extra or {})
    with zipfile.ZipFile(path, 'w') as z:
        for n, b in entries.items(): z.writestr(n, b)


def para(text):
    p = E.Element(q('p')); E.SubElement(E.SubElement(p, q('r')), q('t')).text = text
    return p


def table(text='12.4', span='2'):
    t = E.Element(q('tbl')); grid = E.SubElement(t, q('tblGrid'))
    for i in range(2): E.SubElement(grid, q('gridCol'), {q('w'): '1500'})
    row = E.SubElement(t, q('tr')); cell = E.SubElement(row, q('tc'))
    pr = E.SubElement(cell, q('tcPr')); E.SubElement(pr, q('gridSpan'), {q('val'): span})
    E.SubElement(pr, q('vMerge'), {q('val'): 'restart'}); cell.append(para(text))
    return t


def image_para():
    p = para('来源图'); run = E.SubElement(p, q('r')); d = E.SubElement(run, q('drawing'))
    E.SubElement(d, '{http://schemas.openxmlformats.org/drawingml/2006/main}blip', {'{' + R + '}embed': 'rId1'})
    return p


def media_extra(data=b'actual-source-media'):
    return {'word/media/image.png': data, 'word/_rels/document.xml.rels':
            ('<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="' + R + '/image" Target="media/image.png"/></Relationships>').encode()}


class OutputAuditTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)

    def export(self, source_blocks=None, extra=None):
        old = self.path/'old.docx'; source = self.path/'source.docx'; out = self.path/'out.docx'
        write_doc(old, [para('旧人员委派'), para('独立项目组判断')])
        write_doc(source, source_blocks or [para('集团董事行使表决权。')], extra)
        pkg = Package(old); src = Package(source)
        originals = list(pkg.body)[:2]
        target_paths = element_paths(pkg.root, originals[:1])
        protected_base = element_paths(pkg.root, originals[1:])[0]
        source_elements = list(src.body)[:-1]
        source_paths = element_paths(src.root, source_elements)
        inserted = pkg.replace(originals[:1], source_elements, source=src, whole=True)
        pkg.save(out)
        rec = {'id': 'one', 'action': 'source_copy', 'source_file': str(source),
               'source_sha256': src.hash, 'source_role': 'source', 'source_paths': source_paths,
               'target_paths': target_paths, 'deletion_paths': element_paths(pkg.root, originals[:1]),
               'output_paths': element_paths(pkg.root, inserted),
               'insertion_revision_ids': revision_ids(inserted, 'ins'),
               'deletion_revision_ids': revision_ids(originals[:1], 'del')}
        protected = [{'baseline_path': protected_base, 'output_path': element_paths(pkg.root, originals[1:])[0]}]
        return old, source, out, rec, protected

    def mutate(self, path, action):
        with zipfile.ZipFile(path) as z: parts = {n: z.read(n) for n in z.namelist()}
        action(parts)
        with zipfile.ZipFile(path, 'w') as z:
            for n, b in parts.items(): z.writestr(n, b)

    def test_whole_copy_real_output_and_same_source_are_verified(self):
        old, source, out, rec, protected = self.export([para('旧人员委派')])
        report = audit_docx(out, old, [rec], protected)
        self.assertTrue(report['passed'], report['errors'])
        self.assertGreater(report['revision_count'], 0)

    def test_destination_format_transform_is_independently_checked(self):
        old=self.path/'layout-old.docx';source=self.path/'layout-source.docx';out=self.path/'layout-out.docx'
        oldp=para('旧正文');pp=E.Element(q('pPr'));E.SubElement(pp,q('jc'),{q('val'):'both'});E.SubElement(pp,q('spacing'),{q('line'):'360'});E.SubElement(pp,q('ind'),{q('firstLine'):'480'});oldp.insert(0,pp)
        srcp=para('新正文');spp=E.Element(q('pPr'));E.SubElement(spp,q('jc'),{q('val'):'center'});E.SubElement(spp,q('spacing'),{q('line'):'720'});srcp.insert(0,spp)
        write_doc(old,[oldp]);write_doc(source,[srcp])
        pkg=Package(old);src=Package(source);original=list(pkg.body)[:1];source_nodes=list(src.body)[:1]
        inserted=pkg.replace(original,source_nodes,source=src,whole=True,format_templates=[copy.deepcopy(original[0])]);pkg.save(out)
        rec={'id':'layout','action':'source_copy','source_file':str(source),'source_sha256':src.hash,'source_role':'source',
             'source_paths':element_paths(src.root,source_nodes),'target_paths':element_paths((baseline:=Package(old)).root,list(baseline.body)[:1]),
             'deletion_paths':element_paths(pkg.root,original),'output_paths':element_paths(pkg.root,inserted),
             'insertion_revision_ids':revision_ids(inserted,'ins'),'deletion_revision_ids':revision_ids(original,'del'),
             'transforms':['destination_format','technical_ids','table_layout']}
        good=audit_docx(out,old,[rec]);self.assertTrue(good['passed'],good['errors'])
        def corrupt(parts):
            root=E.fromstring(parts['word/document.xml']);body=root.find(q('body'))
            # Find the inserted paragraph-mark revision and corrupt its paragraph shell.
            for node in body:
                if node.tag!=q('p') or node.find('./'+q('pPr')+'/'+q('rPr')+'/'+q('ins')) is None:continue
                ppr=node.find(q('pPr'));jc=ppr.find(q('jc'))
                if jc is None:jc=E.SubElement(ppr,q('jc'))
                jc.set(q('val'),'center');break
            parts['word/document.xml']=E.tostring(root)
        self.mutate(out,corrupt)
        bad=audit_docx(out,old,[rec]);self.assertFalse(bad['passed']);self.assertIn('段落版式','\n'.join(bad['errors']))

    def test_changed_inserted_text_fails_source_readback(self):
        old, source, out, rec, protected = self.export()
        def corrupt(parts):
            root = E.fromstring(parts['word/document.xml'])
            for t in root.findall('.//w:ins/w:r/w:t', {'w': W}): t.text = '仍然是旧文字'
            parts['word/document.xml'] = E.tostring(root)
        self.mutate(out, corrupt)
        report = audit_docx(out, old, [rec], protected)
        self.assertFalse(report['passed']); self.assertIn('当前来源不一致', '\n'.join(report['errors']))

    def test_same_cell_text_wrong_merge_structure_fails(self):
        old, source, out, rec, protected = self.export([table()])
        def corrupt(parts):
            root = E.fromstring(parts['word/document.xml'])
            root.find('.//w:gridSpan', {'w': W}).set(q('val'), '1')
            parts['word/document.xml'] = E.tostring(root)
        self.mutate(out, corrupt)
        self.assertFalse(audit_docx(out, old, [rec], protected)['passed'])

    def test_real_media_hash_and_relationship_are_verified(self):
        old, source, out, rec, protected = self.export([image_para()], media_extra())
        report = audit_docx(out, old, [rec], protected)
        self.assertTrue(report['passed'], report['errors'])
        def corrupt(parts):
            for n in parts:
                if n.startswith('word/media/'): parts[n] = b'different-year-image'
        self.mutate(out, corrupt)
        self.assertFalse(audit_docx(out, old, [rec], protected)['passed'])

    def test_untracked_protected_change_and_reposting_fail(self):
        old, source, out, rec, protected = self.export()
        def corrupt(parts):
            root = E.fromstring(parts['word/document.xml'])
            p = root
            for i in protected[0]['output_path']: p = p[i]
            p.find('.//' + q('t')).text = '被偷偷改变的结论'
            parts['word/document.xml'] = E.tostring(root)
        self.mutate(out, corrupt)
        report = audit_docx(out, old, [rec], protected)
        self.assertFalse(report['passed']); self.assertFalse(report['protected_unchanged'])
        self.assertFalse(report['reject_restores_baseline'])

    def test_unregistered_or_wrong_revision_ids_fail(self):
        old, source, out, rec, protected = self.export()
        rec['insertion_revision_ids'] = ['not-actual']
        self.assertFalse(audit_docx(out, old, [rec], protected)['passed'])
        self.assertFalse(audit_docx(out, old, [], protected)['passed'])

    def test_changed_source_bytes_and_old_target_source_fail(self):
        old, source, out, rec, protected = self.export()
        source.write_bytes(source.read_bytes() + b'changed')
        self.assertFalse(audit_docx(out, old, [rec], protected)['passed'])
        rec['source_file'] = str(old); rec['source_sha256'] = Reader(old).sha256
        self.assertFalse(audit_docx(out, old, [rec], protected)['passed'])

    def test_source_merge_requires_verified_full_source(self):
        old = self.path/'old.docx'; source = self.path/'source.docx'; out = self.path/'out.docx'
        write_doc(old, [para('旧段前半'), para('新版源正文后半')]); write_doc(source, [para('新版前半。新版源正文后半')])
        pkg = Package(old); src = Package(source); originals = list(pkg.body)[:2]
        target_paths = element_paths(pkg.root, originals)
        inserted = pkg.replace(originals[:1], list(src.body)[:1], source=src, whole=True)
        pkg.replace(originals[1:], [], source=src, whole=True); pkg.save(out)
        common = {'source_file': str(source), 'source_sha256': src.hash, 'source_role': 'source',
                  'source_paths': element_paths(src.root, list(src.body)[:1])}
        one = dict(common, id='one', action='source_copy', target_paths=target_paths[:1],
                   deletion_paths=element_paths(pkg.root, originals[:1]), output_paths=element_paths(pkg.root, inserted),
                   insertion_revision_ids=revision_ids(inserted, 'ins'), deletion_revision_ids=revision_ids(originals[:1], 'del'))
        two = dict(common, id='two', action='source_merge', merge_into='one', target_paths=target_paths[1:],
                   deletion_paths=element_paths(pkg.root, originals[1:]), output_paths=[], insertion_revision_ids=[],
                   deletion_revision_ids=revision_ids(originals[1:], 'del'))
        report = audit_docx(out, old, [one, two]); self.assertTrue(report['passed'], report['errors'])
        two['merge_into'] = 'missing'; self.assertFalse(audit_docx(out, old, [one, two])['passed'])

    def test_existing_footnote_preserved_source_note_added(self):
        p = para('源文带脚注'); E.SubElement(E.SubElement(p, q('r')), q('footnoteReference'), {q('id'): '1'})
        note = ('<w:footnotes xmlns:w="' + W + '"><w:footnote w:id="1"><w:p><w:r><w:t>实际脚注正文</w:t></w:r></w:p></w:footnote></w:footnotes>').encode()
        old, source, out, rec, protected = self.export([p], {'word/footnotes.xml': note})
        report = audit_docx(out, old, [rec], protected)
        self.assertTrue(report['passed'], report['errors'])
        def corrupt(parts): parts['word/footnotes.xml'] = parts['word/footnotes.xml'].replace('实际脚注正文'.encode(), '伪造脚注正文'.encode())
        self.mutate(out, corrupt)
        self.assertFalse(audit_docx(out, old, [rec], protected)['passed'])

    def test_complex_chart_is_blocked(self):
        p = para('原生图表'); E.SubElement(E.SubElement(p, q('r')), '{http://schemas.openxmlformats.org/drawingml/2006/chart}chart')
        old, source, out, rec, protected = self.export([p])
        report = audit_docx(out, old, [rec], protected)
        self.assertFalse(report['passed']); self.assertIn('尚未支持', '\n'.join(report['errors']))

    def test_exact_source_sentence_excerpt_and_unsafe_boundary(self):
        old, source, out, rec, protected = self.export([para('旧人员委派')])
        write_doc(source, [para('前一句。旧人员委派')])
        rec['source_sha256'] = Reader(source).sha256
        rec['source_span'] = {'start': 4, 'end': 9, 'block': 0}
        report = audit_docx(out, old, [rec], protected)
        self.assertTrue(report['passed'], report['errors'])
        rec['source_span']['start'] = 5
        self.assertFalse(audit_docx(out, old, [rec], protected)['passed'])

    def test_table_width_is_never_projected_from_target_or_final_section(self):
        old = self.path/'sections.docx'; source = self.path/'wide-table.docx'; out = self.path/'sections-out.docx'
        section_break = para('节分界'); pp = E.Element(q('pPr')); section_break.insert(0, pp)
        sp = E.SubElement(pp, q('sectPr')); E.SubElement(sp, q('pgSz'), {q('w'): '8000'})
        E.SubElement(sp, q('pgMar'), {q('left'): '1000', q('right'): '1000'})
        write_doc(old, [para('目标位置'), section_break, para('末节')])
        def wide_final(parts):
            root = E.fromstring(parts['word/document.xml']); s = root.find('./w:body/w:sectPr', {'w': W})
            E.SubElement(s, q('pgSz'), {q('w'): '20000'}); E.SubElement(s, q('pgMar'), {q('left'): '1000', q('right'): '1000'})
            parts['word/document.xml'] = E.tostring(root)
        self.mutate(old, wide_final)
        t = table(); cols = t.find(q('tblGrid'))
        for c in cols: c.set(q('w'), '5000')
        write_doc(source, [t]); pkg = Package(old); src = Package(source)
        inserted=pkg.replace(list(pkg.body)[:1], list(src.body)[:1], source=src, whole=True);pkg.save(out)
        self.assertEqual([int(e.get(q('w'))) for e in inserted[0].find(q('tblGrid'))], [5000,5000])
        self.assertFalse([x for x in revision_shape_issues(pkg.root) if '网格宽度无效' in x])

    def test_wide_source_table_is_pasted_complete_without_geometry_mutation(self):
        old = self.path/'oversized-old.docx'; source = self.path/'oversized-source.docx';out=self.path/'oversized-out.docx'
        old_table = table(); source_table = table('25.8')
        for c in old_table.find(q('tblGrid')): c.set(q('w'), '1200')
        for c in source_table.find(q('tblGrid')): c.set(q('w'), '5000')
        spr=E.Element(q('tblPr'));source_table.insert(0,spr);E.SubElement(spr,q('jc'),{q('val'):'right'});E.SubElement(spr,q('tblW'),{q('type'):'dxa',q('w'):'10000'})
        tr=source_table.find(q('tr'));trpr=E.Element(q('trPr'));tr.insert(0,trpr);E.SubElement(trpr,q('trHeight'),{q('val'):'480',q('hRule'):'exact'})
        write_doc(old, [old_table]); write_doc(source, [source_table])
        pkg = Package(old); src = Package(source)
        sect = pkg.body.find(q('sectPr')); E.SubElement(sect, q('pgSz'), {q('w'): '9000'});E.SubElement(sect, q('pgMar'), {q('left'): '1000', q('right'): '1000'})
        inserted=pkg.replace(list(pkg.body)[:1], list(src.body)[:1], source=src, whole=True);pkg.save(out)
        got=inserted[0]
        self.assertEqual([int(c.get(q('w'))) for c in got.find(q('tblGrid'))],[5000,5000])
        self.assertEqual(got.find('./'+q('tblPr')+'/'+q('jc')).get(q('val')),'right')
        self.assertEqual(got.find('./'+q('tblPr')+'/'+q('tblW')).get(q('w')),'10000')
        h=got.find('.//'+q('trHeight'));self.assertEqual((h.get(q('val')),h.get(q('hRule'))),('480','exact'))
        self.assertIsNone(got.find('.//'+q('cantSplit')))

    def test_pasted_rows_preserve_source_properties_without_added_cantsplit(self):
        old = self.path/'row-old.docx'; source = self.path/'row-source.docx'; out = self.path/'row-out.docx'
        source_table = table('215,100.75')
        tr = source_table.find(q('tr')); trpr = E.Element(q('trPr')); tr.insert(0, trpr)
        E.SubElement(trpr, q('trHeight'), {q('val'): '420', q('hRule'): 'atLeast'})
        write_doc(old, [table()]); write_doc(source, [source_table])
        pkg = Package(old); src = Package(source); original = list(pkg.body)[0]
        inserted = pkg.replace([original], list(src.body)[:1], source=src, whole=True); pkg.save(out)
        self.assertIsNone(original.find('.//'+q('cantSplit')))
        self.assertIsNone(inserted[0].find('.//'+q('cantSplit')))
        h = inserted[0].find('.//'+q('trHeight'))
        self.assertEqual((h.get(q('val')), h.get(q('hRule'))), ('420', 'atLeast'))
        self.assertEqual([c.get(q('w')) for c in inserted[0].find(q('tblGrid'))], ['1500','1500'])
        self.assertEqual("".join(n.text or "" for n in inserted[0].iter(q("t"))), "215,100.75")

    def test_identical_native_wide_table_is_copied_without_artificial_shrink(self):
        old = self.path/'native-old.docx'; source = self.path/'native-source.docx'; out = self.path/'native-out.docx'
        old_table = table(); source_table = table('215,100.75')
        for t in (old_table, source_table):
            for c in t.find(q('tblGrid')): c.set(q('w'), '4000')
            pr = E.Element(q('tblPr')); t.insert(0, pr)
            E.SubElement(pr, q('tblW'), {q('w'): '8000', q('type'): 'dxa'})
            E.SubElement(pr, q('jc'), {q('val'): 'center'})
            E.SubElement(pr, q('tblLayout'), {q('type'): 'fixed'})
        write_doc(old, [old_table]); write_doc(source, [source_table])
        pkg = Package(old); src = Package(source)
        s = pkg.body.find(q('sectPr'))
        E.SubElement(s, q('pgSz'), {q('w'): '9200'})
        E.SubElement(s, q('pgMar'), {q('left'): '1000', q('right'): '1000'})
        original = list(pkg.body)[0]
        inserted = pkg.replace([original], list(src.body)[:1], source=src, whole=True); pkg.save(out)
        self.assertEqual([int(c.get(q('w'))) for c in inserted[0].find(q('tblGrid'))], [4000, 4000])
        self.assertIsNot(original, inserted[0])
        self.assertTrue(original.findall('.//' + q('del')))
        self.assertTrue(inserted[0].findall('.//' + q('ins')))
        self.assertEqual(''.join(t.text or '' for t in inserted[0].iter(q('t'))), '215,100.75')
        self.assertFalse([x for x in revision_shape_issues(pkg.root) if '宽度' in x])
        # Readback no longer re-imposes old geometry. It only rejects an invalid
        # inserted SOURCE grid because fit safety was proved before mutation.
        inserted[0].find(q('tblGrid'))[0].set(q('w'), '0')
        self.assertTrue([x for x in revision_shape_issues(pkg.root) if '网格宽度无效' in x])

    def test_native_percentage_table_copies_source_grid_not_original_rounding(self):
        old = self.path/'pct-old.docx'; source = self.path/'pct-source.docx'; out = self.path/'pct-out.docx'
        old_table = table(); source_table = table('215,100.75')
        for t, width in ((old_table, 4000), (source_table, 3999)):
            for c in t.find(q('tblGrid')): c.set(q('w'), str(width))
            pr = E.Element(q('tblPr')); t.insert(0, pr)
            E.SubElement(pr, q('tblW'), {q('w'): '5500', q('type'): 'pct'})
            E.SubElement(pr, q('jc'), {q('val'): 'center'})
            E.SubElement(pr, q('tblLayout'), {q('type'): 'fixed'})
        write_doc(old, [old_table]); write_doc(source, [source_table])
        pkg = Package(old); src = Package(source)
        s = pkg.body.find(q('sectPr'))
        E.SubElement(s, q('pgSz'), {q('w'): '9200'})
        E.SubElement(s, q('pgMar'), {q('left'): '1000', q('right'): '1000'})
        inserted = pkg.replace(list(pkg.body)[:1], list(src.body)[:1], source=src, whole=True); pkg.save(out)
        self.assertEqual([int(c.get(q('w'))) for c in inserted[0].find(q('tblGrid'))], [3999, 3999])
        self.assertEqual(inserted[0].find('./' + q('tblPr') + '/' + q('tblW')).get(q('w')), '5500')
        self.assertFalse([x for x in revision_shape_issues(pkg.root) if '宽度' in x])

    def test_original_styles_cannot_be_overwritten(self):
        old, source, out, rec, protected = self.export()
        style = ('<w:styles xmlns:w="' + W + '"><w:style w:styleId="Normal"><w:rPr><w:b/></w:rPr></w:style></w:styles>').encode()
        self.mutate(old, lambda parts: parts.update({'word/styles.xml': style}))
        self.mutate(out, lambda parts: parts.update({'word/styles.xml': style.replace(b'<w:b/>', b'<w:i/>')}))
        self.assertFalse(audit_docx(out, old, [rec], protected)['passed'])

    def style_pruning_parts(self, count=4081, remove=2):
        root = E.Element(q('styles'))
        for i in range(count):
            style = E.SubElement(root, q('style'), {q('styleId'): 's' + str(i), q('type'): 'paragraph'})
            E.SubElement(style, q('name'), {q('val'): 'Style ' + str(i)})
        old = {'word/styles.xml': E.tostring(root)}
        for item in list(root)[count-remove:]: root.remove(item)
        return old, {'word/styles.xml': E.tostring(root)}

    def test_only_minimal_style_limit_cleanup_is_allowed(self):
        old, new = self.style_pruning_parts()
        ids, proof = allowed_style_pruning(old, new)
        self.assertEqual(ids, {'s4079', 's4080'}); self.assertTrue(proof['allowed'])
        for count, remove in [(4000, 1), (4081, 3), (4081, 1)]:
            old, new = self.style_pruning_parts(count, remove)
            self.assertFalse(allowed_style_pruning(old, new)[0])

    def test_style_pruning_rejects_body_header_numbering_and_field_dependencies(self):
        for filename, xml in [
            ('word/header1.xml', '<w:p><w:pPr><w:pStyle w:val="s4080"/></w:pPr></w:p>'),
            ('word/numbering.xml', '<w:numbering><w:abstractNum><w:styleLink w:val="s4080"/></w:abstractNum></w:numbering>'),
            ('word/document.xml', '<w:document><w:instrText> STYLE</w:instrText><w:instrText>REF "Style 4080" </w:instrText></w:document>'),
            ('word/footnotes.xml', '<w:footnotes><w:fldSimple w:instr=" STYLEREF s4080 "/></w:footnotes>')]:
            with self.subTest(filename=filename):
                old, new = self.style_pruning_parts()
                xml = xml.replace('>', ' xmlns:w="'+W+'">', 1).encode()
                old[filename] = new[filename] = xml
                ids, proof = allowed_style_pruning(old, new)
                self.assertFalse(ids); self.assertIn('s4080', proof['protected_removed_ids'])

    def test_style_pruning_rejects_live_style_dependency_and_default(self):
        for mode in ('link', 'default'):
            old, new = self.style_pruning_parts()
            for entries in (old, new):
                root = E.fromstring(entries['word/styles.xml'])
                if mode == 'link': E.SubElement(root[0], q('basedOn'), {q('val'): 's4080'})
                elif entries is old: root[-1].set(q('default'), '1')
                entries['word/styles.xml'] = E.tostring(root)
            self.assertFalse(allowed_style_pruning(old, new)[0])

    def test_numeric_style_ids_are_not_revision_ids_or_font_sizes(self):
        old, new = self.style_pruning_parts()
        for entries in (old, new):
            entries['word/styles.xml'] = entries['word/styles.xml'].replace(b's4079', b'4079').replace(b's4080', b'4080')
            entries['word/document.xml'] = (
                '<w:document xmlns:w="'+W+'"><w:ins w:id="4079"><w:r>'
                '<w:rPr><w:sz w:val="4080"/></w:rPr><w:t>正文</w:t>'
                '</w:r></w:ins></w:document>').encode()
        ids, proof = allowed_style_pruning(old, new)
        self.assertEqual(ids, {'4079', '4080'})
        self.assertTrue(proof['allowed'])

    def test_readback_allows_proven_pruning_but_rejects_changed_retained_definition(self):
        old, source, out, rec, protected = self.export()
        a, b = self.style_pruning_parts()
        self.mutate(old, lambda parts: parts.update(a)); self.mutate(out, lambda parts: parts.update(b))
        self.assertTrue(audit_docx(out, old, [rec], protected)['passed'])
        root = E.fromstring(b['word/styles.xml']); E.SubElement(root[0], q('hidden'))
        self.mutate(out, lambda parts: parts.update({'word/styles.xml': E.tostring(root)}))
        self.assertFalse(audit_docx(out, old, [rec], protected)['passed'])

    def test_whole_writer_blocks_unsafe_floating_anchors_before_mutation(self):
        for anchor, with_paragraph in [('text', False), ('page', True), ('margin', True)]:
            with self.subTest(anchor=anchor, with_paragraph=with_paragraph):
                t = table('1')
                pr = E.Element(q('tblPr')); t.insert(0, pr)
                E.SubElement(pr, q('tblW'), {q('type'): 'dxa', q('w'): '3000'})
                E.SubElement(pr, q('tblLayout'), {q('type'): 'fixed'})
                E.SubElement(pr, q('tblpPr'), {q('horzAnchor'): 'margin', q('tblpXSpec'): 'center',
                                             q('vertAnchor'): anchor, q('tblpY'): '289'})
                blocks = ([para('单位：万元')] if with_paragraph else []) + [t]
                old = self.path/'float-old.docx'; source = self.path/'float-source.docx'
                write_doc(old, blocks); write_doc(source, blocks)
                pkg = Package(old); src = Package(source)
                root_before = E.tostring(pkg.root); parts_before = dict(pkg.entries)
                with self.assertRaisesRegex(ValueError, '浮动表'):
                    pkg.replace(list(pkg.body)[:-1], list(src.body)[:-1], source=src, whole=True)
                self.assertEqual(E.tostring(pkg.root), root_before)
                self.assertEqual(pkg.entries, parts_before)
                self.assertEqual(revision_ids(list(pkg.body)), [])

    def test_section_end_paragraph_blocks_copy_before_any_mutation(self):
        # Guangzhou scope514 ends with a real text paragraph whose pPr owns a
        # landscape section, rather than an empty separator paragraph. The old
        # writer deleted its runs only and pasted the new table after this break.
        old = self.path/'section-boundary.docx'; source = self.path/'source-boundary.docx'
        boundary = para('项目公司不存在以BT、BOT、PPP等特殊形式承接工程的情况。')
        pr = E.Element(q('pPr')); boundary.insert(0, pr)
        sect = E.SubElement(pr, q('sectPr'))
        E.SubElement(sect, q('pgSz'), {q('w'): '16838', q('h'): '11906', q('orient'): 'landscape'})
        E.SubElement(sect, q('pgMar'), {q('left'): '1440', q('right'): '1440'})
        write_doc(old, [para('表：主要在建项目'), para('单位：万元'), table(), boundary])
        write_doc(source, [para('当前来源表'), table('25.8'), para('当前来源说明')])
        pkg = Package(old); src = Package(source)
        root_before = E.tostring(pkg.root); parts_before = dict(pkg.entries)
        with self.assertRaisesRegex(ValueError, '原生节分界'):
            pkg.replace(list(pkg.body)[:4], list(src.body)[:3], source=src, whole=True)
        self.assertEqual(E.tostring(pkg.root), root_before)
        self.assertEqual(pkg.entries, parts_before)
        self.assertEqual(revision_ids(list(pkg.body)), [])


if __name__ == '__main__': unittest.main()

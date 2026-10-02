"""Full OMML source copying, real revisions, dependency guards and read-back audit."""
import copy
import tempfile
import unittest
import zipfile
from pathlib import Path
from xml.etree import ElementTree as E

from workbench.docxio import (M, W, R, W14, Package, m, w, text,
    native_math_copy_reason, complex_reference_reason, resolve_revisions,
    structural_digest_content, validate_package)
from workbench.output_audit import audit_docx, element_paths, revision_ids
from test_output_audit import write_doc, para


def equation(value='Y', marker=False, display=False):
    p=E.Element(w('p'), {'{'+W14+'}paraId':'00000001'})
    parent=E.SubElement(p,m('oMathPara')) if display else p
    formula=E.SubElement(parent,m('oMath'))
    sub=E.SubElement(formula,m('sSub'))
    base=E.SubElement(sub,m('e')); r=E.SubElement(base,m('r'))
    if marker:
        props=E.SubElement(r,m('rPr')); E.SubElement(props,m('sty'),{m('val'):'b'})
    E.SubElement(r,m('t')).text=value
    suffix=E.SubElement(sub,m('sub')); sr=E.SubElement(suffix,m('r'))
    E.SubElement(sr,m('t')).text='i'
    if not display:
        run=E.SubElement(p,w('r')); E.SubElement(run,w('t')).text='：第i年的规模容量，单位兆瓦。'
    return p


def extras(normal='SourceNormal',normal_bold=False,math_font='Cambria Math',default_font='Times New Roman'):
    settings=E.Element(w('settings')); mp=E.SubElement(settings,m('mathPr'))
    E.SubElement(mp,m('mathFont'),{m('val'):math_font})
    styles=E.Element(w('styles')); dd=E.SubElement(styles,w('docDefaults'))
    rp=E.SubElement(E.SubElement(dd,w('rPrDefault')),w('rPr'))
    E.SubElement(rp,w('rFonts'),{w('ascii'):default_font})
    ns=E.SubElement(styles,w('style'),{w('type'):'paragraph',w('default'):'1',w('styleId'):normal})
    E.SubElement(ns,w('name'),{w('val'):'Normal'})
    if normal_bold:E.SubElement(E.SubElement(ns,w('rPr')),w('b'))
    rels=('<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
          '<Relationship Id="rId1" Type="'+R+'/styles" Target="styles.xml"/>'
          '<Relationship Id="rId2" Type="'+R+'/settings" Target="settings.xml"/>'
          '</Relationships>')
    return {'word/settings.xml':E.tostring(settings),'word/styles.xml':E.tostring(styles),
            'word/_rels/document.xml.rels':rels.encode()}


class NativeMathTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory(prefix='workbench-native-math-')
        self.addCleanup(temp.cleanup);self.base=Path(temp.name)

    def export(self,display=False,old_value='Y',source_value='Y'):
        old=self.base/'old.docx';src=self.base/'source.docx';out=self.base/'out.docx'
        protected=para('独立核查判断保留。');protected.set('{'+W14+'}paraId','00000002')
        write_doc(old,[equation(old_value,display=display),protected],extras(normal='TargetNormal'))
        write_doc(src,[equation(source_value,marker=True,display=display)],extras(normal_bold=True))
        target=Package(old);source=Package(src)
        original=list(target.body)[0]
        target_paths=element_paths(target.root,[original])
        source_nodes=list(source.body)[:-1]
        self.assertEqual('',native_math_copy_reason(target,source,source_nodes))
        inserted=target.replace([original],source_nodes,source=source,whole=True)
        target.save(out)
        rec={'id':'formula','action':'source_copy','source_file':str(src),
             'source_sha256':source.hash,'source_role':'prospectus',
             'source_paths':element_paths(source.root,source_nodes),'target_paths':target_paths,
             'deletion_paths':element_paths(target.root,[original]),
             'output_paths':element_paths(target.root,inserted),
             'insertion_revision_ids':revision_ids(inserted,'ins'),
             'deletion_revision_ids':revision_ids([original],'del')}
        return old,src,out,rec

    def test_identical_inline_formula_is_source_object_not_old_reposted(self):
        old,src,out,rec=self.export()
        p=Package(out)
        self.assertEqual('Yi：第i年的规模容量，单位兆瓦。独立核查判断保留。',text(p.root))
        deleted=p.root.find('.//'+w('del')+'/'+m('oMath'))
        inserted=p.root.find('.//'+w('ins')+'/'+m('oMath'))
        self.assertIsNotNone(deleted);self.assertIsNotNone(inserted)
        self.assertIsNone(deleted.find('.//'+m('sty')))
        self.assertEqual('b',inserted.find('.//'+m('sty')).get(m('val')))
        self.assertEqual([],validate_package(out))
        audit=audit_docx(out,old,[rec],[])
        self.assertTrue(audit['passed'],audit['errors'])
        accepted=copy.deepcopy(p.root);resolve_revisions(accepted,True)
        self.assertEqual(1,len(list(accepted.iter(m('oMath')))))
        self.assertEqual('b',accepted.find('.//'+m('sty')).get(m('val')))
        rejected=copy.deepcopy(p.root);resolve_revisions(rejected,False)
        self.assertEqual(structural_digest_content(Package(old).root),structural_digest_content(rejected))
        self.assertEqual(1,len(list(rejected.iter(m('oMath')))))
        self.assertIsNone(rejected.find('.//'+m('sty')))

    def test_changed_display_equation_is_complete_reversible_object(self):
        old,src,out,rec=self.export(display=True,old_value='Old',source_value='New')
        p=Package(out)
        self.assertEqual(1,len(p.root.findall('.//'+w('del')+'/'+m('oMathPara'))))
        self.assertEqual(1,len(p.root.findall('.//'+w('ins')+'/'+m('oMathPara'))))
        accepted=copy.deepcopy(p.root);resolve_revisions(accepted,True)
        self.assertIn('Newi',text(accepted));self.assertNotIn('Old',text(accepted))
        self.assertEqual(1,len(list(accepted.iter(m('oMathPara')))))
        rejected=copy.deepcopy(p.root);resolve_revisions(rejected,False)
        self.assertEqual(structural_digest_content(Package(old).root),structural_digest_content(rejected))
        audit=audit_docx(out,old,[rec],[]);self.assertTrue(audit['passed'],audit['errors'])

    def mutate(self,path,fn):
        with zipfile.ZipFile(path) as archive:parts={n:archive.read(n) for n in archive.namelist()}
        fn(parts)
        with zipfile.ZipFile(path,'w') as archive:
            for n,value in parts.items():archive.writestr(n,value)

    def test_readback_rejects_changed_formula_even_when_explanation_matches(self):
        old,src,out,rec=self.export()
        def alter(parts):
            root=E.fromstring(parts['word/document.xml'])
            root.find('.//'+w('ins')+'/'+m('oMath')+'//'+m('t')).text='FAKE'
            parts['word/document.xml']=E.tostring(root)
        self.mutate(out,alter)
        audit=audit_docx(out,old,[rec],[])
        self.assertFalse(audit['passed']);self.assertTrue(any('当前来源不一致' in e for e in audit['errors']))

    def test_readback_rejects_missing_formula_revision_despite_paragraph_marker(self):
        old,src,out,rec=self.export()
        def alter(parts):
            root=E.fromstring(parts['word/document.xml'])
            for p in root.iter(w('p')):
                for child in list(p):
                    if child.tag==w('ins') and child.find(m('oMath')) is not None:
                        index=list(p).index(child);p.remove(child)
                        for sub in child:p.insert(index,sub)
            parts['word/document.xml']=E.tostring(root)
        self.mutate(out,alter)
        audit=audit_docx(out,old,[rec],[])
        self.assertFalse(audit['passed'])

    def test_readback_rejects_different_inherited_normal_style(self):
        old,src,out,rec=self.export()
        def alter(parts):
            root=E.fromstring(parts['word/document.xml'])
            p=next(p for p in root.iter(w('p')) if p.find('.//'+w('ins')+'/'+m('oMath')) is not None)
            pr=p.find(w('pPr'));pr.remove(pr.find(w('pStyle')))
            parts['word/document.xml']=E.tostring(root)
        self.mutate(out,alter)
        audit=audit_docx(out,old,[rec],[])
        self.assertFalse(audit['passed']);self.assertTrue(any('来源段落样式' in e for e in audit['errors']))

    def test_different_global_math_or_default_fonts_block_before_any_mutation(self):
        for changes in ({'math_font':'Other Math'},{'default_font':'Arial'}):
            with self.subTest(changes=changes):
                old=self.base/'old.docx';src=self.base/'source.docx'
                write_doc(old,[equation()],extras())
                write_doc(src,[equation(marker=True)],extras(**changes))
                p=Package(old);source=Package(src);before=E.tostring(p.root);parts=dict(p.entries)
                self.assertTrue(native_math_copy_reason(p,source,list(source.body)[:-1]))
                with self.assertRaises(ValueError):p.replace([list(p.body)[0]],[list(source.body)[0]],source=source,whole=True)
                self.assertEqual(before,E.tostring(p.root));self.assertEqual(parts,p.entries)

    def test_partial_or_annotated_formula_is_not_supported(self):
        p=equation();f=p.find(m('oMath'));p.remove(f);E.SubElement(p,w('r')).append(f)
        self.assertTrue(complex_reference_reason([p]))
        p=equation();E.SubElement(p.find(m('oMath')),w('bookmarkStart'),{w('id'):'1',w('name'):'x'})
        self.assertTrue(complex_reference_reason([p]))
        p=equation();f=p.find(m('oMath'));p.remove(f);p.append(list(f)[0])
        self.assertTrue(complex_reference_reason([p]))

    def test_source_math_settings_tamper_fails_independent_readback(self):
        old,src,out,rec=self.export()
        def alter(parts):
            settings=E.fromstring(parts['word/settings.xml'])
            settings.find('.//'+m('mathFont')).set(m('val'),'Other Math')
            parts['word/settings.xml']=E.tostring(settings)
        self.mutate(out,alter)
        audit=audit_docx(out,old,[rec],[])
        self.assertFalse(audit['passed']);self.assertTrue(any('全局设置' in e for e in audit['errors']))


if __name__=='__main__':unittest.main()

"""Tracked whole-block copies preserve bookmark identity, order and pairing."""
import copy
import tempfile
import unittest
from pathlib import Path
from xml.etree import ElementTree as E

from workbench.docxio import Package, w, text, resolve_revisions, validate_package
from workbench.output_audit import Reader, audit_docx, element_paths, revision_ids, _bookmark_manifest, _resolve
from test_output_audit import write_doc, para


def start(number):
    return E.Element(w('bookmarkStart'),{w('id'):str(number),w('name'):'bookmark'+str(number)})


def end(number):
    return E.Element(w('bookmarkEnd'),{w('id'):str(number)})


class BookmarkCopyTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory(prefix='bookmark-source-copy-')
        self.addCleanup(temp.cleanup);self.path=Path(temp.name)

    def export(self,old_blocks,source_blocks,target_index=0):
        old=self.path/'old.docx';src=self.path/'source.docx';out=self.path/'out.docx'
        write_doc(old,old_blocks);write_doc(src,source_blocks)
        target=Package(old);source=Package(src)
        old_node=list(target.body)[target_index];new=list(source.body)[:-1]
        target_paths=element_paths(target.root,[old_node])
        inserted=target.replace([old_node],new,source=source,whole=True)
        target.save(out)
        rec={'id':'bookmarks','action':'source_copy','source_file':str(src),
             'source_sha256':source.hash,'source_role':'prospectus',
             'source_paths':element_paths(source.root,new),'target_paths':target_paths,
             'deletion_paths':element_paths(target.root,[old_node]),
             'output_paths':element_paths(target.root,inserted),
             'insertion_revision_ids':revision_ids(inserted,'ins'),
             'deletion_revision_ids':revision_ids([old_node],'del')}
        return old,src,out,rec

    def verify_outcomes(self,old,out,rec):
        audit=audit_docx(out,old,[rec],[])
        self.assertTrue(audit['passed'],audit['errors'])
        expected=_bookmark_manifest(Reader(old).root)
        output=Reader(out).root
        self.assertEqual(expected,_bookmark_manifest(output))
        for accept in (False,True):
            resolved=_resolve(output,accept)
            self.assertEqual(expected,_bookmark_manifest(resolved))
            mutable=copy.deepcopy(output);resolve_revisions(mutable,accept)
            self.assertEqual(expected,_bookmark_manifest(mutable))
            starts=[e.get(w('id')) for e in resolved.iter(w('bookmarkStart'))]
            ends=[e.get(w('id')) for e in resolved.iter(w('bookmarkEnd'))]
            self.assertCountEqual(starts,ends);self.assertEqual(len(starts),len(set(starts)))
        self.assertEqual([],validate_package(out))

    def test_guangzhou_three_zero_width_bookmarks_precede_heading(self):
        heading=para('6、示例水务业务')
        for number in reversed((23,24,25)):
            heading.insert(0,end(number));heading.insert(0,start(number))
        old,src,out,rec=self.export([heading,para('独立核查后续正文')],[para('6、示例水务业务')])
        self.verify_outcomes(old,out,rec)
        body=Reader(out).root.find(w('body'))
        self.assertEqual(['bookmarkStart','bookmarkEnd']*3,
                         [e.tag.rsplit('}',1)[-1] for e in list(body)[:6]])

    def test_cross_paragraph_end_before_empty_pair_keeps_original_order(self):
        first=para('先前的独立核查正文。');first.insert(0,start(1))
        heading=para('待更新标题')
        heading.insert(0,end(2));heading.insert(0,start(2));heading.insert(0,end(1))
        heading.insert(3,start(3));heading.append(end(3))
        old,src,out,rec=self.export([first,heading,para('保留的后续正文')],[para('来源新标题')],1)
        self.verify_outcomes(old,out,rec)
        accepted=_resolve(Reader(out).root,True)
        self.assertIn('来源新标题',text(accepted));self.assertNotIn('待更新标题',text(accepted))

    def test_trailing_sequential_empty_bookmarks_stay_after_block(self):
        heading=para('旧标题')
        for number in (10,11):heading.append(start(number));heading.append(end(number))
        old,src,out,rec=self.export([heading],[para('新标题')])
        self.verify_outcomes(old,out,rec)
        body=Reader(out).root.find(w('body'))
        self.assertEqual(['p','p','bookmarkStart','bookmarkEnd','bookmarkStart','bookmarkEnd','sectPr'],
                         [e.tag.rsplit('}',1)[-1] for e in body])

    def test_nested_and_crossing_ranges_keep_their_endpoint_sequence(self):
        p=para('旧正文')
        p.insert(0,start(31));p.insert(1,start(32))
        p.append(end(31));p.append(end(32))
        old,src,out,rec=self.export([p],[para('来源正文')])
        self.verify_outcomes(old,out,rec)


if __name__=='__main__':unittest.main()

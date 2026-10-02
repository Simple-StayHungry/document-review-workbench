import copy
import tempfile
import unittest
from pathlib import Path
from xml.etree import ElementTree as ET

from workbench.docxio import Package, w, text, resolve_revisions
from tests.test_source_insertion_writer import make_package, paragraph


class RevisionDateTests(unittest.TestCase):
    def setUp(self):
        td=tempfile.TemporaryDirectory();self.addCleanup(td.cleanup);self.base=Path(td.name)

    def test_revision_date_sets_w_date_only(self):
        target=make_package(self.base/'target.docx',[paragraph('原正文')]);target.save(self.base/'target2.docx')
        pkg=Package(self.base/'target2.docx');old=pkg.body[0];new=copy.deepcopy(old)
        next(new.iter(w('t'))).text='新正文'
        pkg.set_revision_date('2026-09-26')
        pkg.replace([old],[new],whole=False)
        dates={e.get(w('date')) for e in pkg.root.iter() if e.tag in {w('ins'),w('del'),w('pPrChange'),w('rPrChange')}}
        self.assertEqual(dates,{'2026-09-26T00:00:00Z'})
        rejected=copy.deepcopy(pkg.root);resolve_revisions(rejected,False)
        accepted=copy.deepcopy(pkg.root);resolve_revisions(accepted,True)
        self.assertIn('原正文',text(rejected));self.assertIn('新正文',text(accepted))

    def test_revision_date_does_not_change_visible_cover_date(self):
        cover='调查日期：【2026】年【7】月【17】日'
        target=make_package(self.base/'cover.docx',[paragraph(cover),paragraph('原正文')]);target.save(self.base/'cover2.docx')
        pkg=Package(self.base/'cover2.docx');pkg.set_revision_date('2026-09-26')
        old=pkg.body[1];new=copy.deepcopy(old);next(new.iter(w('t'))).text='更新正文'
        pkg.replace([old],[new],whole=False)
        rejected=copy.deepcopy(pkg.root);resolve_revisions(rejected,False)
        accepted=copy.deepcopy(pkg.root);resolve_revisions(accepted,True)
        self.assertIn(cover,text(rejected));self.assertIn(cover,text(accepted))
        self.assertNotIn('调查日期：【2026】年【9】月【26】日',text(accepted))

    def test_invalid_revision_date_rejected(self):
        target=make_package(self.base/'bad.docx',[paragraph('正文')]);target.save(self.base/'bad2.docx')
        with self.assertRaisesRegex(ValueError,'修订日期格式'):
            Package(self.base/'bad2.docx').set_revision_date('2026-02-31')

if __name__=='__main__':unittest.main()

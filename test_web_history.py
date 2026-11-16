import copy
import json
from pathlib import Path
import tempfile
import unittest
from newsletter import issue_hash, markdown, write_json
from newsletter_test_fixture import fixture
from web_history import archive_issues, retain_local, cutoff


def edition(day):
    x=fixture();x['date']=day;x['id']='daily-ai-'+day
    x['articles'][0]['publishedAt']=day
    x['approval']['contentHash']=issue_hash(x)
    return x


class HistoryTests(unittest.TestCase):
    def setup_root(self, root):
        for name in ['issues','inbox','output','assets','data']: (root/name).mkdir()

    def test_calendar_boundary_and_only_valid_approved(self):
        with tempfile.TemporaryDirectory() as folder:
            r=Path(folder);self.setup_root(r);today=edition('2026-11-16')
            for day in ['2026-10-17','2026-10-18','2026-11-15','2026-11-17']:
                write_json(r/'issues'/f'{day}.json',edition(day))
            draft=edition('2026-11-14');draft['status']='draft';write_json(r/'issues/2026-11-14.json',draft)
            changed=edition('2026-11-13');changed['articles'][0]['title']='changed';write_json(r/'issues/2026-11-13.json',changed)
            self.assertEqual(cutoff(today['date']),'2026-10-18')
            self.assertEqual([x['date']for x in archive_issues(today,r)],['2026-11-16','2026-11-15','2026-10-18'])

    def test_archive_image_and_shared_asset_survive_original_deletion(self):
        with tempfile.TemporaryDirectory() as folder:
            r=Path(folder)/'work';r.mkdir();self.setup_root(r);vault=Path(folder)/'vault';vault.mkdir()
            old=edition('2026-10-17');new=edition('2026-11-16')
            image={'src':'assets/shared.jpg','width':800,'height':400,'alt':'fixture'}
            old['articles'][0]['image']=image;new['articles'][0]['image']=image
            for x in [old,new]:x['approval']['contentHash']=issue_hash(x);write_json(r/'issues'/(x['date']+'.json'),x)
            (r/'assets/shared.jpg').write_bytes(b'original image bytes')
            (r/'inbox/2026-10-17').mkdir();(r/'inbox/2026-10-17/raw.json').write_text('{}')
            (r/'output/2026-10-17.md').write_text('old')
            plan=retain_local(new,r,vault);self.assertEqual(plan['status'],'planned');self.assertTrue((r/'issues/2026-10-17.json').exists())
            result=retain_local(new,r,vault,True);self.assertEqual(result['status'],'cleaned')
            self.assertFalse((r/'issues/2026-10-17.json').exists());self.assertFalse((r/'inbox/2026-10-17').exists())
            self.assertTrue((r/'assets/shared.jpg').exists())
            self.assertEqual((vault/'附件/2026-10-17-shared.jpg').read_bytes(),b'original image bytes')
            self.assertIn(markdown(old),(vault/'2026-10-17 AI 日报.md').read_text())
            self.assertIn('![[附件/',(vault/'2026-10-17 AI 日报.md').read_text())
            self.assertEqual(retain_local(new,r,vault,True)['expiredIssues'],0)

    def test_manual_archive_difference_prevents_all_deletions(self):
        with tempfile.TemporaryDirectory() as folder:
            r=Path(folder)/'work';r.mkdir();self.setup_root(r);vault=Path(folder)/'vault';vault.mkdir()
            old=edition('2026-10-17');write_json(r/'issues/2026-10-17.json',old)
            note=vault/'2026-10-17 AI 日报.md';note.write_text('人工修改的内容')
            with self.assertRaisesRegex(ValueError,'retention_archive_differs'):
                retain_local(edition('2026-11-16'),r,vault,True)
            self.assertTrue((r/'issues/2026-10-17.json').exists());self.assertEqual(note.read_text(),'人工修改的内容')

    def test_missing_archive_for_orphan_raw_material_stops_cleanup(self):
        with tempfile.TemporaryDirectory() as folder:
            r=Path(folder);self.setup_root(r);(r/'inbox/2026-10-01').mkdir()
            with self.assertRaisesRegex(ValueError,'retention_archive_missing'):
                retain_local(edition('2026-11-16'),r,r/'vault',True)
            self.assertTrue((r/'inbox/2026-10-01').exists())

if __name__=='__main__':unittest.main()

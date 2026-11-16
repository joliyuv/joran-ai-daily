import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch
from cloudflare_retention import retire_snapshots

class SnapshotTests(unittest.TestCase):
    def test_verified_current_is_kept_and_older_snapshots_retired(self):
        keep='aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa';old='bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb'
        rows=[{'Id':keep,'Environment':'Production','Branch':'main','Deployment':'https://aaaaaaaa.daily-test.pages.dev'},
              {'Id':old,'Environment':'Production','Branch':'main'}]
        deleted=[];expected={'issues':[{'date':'2026-11-16'}]}
        def run(args,**kw):
            if 'list' in args:return SimpleNamespace(returncode=0,stdout=json.dumps([r for r in rows if r['Id']not in deleted]))
            identity=args[args.index('delete')+1];deleted.append(identity);return SimpleNamespace(returncode=0,stdout='')
        with tempfile.TemporaryDirectory()as folder:
            root=Path(folder);(root/'app.js').write_bytes(b'current ui')
            config={'project':'daily-test','branch':'main','accountId':'fake','wrangler':['wrangler']}
            fetch=lambda u:b'current ui' if u.endswith('/app.js')else json.dumps(expected).encode()
            with patch('cloudflare_retention.subprocess.run',side_effect=run):
                result=retire_snapshots(config,expected,fetch,root)
            self.assertEqual(result['retiredDeployments'],1);self.assertEqual(deleted,[old])

    def test_unknown_current_snapshot_blocks_all_deletion(self):
        rows=[{'Id':'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa','Environment':'Production','Branch':'main','Deployment':'https://untrusted.example'}]
        calls=[]
        def run(args,**kw):calls.append(args);return SimpleNamespace(returncode=0,stdout=json.dumps(rows))
        with patch('cloudflare_retention.subprocess.run',side_effect=run):
            with self.assertRaisesRegex(ValueError,'cloudflare_snapshot_url_invalid'):
                retire_snapshots({'project':'daily-test','branch':'main','accountId':'fake','wrangler':['wrangler']},{},lambda _:b'',Path('/tmp'))
        self.assertEqual(len(calls),1)

if __name__=='__main__':unittest.main()

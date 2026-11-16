"""Retire superseded public snapshots after the current rolling site is verified."""
import json
import os
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor


def retire_snapshots(config, expected_index, fetch, root):
    project=config['project']
    if not re.fullmatch(r'[a-z0-9-]+',project):raise ValueError('invalid_pages_configuration')
    env={**os.environ,'CLOUDFLARE_ACCOUNT_ID':config['accountId'],'WRANGLER_SEND_METRICS':'false','CI':'true'}
    prefix=config['wrangler']+['pages','deployment']
    def run(args):
        r=subprocess.run(prefix+args,cwd=root,env=env,capture_output=True,text=True,timeout=90)
        if r.returncode:raise ValueError('cloudflare_snapshot_cleanup_failed')
        return r.stdout
    def listing():
        rows=json.loads(run(['list','--project-name',project,'--json']))
        if not isinstance(rows,list):raise ValueError('cloudflare_snapshot_list_invalid')
        return rows
    rows=listing()
    if not rows:raise ValueError('cloudflare_snapshot_list_empty')
    # Protect the newest successful production bundle; verify its URL, body and UI first.
    protected=rows[0]
    if protected.get('Environment')!='Production' or protected.get('Branch')!=config['branch']:
        raise ValueError('cloudflare_snapshot_current_unconfirmed')
    url=protected['Deployment']
    if not re.fullmatch(r'https://[a-f0-9]+\.'+re.escape(project)+r'\.pages\.dev',url):
        raise ValueError('cloudflare_snapshot_url_invalid')
    if json.loads(fetch(url+'/data/history.json'))!=expected_index or fetch(url+'/app.js')!=(root/'app.js').read_bytes():
        raise ValueError('cloudflare_snapshot_current_unconfirmed')
    keep=protected['Id'];deleted=0
    def remove(row):
        identity=row['Id']
        if identity==keep or not re.fullmatch(r'[a-f0-9-]{36}',identity):
            raise ValueError('cloudflare_snapshot_id_invalid')
        run(['delete',identity,'--project-name',project,'--force'])
    # CLI returns the first page; deletion exposes the next page on the next pass.
    for _ in range(10):
        old=[r for r in rows if r['Id']!=keep and r.get('Environment')=='Production' and r.get('Branch')==config['branch']]
        if not old:return {'status':'cleaned','retiredDeployments':deleted}
        with ThreadPoolExecutor(max_workers=3) as pool:list(pool.map(remove,old))
        deleted+=len(old);rows=listing()
    raise ValueError('cloudflare_snapshot_cleanup_incomplete')

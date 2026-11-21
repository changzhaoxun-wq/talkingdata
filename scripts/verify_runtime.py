#!/usr/bin/env python3
"""Verify SSE replay and optional Docker restart durability without model calls."""
import argparse
import hashlib
import json
import subprocess
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

ROOT = Path(__file__).resolve().parent.parent


def main(args):
    ids = json.loads((ROOT / 'docs/evidence/sessions.json').read_text())
    with httpx.Client(base_url=args.base_url, trust_env=False, timeout=15) as client:
        def capture():
            result = {}
            for sid in ids.values():
                snapshot = client.get('/api/sessions/' + sid).json()
                assert all(r['status'] not in ('queued', 'running') for r in snapshot['runs'])
                files = client.get('/api/sessions/' + sid + '/files').json()['files']
                hashes = {}
                for file in files:
                    response = client.get('/api/sessions/' + sid + '/files/download', params={'path': file['path']})
                    response.raise_for_status()
                    hashes[file['path']] = hashlib.sha256(response.content).hexdigest()
                result[sid] = {'cli_session_id': snapshot['cli_session_id'],
                               'messages': [m['id'] for m in snapshot['messages']],
                               'runs': [r['id'] for r in snapshot['runs']], 'sha256': hashes}
            return result

        before = capture()
        sid = ids['work_session']
        events = client.get('/api/sessions/' + sid).json()['events']
        cursor, expected = events[-3]['id'], events[-2]['id']
        replay = []
        for params, headers in [({'after': cursor}, {}), ({'after': 0}, {'Last-Event-ID': str(cursor)})]:
            with client.stream('GET', '/api/sessions/' + sid + '/events', params=params, headers=headers) as response:
                response.raise_for_status()
                for line in response.iter_lines():
                    if line.startswith('id: '):
                        replay.append(int(line[4:])); break
        assert replay == [expected, expected]
        result = {'verified_at': datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(),
                  'sse_query_cursor_replay': True, 'sse_last_event_id_replay': True}
        if args.restart:
            subprocess.run(['docker', 'restart', '--time', '10', args.container], check=True, capture_output=True)
            for _ in range(60):
                try:
                    if client.get('/api/health').status_code == 200:
                        break
                except httpx.RequestError:
                    pass
                time.sleep(.5)
            else:
                raise RuntimeError('Application did not return after restart')
            after = capture()
            assert after == before, 'Persisted messages, sessions, runs or file bytes changed'
            result.update(docker_restart_preserves_sessions_messages_and_file_hashes=True, sessions=after)
        for _ in range(40):
            health = subprocess.check_output(['docker', 'inspect', '--format', '{{.State.Health.Status}}', args.container], text=True).strip()
            if health == 'healthy': break
            time.sleep(.5)
        assert health == 'healthy', health
        query = '{"image":{{json .Image}},"user":{{json .Config.User}},"memory":{{.HostConfig.Memory}},"cpus":{{.HostConfig.NanoCpus}},"pids":{{.HostConfig.PidsLimit}},"cap_drop":{{json .HostConfig.CapDrop}},"security_opt":{{json .HostConfig.SecurityOpt}}}'
        result['container'] = json.loads(subprocess.check_output(['docker', 'inspect', '--format', query, args.container], text=True))
        result['health'] = health
        result['cli_version'] = subprocess.check_output(['docker', 'exec', args.container, 'opencode', '--version'], text=True).strip()
        (ROOT / 'docs/evidence/runtime-checks.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', default='http://127.0.0.1:8787')
    parser.add_argument('--container', default='talkingdata-workbench')
    parser.add_argument('--restart', action='store_true')
    main(parser.parse_args())

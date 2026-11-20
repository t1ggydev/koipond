#!/usr/bin/env python3
"""Render the reviewed private deployment from pinned official configuration."""
from copy import deepcopy
from pathlib import Path
import re
import yaml

ROOT = Path(__file__).resolve().parent


class Loader(yaml.SafeLoader):
    pass


def override(loader, node):
    if isinstance(node, yaml.SequenceNode):
        return loader.construct_sequence(node, deep=True)
    if isinstance(node, yaml.MappingNode):
        return loader.construct_mapping(node, deep=True)
    return loader.construct_scalar(node)


Loader.add_constructor('!override', override)
base = yaml.safe_load((ROOT / 'upstream/docker-compose.yml').read_text())
kong = yaml.load((ROOT / 'upstream/docker-compose.kong.yml').read_text(), Loader=Loader)
base['name'] = 'koipond-supabase'
base['services']['api-gw'].update(deepcopy(kong['services']['api-gw']))
limits = {'db': '2g', 'studio': '768m', 'api-gw': '1g', 'auth': '384m',
          'rest': '384m', 'realtime': '1g', 'storage': '512m', 'imgproxy': '256m',
          'meta': '384m', 'functions': '512m', 'supavisor': '768m'}
for name, service in base['services'].items():
    if name != 'realtime':
        service['container_name'] = 'koipond-supabase-' + name
    service['mem_limit'] = limits[name]
    service['logging'] = {'driver': 'local', 'options': {'max-size': '10m', 'max-file': '3'}}
    mounted = []
    for volume in service.get('volumes', []):
        source, *rest = volume.split(':')
        if source == './volumes/db/data':
            source = './data/postgres'
        elif source == './volumes/storage':
            source = './data/storage'
        elif source == './volumes/snippets':
            source = './data/snippets'
        elif source.startswith('./volumes/'):
            source = './upstream/' + source[2:]
        mounted.append(':'.join([source] + rest))
    if mounted:
        service['volumes'] = mounted
base['services']['studio']['ports'] = ['127.0.0.1:3001:3000']
gateway = base['services']['api-gw']
gateway['ports'] = ['10.10.10.30:8000:8000']
gateway['environment']['KONG_ADMIN_LISTEN'] = 'off'
gateway['depends_on'] = {name: {'condition': 'service_healthy'} for name in ['auth', 'rest', 'realtime', 'storage']}
gateway['volumes'][0] = './kong.private.yml:/home/kong/temp.yml:ro'
base['services']['supavisor']['ports'] = ['127.0.0.1:5432:5432', '127.0.0.1:6543:6543']
base['services']['db']['shm_size'] = '256m'
base['services']['db']['command'] += ['-c', 'shared_buffers=256MB', '-c', 'max_connections=120']
base['services']['meta']['healthcheck'] = {
    'test': ['CMD', 'node', '-e', "fetch('http://127.0.0.1:8080/health').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"],
    'interval': '10s', 'timeout': '5s', 'retries': 5,
}
(ROOT / 'compose.yml').write_text(yaml.safe_dump(base, sort_keys=False))
# Preserve upstream quoting exactly. The entrypoint injects Lua expressions into
# quoted template values; re-serializing placeholders can make that YAML invalid.
lines = []
skip = False
for line in (ROOT / 'upstream/volumes/api/kong.yml').read_text().splitlines():
    match = re.match(r'^  - name: (\S+)\s*$', line)
    if match:
        skip = match.group(1) in ['dashboard', 'meta', 'mcp', 'mcp-blocker']
    if not skip:
        lines.append(line)
(ROOT / 'kong.private.yml').write_text('\n'.join(lines) + '\n')
print('Private Compose and gateway configuration rendered')

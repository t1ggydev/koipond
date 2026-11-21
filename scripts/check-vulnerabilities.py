#!/usr/bin/env python3
"""Inventory known CVEs in an immutable build and its source; never deploy or exploit."""
import argparse
import json
from pathlib import Path
import re
import subprocess

p = argparse.ArgumentParser()
p.add_argument('--image', required=True)
p.add_argument('--revision', required=True)
p.add_argument('--source', type=Path, required=True)
p.add_argument('--output-dir', type=Path, required=True)
args = p.parse_args()
assert re.fullmatch('[a-f0-9]{40}', args.revision)
assert re.fullmatch(r'koipond-prep:[a-f0-9]{40}-[A-Za-z0-9_.-]+', args.image)
revision = subprocess.check_output(['docker', 'image', 'inspect', '--format',
    '{{index .Config.Labels "org.opencontainers.image.revision"}}', args.image], text=True).strip()
assert revision == args.revision, 'Only the exact tested build may be scanned'
args.output_dir.mkdir(parents=True, exist_ok=True)
common = ['--scanners', 'vuln', '--skip-version-check', '--disable-telemetry',
          '--parallel', '1', '--timeout', '10m', '--cache-dir', '/var/cache/security-trivy',
          '--list-all-pkgs', '--format', 'json', '--exit-code', '0']
for kind, target in [('image', args.image), ('source', str(args.source.resolve()))]:
    output = args.output_dir / f'security-{kind}-{args.revision}.json'
    command = ['trivy', 'image' if kind == 'image' else 'fs'] + common
    command += ['--image-src', 'docker'] if kind == 'image' else [
        '--include-dev-deps', '--skip-dirs', 'deploy/supabase/upstream']
    subprocess.run(command + ['--output', str(output), target], check=True, timeout=660,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    report = json.loads(output.read_text())
    metadata = report.get('Metadata', {})
    report['Metadata'] = {k: metadata[k] for k in ['OS', 'ImageID', 'RepoDigests'] if k in metadata}
    output.write_text(json.dumps(report))
    output.chmod(0o600)
    findings = sum(len(r.get('Vulnerabilities', [])) for r in report.get('Results', []))
    print(json.dumps({'kind': kind, 'revision': args.revision, 'findings': findings,
                      'report': output.name, 'blocks_deployment': False}))

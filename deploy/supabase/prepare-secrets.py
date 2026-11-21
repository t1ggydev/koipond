#!/usr/bin/env python3
"""Controller-only provisioning. Secret material is never written to stdout."""
import base64
import hashlib
import hmac
import importlib.util
import json
import os
from pathlib import Path
import re
import secrets
import time

os.umask(0o077)
ROOT = Path('/home/leon/homelab-ansible/root-infra')
spec = importlib.util.spec_from_file_location('vault_helper', ROOT / 'files/sophos-api.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
password = module.vault_password()
directory = ROOT / 'recovery/koipond'
directory.mkdir(mode=0o700, parents=True, exist_ok=True)
target = directory / 'supabase-self-hosted-secrets.vault'


def b64(data):
    return base64.urlsafe_b64encode(data).decode().rstrip('=')


def jwt(role, secret):
    now = int(time.time())
    head = b64(json.dumps({'alg': 'HS256', 'typ': 'JWT'}, separators=(',', ':')).encode())
    body = b64(json.dumps({'role': role, 'iss': 'supabase', 'iat': now - 60, 'exp': now + 2 * 365 * 86400}, separators=(',', ':')).encode())
    message = head + '.' + body
    return message + '.' + b64(hmac.new(secret.encode(), message.encode(), hashlib.sha256).digest())


if target.exists():
    record = json.loads(module.vault(['view', str(target)], password=password))
else:
    secret = secrets.token_urlsafe(48)
    env = {
        'POSTGRES_PASSWORD': secrets.token_urlsafe(36),
        'JWT_SECRET': secret,
        'ANON_KEY': jwt('anon', secret),
        'SERVICE_ROLE_KEY': jwt('service_role', secret),
        'SECRET_KEY_BASE': secrets.token_hex(48),
        'REALTIME_DB_ENC_KEY': secrets.token_hex(8),
        'VAULT_ENC_KEY': secrets.token_hex(16),
        'PG_META_CRYPTO_KEY': secrets.token_hex(32),
        'DASHBOARD_USERNAME': 'leon',
        'DASHBOARD_PASSWORD': secrets.token_urlsafe(30),
        'S3_PROTOCOL_ACCESS_KEY_ID': secrets.token_hex(16),
        'S3_PROTOCOL_ACCESS_KEY_SECRET': secrets.token_hex(32),
        'LOGFLARE_PUBLIC_ACCESS_TOKEN': secrets.token_hex(32),
        'LOGFLARE_PRIVATE_ACCESS_TOKEN': secrets.token_hex(32),
    }
    record = {'format': 'koipond-supabase-secrets-v1', 'created_at': int(time.time()), 'environment': env}
    module.vault(['encrypt', '--output', str(target), '-'], json.dumps(record).encode(), password=password)
env = {}
for line in (Path(__file__).parent / 'upstream/.env.example').read_text().splitlines():
    if re.match(r'^[A-Z][A-Z0-9_]*=', line):
        key, value = line.split('=', 1)
        env[key] = value
env.update(record['environment'])
env.update({
    'COMPOSE_FILE': 'compose.yml:images.lock.yml',
    'POSTGRES_HOST': 'db', 'POSTGRES_DB': 'postgres', 'POSTGRES_PORT': '5432',
    'SITE_URL': 'https://www.koipond.live',
    'SUPABASE_PUBLIC_URL': 'https://koipond-api.kluge-it.tech',
    'API_EXTERNAL_URL': 'https://koipond-api.kluge-it.tech/auth/v1',
    'ADDITIONAL_REDIRECT_URLS': '', 'DISABLE_SIGNUP': 'true',
    'ENABLE_EMAIL_SIGNUP': 'false', 'ENABLE_PHONE_SIGNUP': 'false',
    'ENABLE_ANONYMOUS_USERS': 'false', 'ENABLE_EMAIL_AUTOCONFIRM': 'false',
    'ENABLE_PHONE_AUTOCONFIRM': 'false', 'SMTP_HOST': '', 'SMTP_USER': '', 'SMTP_PASS': '',
    'SMTP_ADMIN_EMAIL': 'administrator@kluge-it.tech', 'SMTP_SENDER_NAME': 'Koipond',
    'OPENAI_API_KEY': '', 'JWT_EXPIRY': '3600',
    'STUDIO_DEFAULT_ORGANIZATION': 'Kluge IT', 'STUDIO_DEFAULT_PROJECT': 'Koipond',
    'SUPABASE_PUBLISHABLE_KEY': '', 'SUPABASE_SECRET_KEY': '', 'JWT_KEYS': '', 'JWT_JWKS': '',
    'ANON_KEY_ASYMMETRIC': '', 'SERVICE_ROLE_KEY_ASYMMETRIC': '',
    'POOLER_TENANT_ID': 'koipond', 'POOLER_DEFAULT_POOL_SIZE': '10',
    'POOLER_MAX_CLIENT_CONN': '100', 'POOLER_DB_POOL_SIZE': '3',
    'FUNCTIONS_VERIFY_JWT': 'true', 'FUNCTIONS_MEMORY_LIMIT': '512m',
    'PGRST_DB_SCHEMAS': 'public,graphql_public', 'PGRST_DB_MAX_ROWS': '1000',
})
runtime = ROOT / 'work/koipond-supabase-runtime'
runtime.mkdir(mode=0o700, exist_ok=True)
runtime.chmod(0o700)
env_file = runtime / 'runtime.env'
env_file.write_text('\n'.join(key + '=' + value for key, value in env.items()) + '\n')
env_file.chmod(0o600)
print(json.dumps({'vault': str(target), 'runtime_environment': str(env_file)}))

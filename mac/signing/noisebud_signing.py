"""Noisebud Mac signing 1.0.1. Public certificate selectors; never reads secrets.

Canonical source: C:/Projects/Noisebud Mac Signing. Consumers carry identical
copies so exported projects work independently. A Mac-wide JSON file overrides
the bundled selectors for annual renewal; no certificate is ever deleted.
"""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import plistlib
import re
import shlex
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET

VERSION = '1.0.1'
MAGICS = {bytes.fromhex(x) for x in ('cafebabe', 'cafebabf', 'bebafeca',
          'bfbafeca', 'cffaedfe', 'cefaedfe', 'feedfacf', 'feedface')}


def run(*args, **kwargs):
    return subprocess.run([str(a) for a in args], check=True, capture_output=True, **kwargs)


def settings():
    explicit = os.environ.get('NOISEBUD_SIGNING_CONFIG')
    shared = Path.home() / 'Desktop/Certificat/noisebud-signing.json'
    path = Path(explicit).expanduser() if explicit else (
        shared if shared.is_file() else Path(__file__).with_name('noisebud-signing.json'))
    data = json.loads(path.read_text(encoding='utf-8-sig'))
    if data.get('schemaVersion') != 1:
        raise ValueError('Unsupported Noisebud signing configuration: ' + str(path))
    config = {'team': os.environ.get('NOISEBUD_APPLE_TEAM_ID') or data['teamId'],
              'application': os.environ.get('NOISEBUD_APPLE_SIGN_IDENTITY') or data['applicationSha1'],
              'installer': os.environ.get('NOISEBUD_APPLE_INSTALLER_IDENTITY') or data['installerSha1']}
    if not re.fullmatch(r'[A-Z0-9]{10}', config['team']):
        raise ValueError('Missing/invalid Developer ID team')
    for key in ('application', 'installer'):
        if not re.fullmatch(r'[A-Fa-f0-9]{40}', config[key]):
            raise ValueError(key + ' identity must be an exact SHA-1 fingerprint, never a shared certificate name')
        config[key] = config[key].upper()
    return config


def pem_der(pem):
    return base64.b64decode(re.sub(rb'-----[^\n]+-----|\s', b'', pem), validate=True)


def certificate(der, expected, team, kind):
    if hashlib.sha1(der).hexdigest().upper() != expected:
        raise ValueError('Wrong ' + kind + ' signing leaf certificate')
    output = run('openssl', 'x509', '-inform', 'DER', '-noout', '-subject', '-issuer',
                 '-nameopt', 'RFC2253', input=der).stdout.decode('utf-8')
    subject = next((line for line in output.splitlines() if line.startswith('subject=')), '')
    issuer = next((line for line in output.splitlines() if line.startswith('issuer=')), '')
    expected_type = 'Developer ID Application:' if kind == 'application' else 'Developer ID Installer:'
    if expected_type not in subject or not re.search(r'(?:=|,)OU=' + re.escape(team) + r'(?:,|$)', subject):
        raise ValueError('Wrong Developer ID type or signing team')
    if not re.search(r'(?:=|,)OU=G2(?:,|$)', issuer) or 'CN=Developer ID Certification Authority' not in issuer:
        raise ValueError('Signing certificate was not issued by Developer ID G2')
    run('openssl', 'x509', '-inform', 'DER', '-noout', '-checkend', '0', input=der)
    # -checkend checks expiry; verify the start date too, in UTC.
    from datetime import datetime, timezone
    dates = run('openssl', 'x509', '-inform', 'DER', '-noout', '-startdate', input=der).stdout.decode().strip()
    start = datetime.strptime(dates.split('=', 1)[1], '%b %d %H:%M:%S %Y %Z').replace(tzinfo=timezone.utc)
    if start > datetime.now(timezone.utc):
        raise ValueError('Signing certificate is not valid yet')


def validate_identity(config, kind):
    expected = config[kind]
    args = ['security', 'find-identity', '-v']
    if kind == 'application':
        args += ['-p', 'codesigning']
    identities = run(*args).stdout.decode('utf-8')
    if not re.search(r'\b' + expected + r'\s+"Developer ID ' + ('Application' if kind == 'application' else 'Installer') + r':', identities, re.I):
        raise ValueError('Valid ' + kind + ' identity/private key not available: ' + expected)
    certificates = run('security', 'find-certificate', '-a', '-p').stdout
    for pem in re.findall(rb'-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----', certificates, re.S):
        der = pem_der(pem)
        if hashlib.sha1(der).hexdigest().upper() == expected:
            certificate(der, expected, config['team'], kind)
            return
    raise ValueError('Selected ' + kind + ' certificate is missing from the keychain')


def verify_code(target, config, tree=False):
    target = Path(target)
    # Native cryptographic verification is independent of the leaf hash check.
    run('codesign', '--verify', '--all-architectures', '--strict', '--verbose=2', *(['--deep'] if target.is_dir() else []), target)
    candidates = [target]
    if tree and target.is_dir():
        for path in target.rglob('*'):
            if path.is_symlink() or not path.is_file():
                continue
            with path.open('rb') as stream:
                if stream.read(4) in MAGICS:
                    candidates.append(path)
    for path in candidates:
        executable = path
        if path.is_dir() and (path / 'Contents/Info.plist').is_file():
            with (path / 'Contents/Info.plist').open('rb') as stream:
                executable = path / 'Contents/MacOS' / plistlib.load(stream)['CFBundleExecutable']
        architectures = []
        if executable.is_file():
            with executable.open('rb') as stream:
                if stream.read(4) in MAGICS:
                    architectures = run('lipo', '-archs', executable).stdout.decode().split()
                    if not architectures:
                        raise ValueError('Missing Mach-O architectures: ' + str(executable))
        # A universal can contain different signatures per slice. Inspect every
        # leaf/timestamp; a correct preferred slice does not prove the other one.
        for architecture in architectures or [None]:
            arch_args = ['--architecture', architecture] if architecture else []
            with tempfile.TemporaryDirectory(prefix='noisebud-signature-') as directory:
                prefix = str(Path(directory) / 'cert')
                # This option has an optional argument: attach it with '=' so
                # codesign does not interpret the prefix as another code path.
                run('codesign', '-d', *arch_args, '--extract-certificates=' + prefix, path)
                leaf = Path(prefix + '0')
                if not leaf.is_file():
                    raise ValueError('Missing signing leaf certificate: ' + str(path))
                certificate(leaf.read_bytes(), config['application'], config['team'], 'application')
            details = run('codesign', '-d', *arch_args, '--verbose=4', path).stderr.decode('utf-8')
            if not re.search(r'^Timestamp=', details, re.M):
                raise ValueError('Missing secure signing timestamp: ' + str(path))
        # Repeat strict verification for every executable/library, not only bundles.
        if path != target:
            run('codesign', '--verify', '--all-architectures', '--strict', path)


def verify_pkg(target, config):
    run('pkgutil', '--check-signature', target)
    with tempfile.TemporaryDirectory(prefix='noisebud-pkg-signature-') as directory:
        toc = Path(directory) / 'toc.xml'
        run('xar', '-f', target, '--dump-toc=' + str(toc))
        root = ET.parse(toc).getroot()
        signature = next((node for node in root.iter() if node.tag.rsplit('}', 1)[-1] == 'signature'), None)
        if signature is None:
            raise ValueError('Missing PKG signature')
        leaf = next((node for node in signature.iter() if node.tag.rsplit('}', 1)[-1] == 'X509Certificate'), None)
        if leaf is None or not leaf.text:
            raise ValueError('Missing PKG signing leaf certificate')
        certificate(base64.b64decode(re.sub(r'\s', '', leaf.text), validate=True),
                    config['installer'], config['team'], 'installer')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['environment', 'json', 'verify-code', 'verify-pkg'])
    parser.add_argument('target', nargs='?')
    parser.add_argument('--validate', action='store_true')
    parser.add_argument('--installer', action='store_true')
    parser.add_argument('--tree', action='store_true')
    args = parser.parse_args()
    config = settings()
    if args.validate:
        validate_identity(config, 'application')
        if args.installer:
            validate_identity(config, 'installer')
    if args.action == 'json':
        print(json.dumps(config))
    elif args.action == 'environment':
        for name, key in [('NOISEBUD_APPLE_TEAM_ID', 'team'), ('NOISEBUD_APPLE_SIGN_IDENTITY', 'application'),
                          ('NOISEBUD_APPLE_INSTALLER_IDENTITY', 'installer'), ('CSC_NAME', 'application')]:
            print('export ' + name + '=' + shlex.quote(config[key]))
    else:
        if not args.target:
            parser.error('target is required')
        if args.action == 'verify-code':
            verify_code(args.target, config, args.tree)
        else:
            verify_pkg(args.target, config)
        print('G2 signing certificate verified: ' + args.target)


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, KeyError, subprocess.CalledProcessError) as error:
        details = getattr(error, 'stderr', None)
        if isinstance(details, bytes):
            details = details.decode('utf-8', errors='replace')
        suffix = ('\n' + details.strip()) if details and details.strip() else ''
        raise SystemExit('Noisebud signing failed: ' + str(error) + suffix)

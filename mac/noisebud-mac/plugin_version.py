"""Set Noisebud's own release version inside SynthEdit-exported AU/VST3 bundles.

SynthEdit 1.5 always exports version 1.0: the shared wrapper binary reads
``<Plugin Version="1.0.0.0">`` from ``Contents/Resources/factory.se.xml``
(VST3 class version shown by hosts), the AU ``Info.plist`` registers
``AudioComponents[].version = 65536`` (1.0.0) and the CFBundle version strings
are empty or 1.0. ``patch_bundle`` rewrites exactly those fields, so it must run
on the staged copy *before* any code signing (Mac) or installer build (Windows).

Canonical copy: noisebud-build-toolkit/plugin_version.py. Consumers keep a
byte-identical copy (see sync_consumers.py). Standard library only.

CLI (used by the Windows PowerShell builder):
  python plugin_version.py derive --bundle-name NBB_ToC_v112 --release 1.00 --major 1
  python plugin_version.py check --version 1.12
  python plugin_version.py patch PATH/TO/Plugin.vst3 --version 1.12
  python plugin_version.py verify PATH/TO/Plugin.vst3 --version 1.12
"""
import argparse
import json
import plistlib
import re
import sys
from pathlib import Path

__version__ = '1.0.0'

_VERSION = re.compile(r'[0-9]+(?:\.[0-9]+){0,3}')
_SUFFIX = re.compile(r'[ _-]v(?P<tag>[0-9]+(?:_[0-9]+)*)$', re.IGNORECASE)
_FACTORY_VERSION = re.compile(rb'(<Plugin\s(?:[^>]*?\s)?Version=")([^"]*)(")')
_MODULEINFO_VERSION = re.compile(rb'("Version"\s*:\s*")([^"]*)(")')


def parse(version):
    """Validate a numeric version and return its integer components."""
    if not isinstance(version, str) or not _VERSION.fullmatch(version):
        raise ValueError('Pluginversionen måste vara numerisk, t.ex. 1.12 eller 2.56: ' + repr(version))
    parts = tuple(int(part) for part in version.split('.'))
    if any(part > 65535 for part in parts):
        raise ValueError('Varje versionsdel måste vara högst 65535: ' + version)
    return parts


def au_version(version):
    """Apple AudioComponents version: major << 16 | minor << 8 | bugfix."""
    parts = parse(version) + (0, 0, 0)
    major, minor, bugfix, build = parts[:4]
    if minor > 255 or bugfix > 255 or build:
        raise ValueError('AU-versionen kräver formen MAJOR.MINOR[.BUGFIX] med MINOR/BUGFIX <= 255: ' + version)
    return (major << 16) | (minor << 8) | bugfix


def factory_version(version):
    """SynthEdit's factory.se.xml uses four components (1.0.0.0)."""
    return '.'.join(str(part) for part in (parse(version) + (0, 0, 0))[:4])


def derive(bundle_name, release, major):
    """Propose the plugin version from the bundle suffix and the product manifest.

    The manifest is shared by a whole major version (its releaseVersion may stay
    1.00), while the exported bundle name carries the real build, e.g.
    NBB_ToC_v112 -> 1.12, Pinky2_v256 -> 2.56, Width_v0_0_8 -> 0.0.8. A suffix
    that does not start with the manifest's major version is ignored.
    Returns (version, source, note).
    """
    parse(release)
    stem = Path(str(bundle_name)).name
    for extension in ('.vst3', '.component'):
        if stem.lower().endswith(extension):
            stem = stem[:-len(extension)]
    match = _SUFFIX.search(stem)
    if not match:
        return release, 'manifest', 'Bundlenamnet saknar _vNNN; manifestets releaseVersion används.'
    tag = match.group('tag')
    if '_' in tag:
        candidate = '.'.join(str(int(part)) for part in tag.split('_'))
    elif tag == release.replace('.', ''):
        candidate = release
    else:
        prefix = str(int(major))
        candidate = prefix + '.' + tag[len(prefix):] if tag.startswith(prefix) and len(tag) > len(prefix) else ''
    try:
        if candidate and parse(candidate)[0] == int(major):
            return candidate, 'bundle', ''
    except ValueError:
        pass
    return release, 'manifest', ('Bundlesuffixet v' + tag + ' matchar inte major ' + str(major)
                                 + '; manifestets releaseVersion används.')


def _plist_format(data):
    return plistlib.FMT_BINARY if data.startswith(b'bplist') else plistlib.FMT_XML


def patch_bundle(bundle, version):
    """Rewrite every SynthEdit version field in one AU/VST3 bundle. Returns changes."""
    bundle = Path(bundle)
    parse(version)
    changes = []
    factory = bundle / 'Contents/Resources/factory.se.xml'
    if not factory.is_file():
        raise ValueError('SynthEdit factory.se.xml saknas: ' + str(factory))
    data = factory.read_bytes()
    updated, count = _FACTORY_VERSION.subn(
        lambda m: m.group(1) + factory_version(version).encode('ascii') + m.group(3), data)
    if not count:
        raise ValueError('Ingen <Plugin Version="..."> hittades i ' + str(factory))
    if updated != data:
        factory.write_bytes(updated)
    changes.append('factory.se.xml Version=' + factory_version(version))

    info_path = bundle / 'Contents/Info.plist'
    if info_path.is_file():
        raw = info_path.read_bytes()
        info = plistlib.loads(raw)
        info['CFBundleShortVersionString'] = version
        info['CFBundleVersion'] = version
        components = info.get('AudioComponents')
        if components is not None:
            if not isinstance(components, list) or not all(isinstance(c, dict) for c in components):
                raise ValueError('Ogiltig AudioComponents-lista i ' + str(info_path))
            for component in components:
                component['version'] = au_version(version)
            changes.append('AudioComponents version=' + str(au_version(version)))
        info_path.write_bytes(plistlib.dumps(info, fmt=_plist_format(raw)))
        changes.append('Info.plist CFBundle(Short)Version=' + version)

    # VST3 SDK 3.7 bundles may carry moduleinfo.json; SynthEdit 1.5 does not.
    moduleinfo = bundle / 'Contents/Resources/moduleinfo.json'
    if moduleinfo.is_file():
        data = moduleinfo.read_bytes()
        updated, count = _MODULEINFO_VERSION.subn(
            lambda m: m.group(1) + version.encode('ascii') + m.group(3), data)
        if count:
            moduleinfo.write_bytes(updated)
            changes.append('moduleinfo.json Version=' + version)
    return changes


def verify_bundle(bundle, version):
    """Raise if any SynthEdit version field in the bundle differs from version."""
    bundle = Path(bundle)
    found = _FACTORY_VERSION.findall((bundle / 'Contents/Resources/factory.se.xml').read_bytes())
    if not found or any(value.decode('ascii', 'replace') != factory_version(version) for _, value, _ in found):
        raise ValueError('factory.se.xml har fel pluginversion i ' + str(bundle))
    info_path = bundle / 'Contents/Info.plist'
    if info_path.is_file():
        info = plistlib.loads(info_path.read_bytes())
        if info.get('CFBundleShortVersionString') != version or info.get('CFBundleVersion') != version:
            raise ValueError('Info.plist har fel pluginversion i ' + str(bundle))
        for component in info.get('AudioComponents') or []:
            if component.get('version') != au_version(version):
                raise ValueError('AU AudioComponents har fel version i ' + str(bundle))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    commands = parser.add_subparsers(dest='command', required=True)
    derive_parser = commands.add_parser('derive')
    derive_parser.add_argument('--bundle-name', required=True)
    derive_parser.add_argument('--release', required=True)
    derive_parser.add_argument('--major', required=True, type=int)
    commands.add_parser('check').add_argument('--version', required=True)
    for name in ('patch', 'verify'):
        sub = commands.add_parser(name)
        sub.add_argument('bundle')
        sub.add_argument('--version', required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == 'derive':
            version, source, note = derive(args.bundle_name, args.release, args.major)
            au_version(version)
            print(json.dumps({'pluginVersion': version, 'source': source, 'note': note}))
        elif args.command == 'check':
            au_version(args.version)
            print('Pluginversion OK: ' + args.version)
        elif args.command == 'patch':
            au_version(args.version)
            for change in patch_bundle(args.bundle, args.version):
                print('Pluginversion: ' + change)
            verify_bundle(args.bundle, args.version)
        else:
            verify_bundle(args.bundle, args.version)
            print('Pluginversion OK: ' + args.version)
    except (ValueError, OSError) as error:
        print('Pluginversion misslyckades: ' + str(error), file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main())

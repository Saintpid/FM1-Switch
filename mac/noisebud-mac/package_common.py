"""Shared, platform-independent validation for Noisebud product packages."""
import hashlib
import json
import plistlib
import re
import shutil
import struct
from pathlib import Path, PurePosixPath


MAXIMUM_MACOS_DEPLOYMENT_TARGET = (11, 0, 0)


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def write_json(path, value):
    Path(path).write_text(json.dumps(value, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')


def safe_leaf(value):
    return (isinstance(value, str) and 0 < len(value) <= 200
            and value not in ('.', '..') and not value.endswith(('.', ' '))
            and not re.search(r'[<>:"/\\|?*\x00-\x1f]', value))


def validate_metadata(metadata, manifest):
    if metadata.get('schemaVersion') != 1 or metadata.get('purpose') != 'noisebud-installer-metadata':
        raise ValueError('Unsupported installer metadata')
    if metadata.get('productManifest') != manifest or metadata.get('registrationRequired') is not True:
        raise ValueError('Installer metadata and original manifest disagree')
    if type(manifest.get('schemaVersion')) is not int or manifest['schemaVersion'] != 1:
        raise ValueError('Unsupported product manifest')
    reg = manifest['registration']
    for key in ('appId', 'productId'):
        if not isinstance(reg.get(key), str) or not re.fullmatch(r'[a-z0-9_-]{1,100}', reg[key]):
            raise ValueError('Invalid registration ' + key)
    if type(reg.get('majorVersion')) is not int or not 1 <= reg['majorVersion'] <= 1000000:
        raise ValueError('A protected product requires majorVersion >= 1')
    if not isinstance(reg.get('productName'), str) or not reg['productName'].strip():
        raise ValueError('Missing registration name')
    release = manifest['releaseVersion']
    if not isinstance(release, str) or not re.fullmatch(r'\d+(?:\.\d+){0,3}', release):
        raise ValueError('Invalid releaseVersion')
    names = metadata['installer']
    if names['releaseVersion'] != release or not safe_leaf(names['fileStem']):
        raise ValueError('Invalid installer name/version')
    if 'pluginVersion' in names:
        # Older exports lack it and keep using releaseVersion (plugin_version.py).
        import plugin_version
        plugin_version.au_version(names['pluginVersion'])
    for key, prefix, suffix in (('windowsFileName', 'Win_-_', '.exe'), ('macFileName', 'macOS_-_', '.dmg')):
        if names[key] != prefix + names['fileStem'] + suffix or not safe_leaf(names[key]):
            raise ValueError('Inconsistent installer filenames')
    bundle = metadata['pluginBundleName']
    if not safe_leaf(bundle) or not bundle.endswith('.vst3'):
        raise ValueError('Invalid VST3 bundle name')
    return reg, names


def _macho_slices(path):
    """Return (cpu, offset, size) without relying on macOS-only tools."""
    path = Path(path)
    length = path.stat().st_size
    with path.open('rb') as stream:
        head = stream.read(4096)
    if len(head) < 8:
        return []
    magic = head[:4]
    if magic in (b'\xca\xfe\xba\xbe', b'\xca\xfe\xba\xbf', b'\xbe\xba\xfe\xca', b'\xbf\xba\xfe\xca'):
        endian = '>' if magic[0] == 0xca else '<'
        count = struct.unpack_from(endian + 'I', head, 4)[0]
        is_64 = magic in (b'\xca\xfe\xba\xbf', b'\xbf\xba\xfe\xca')
        stride = 32 if is_64 else 20
        if not 1 <= count <= 16 or len(head) < 8 + count * stride:
            raise ValueError('Invalid universal Mach-O header: ' + str(path))
        result = []
        for index in range(count):
            position = 8 + index * stride
            if is_64:
                cpu, _, offset, size, _, _ = struct.unpack_from(endian + 'IIQQII', head, position)
            else:
                cpu, _, offset, size, _ = struct.unpack_from(endian + 'IIIII', head, position)
            if size < 28 or offset + size > length:
                raise ValueError('Invalid universal Mach-O slice: ' + str(path))
            result.append((cpu, offset, size))
        return result
    if magic in (b'\xcf\xfa\xed\xfe', b'\xce\xfa\xed\xfe', b'\xfe\xed\xfa\xcf', b'\xfe\xed\xfa\xce'):
        endian = '<' if magic[0] in (0xcf, 0xce) else '>'
        return [(struct.unpack_from(endian + 'I', head, 4)[0], 0, length)]
    return []


def macho_arches(path):
    """Read Mach-O architecture headers without executing macOS tools on Windows."""
    return {{0x1000007: 'x86_64', 0x100000c: 'arm64'}.get(cpu, hex(cpu))
            for cpu, _, _ in _macho_slices(path)}


def _decode_macos_version(value):
    return value >> 16, (value >> 8) & 0xff, value & 0xff


def macho_deployment_targets(path):
    """Read LC_BUILD_VERSION/LC_VERSION_MIN_MACOSX from every Mach-O slice."""
    path = Path(path)
    targets = {}
    with path.open('rb') as stream:
        for cpu, offset, size in _macho_slices(path):
            stream.seek(offset)
            header = stream.read(32)
            magic = header[:4]
            if magic in (b'\xcf\xfa\xed\xfe', b'\xce\xfa\xed\xfe'):
                endian = '<'
            elif magic in (b'\xfe\xed\xfa\xcf', b'\xfe\xed\xfa\xce'):
                endian = '>'
            else:
                raise ValueError('Invalid Mach-O slice header: ' + str(path))
            is_64 = magic in (b'\xcf\xfa\xed\xfe', b'\xfe\xed\xfa\xcf')
            header_size = 32 if is_64 else 28
            if len(header) < header_size:
                raise ValueError('Truncated Mach-O header: ' + str(path))
            command_count = struct.unpack_from(endian + 'I', header, 16)[0]
            command_bytes = struct.unpack_from(endian + 'I', header, 20)[0]
            if command_count > 65535 or command_bytes > size - header_size:
                raise ValueError('Invalid Mach-O load commands: ' + str(path))
            stream.seek(offset + header_size)
            commands = stream.read(command_bytes)
            position = 0
            minimum = None
            for _ in range(command_count):
                if position + 8 > len(commands):
                    raise ValueError('Truncated Mach-O load command: ' + str(path))
                command, command_size = struct.unpack_from(endian + 'II', commands, position)
                if command_size < 8 or position + command_size > len(commands):
                    raise ValueError('Invalid Mach-O load command: ' + str(path))
                if command == 0x32 and command_size >= 24:  # LC_BUILD_VERSION
                    minimum = _decode_macos_version(struct.unpack_from(endian + 'I', commands, position + 12)[0])
                elif command == 0x24 and command_size >= 16:  # LC_VERSION_MIN_MACOSX
                    minimum = _decode_macos_version(struct.unpack_from(endian + 'I', commands, position + 8)[0])
                position += command_size
            architecture = {0x1000007: 'x86_64', 0x100000c: 'arm64'}.get(cpu, hex(cpu))
            if minimum is None:
                raise ValueError('Mach-O binary has no macOS deployment target: ' + str(path))
            targets[architecture] = minimum
    return targets


def validate_macos_compatibility(path, maximum=MAXIMUM_MACOS_DEPLOYMENT_TARGET):
    targets = macho_deployment_targets(path)
    for architecture, minimum in targets.items():
        if minimum > maximum:
            actual = '.'.join(str(value) for value in minimum)
            supported = '.'.join(str(value) for value in maximum)
            raise ValueError('Mac binary requires macOS ' + actual + ' instead of ' + supported
                             + ' or earlier (' + architecture + '): ' + str(path))
    return targets


def bundle_executable(bundle):
    bundle = Path(bundle)
    with (bundle / 'Contents/Info.plist').open('rb') as stream:
        info = plistlib.load(stream)
    exe = info.get('CFBundleExecutable', '')
    if not safe_leaf(exe):
        raise ValueError('Invalid CFBundleExecutable: ' + str(bundle))
    result = bundle / 'Contents/MacOS' / exe
    if not result.is_file():
        raise ValueError('Missing Mac binary: ' + str(result))
    return result


def validate_plugin(bundle, replacing_followers=False):
    bundle = Path(bundle)
    if not {'arm64', 'x86_64'} <= macho_arches(bundle_executable(bundle)):
        raise ValueError('Plugin must contain arm64 AND x86_64: ' + str(bundle))
    followers = [p for p in bundle.rglob('*.sem') if p.is_dir() and
                 re.fullmatch(r'(NoisebudFollower(?:V2)?|NBSignalCore)(?:_v[0-9_]+)?\.sem', p.name)]
    if not followers:
        raise ValueError('Protected plugin contains no NB Follower: ' + str(bundle))
    for binary in bundle.rglob('*'):
        if not binary.is_file() or 'MacOS' not in binary.parts:
            continue
        if replacing_followers and any(parent in followers for parent in binary.parents):
            continue
        arches = macho_arches(binary)
        if arches and not {'arm64', 'x86_64'} <= arches:
            raise ValueError('Non-universal embedded binary: ' + str(binary))
        if arches:
            validate_macos_compatibility(binary)
    return followers


def copy_tree(source, target, mac_only=False):
    source, target = Path(source), Path(target)
    for path in [source] + list(source.rglob('*')):
        if path.is_symlink() or (getattr(path.stat(), 'st_file_attributes', 0) & 0x400):
            raise ValueError('Symlink/reparse point is not allowed in export: ' + str(path))
    def ignored(directory, names):
        return [n for n in names if n in ('__pycache__', '.DS_Store', '_CodeSignature')
                or (mac_only and (n in ('x86_64-win', 'x86-win', 'arm64-win') or n.endswith(('.pdb', '.lib', '.exp'))))]
    shutil.copytree(source, target, ignore=ignored)


def inventory(root):
    root = Path(root)
    return {p.relative_to(root).as_posix(): sha256(p) for p in sorted(root.rglob('*'))
            if p.is_file() and p.relative_to(root).as_posix() != 'files.json'}


def validate_package(root):
    root = Path(root)
    expected = read_json(root / 'files.json')
    # Reject symlinks before hashing or following any paths.
    if any(p.is_symlink() for p in root.rglob('*')):
        raise ValueError('Unexpected symlink in package')
    actual = inventory(root)
    if actual != expected:
        raise ValueError('Package files do not match the Windows SHA-256 inventory')
    recipe = read_json(root / 'package.json')
    if recipe.get('schemaVersion') != 1 or recipe.get('purpose') != 'noisebud-mac-product':
        raise ValueError('Unsupported Mac product package')
    metadata = read_json(root / 'installer.json')
    manifest = read_json(root / 'product.json')
    reg, names = validate_metadata(metadata, manifest)
    expected_bundles = [metadata['pluginBundleName'], metadata['pluginBundleName'][:-5] + '.component']
    if recipe.get('bundles') != expected_bundles:
        raise ValueError('AU/VST3 payload names disagree with metadata')
    for name in expected_bundles:
        validate_plugin(root / 'payload' / name, replacing_followers=True)
    return metadata, manifest, reg, names


def registration_app_path(app_id):
    if not re.fullmatch(r'[a-z0-9_-]{1,100}', app_id):
        raise ValueError('Unsafe app ID')
    return PurePosixPath('/Applications/Noisebud/Registration') / app_id / 'Noisebud Registration.app'

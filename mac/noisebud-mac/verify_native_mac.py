"""Mac release gate; shared with app consumers without licensing side effects."""
import argparse
import json
import plistlib
import subprocess
import sys
from pathlib import Path

# In consumers, package_common.py is copied beside this tool from Follower.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'mac-packaging'))
from package_common import macho_arches, validate_macos_compatibility


def validate_tree(root, architectures, minimum):
    root = Path(root)
    if not root.exists():
        raise ValueError('Missing native payload: ' + str(root))
    files = [root] if root.is_file() else root.rglob('*')
    count = 0
    for path in files:
        if not path.is_file():
            continue
        arches = macho_arches(path)
        if arches:
            if not set(architectures) <= arches:
                raise ValueError('Missing required native slices: ' + str(path))
            validate_macos_compatibility(path, minimum)
            count += 1
    if not count:
        raise ValueError('No Mach-O code found: ' + str(root))
    return count


def validate_launch_plist(bundle):
    with (Path(bundle) / 'Contents/Info.plist').open('rb') as stream:
        info = plistlib.load(stream)
    if info.get('LSRequiresNativeExecution') is not True:
        raise ValueError('App lacks native Launch Services policy: ' + str(bundle))


def validate_probe(executable, parent_architecture=None):
    command = [str(executable), '--native-process-probe']
    if parent_architecture:
        command = ['/usr/bin/arch', '-' + parent_architecture] + command
    result = subprocess.run(command, check=True, capture_output=True, text=True, timeout=45)
    info = json.loads(result.stdout)
    if (info.get('native') is not True or info.get('translated') is not False
            or info.get('architecture') not in ('arm64', 'x86_64')
            or info.get('architecture') != info.get('hardwareArchitecture')):
        raise ValueError('Unexpected translated helper process: ' + str(executable))
    if not info.get('executable') or not isinstance(info.get('pid'), int):
        raise ValueError('Incomplete native process probe: ' + str(executable))
    print(json.dumps({'parentLaunchArchitecture': parent_architecture or 'default', **info}))
    return info


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', action='append', default=[])
    parser.add_argument('--architectures', nargs='+', default=['arm64', 'x86_64'])
    parser.add_argument('--minimum', default='11.0')
    parser.add_argument('--app', action='append', default=[])
    parser.add_argument('--probe', action='append', default=[])
    parser.add_argument('--translated-parent', action='store_true')
    args = parser.parse_args()
    minimum = tuple(int(v) for v in args.minimum.split('.'))
    minimum = (minimum + (0, 0, 0))[:3]
    for root in args.root:
        count = validate_tree(root, args.architectures, minimum)
        print(f'Native slices/minimum OS verified: {count} Mach-O files in {root}')
    for app in args.app:
        validate_launch_plist(app)
    for executable in args.probe:
        info = validate_probe(executable)
        if args.translated_parent and info['hardwareArchitecture'] == 'arm64':
            # Rosetta is allowed here only as a developer-side regression test.
            validate_probe(executable, 'x86_64')


if __name__ == '__main__':
    main()

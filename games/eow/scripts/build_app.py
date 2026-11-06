#!/usr/bin/env python3
"""Package a finished Echoes of Wisdom work folder (see README.md, Build) as a self-contained Mac app.

    python3.14 scripts/build_app.py --work EoW_build --out "/Applications/Echoes of Wisdom.app"
                                    [--game <file with the control data> --keys prod.keys]

Everything is checked against assets/expected-v1.1.0.json first. The app holds the game data as a signed bundle in
Contents/Helpers, cloned from the work folder when both are on one APFS volume. Saves, the shader cache and the log go to
~/Library/Application Support/MacNativeGames/EoW and ~/Library/Logs/MacNativeGames/eow.log, never into the app.
--game and --keys only give the app the game's own icon; without them, or if that fails, it gets the default icon.
"""
import argparse
import hashlib
import json
import os
import plistlib
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
EXPECTED = json.loads((HERE / 'assets/expected-v1.1.0.json').read_text())
VERSION = 'v110'
MODULES = ('main', 'sdk', 'subsdk0', 'subsdk1')
BUNDLE_ID = 'org.macnativegames.eow'
APP_NAME = 'Echoes of Wisdom'
EXECUTABLE = 'EoW'
LSREGISTER = '/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister'


class BuildError(Exception):
    pass


def say(text):
    print(text, flush=True)


def run(command, log=None, cwd=None):
    r = subprocess.run([str(c) for c in command], cwd=cwd, capture_output=True, text=True)
    if log:
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text(r.stdout + r.stderr)
    if r.returncode:
        raise BuildError(f'{Path(str(command[0])).name} failed: {(r.stdout + r.stderr).strip()[-600:]}')


def sha256(path):
    digest = hashlib.sha256()
    with path.open('rb') as f:
        while block := f.read(1 << 22):
            digest.update(block)
    return digest.hexdigest()


def clone(src, dst):
    """A copy-on-write clone (cp -c) on one APFS volume, else a normal copy; for a file or a folder."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    if subprocess.run(['cp', '-Rc', str(src), str(dst)], capture_output=True).returncode:
        if src.is_dir():
            shutil.copytree(src, dst)
        else:
            shutil.copy2(src, dst)


def check_work(work):
    for name, want in EXPECTED['rewritten_sha256'].items():
        path = work / f'rewritten/{VERSION}' / name
        if not path.is_file() or sha256(path) != want:
            raise BuildError(f'{path}: missing or not the expected rewrite; run the Build steps of README.md')
    index = json.loads((work / f'extracted/{VERSION}/romfs/index.json').read_text())
    if index.get('romfs_sha256') != EXPECTED['romfs']['sha256']:
        raise BuildError('the RomFS index does not match the expected game data')
    needed = ['build/eow-native', 'build/spirv-cross/spirv-cross', 'build/shader-service/totk_xlate',
              f'extracted/{VERSION}/romfs/romfs.bin', f'extracted/{VERSION}/romfs/runtime-index.bin',
              'extracted/firmware/shared-fonts/manifest.json']
    for rel in needed:
        if not (work / rel).is_file():
            raise BuildError(f'{work / rel} is missing; run the Build steps of README.md')


def make_launcher(work, logs):
    out = work / 'build/app-launcher'
    run(['xcrun', 'clang', '-fobjc-arc', '-O2', '-Wall', '-Iruntime', '-Ithird_party/d2r_gfx/gfx/nvn', 'app/launcher.m',
         '-framework', 'AppKit', '-o', out], logs / 'launcher.log', cwd=HERE)
    return out


def make_icon(work, game, keys, logs):
    icon = work / 'build/AppIcon.icns'
    if icon.exists() or not (game and keys):
        return icon if icon.exists() else None
    try:
        run([work / '.venv/bin/python', 'scripts/extract_app_icon.py', game, '--keys', keys, '--title', EXPECTED['title_id'],
             '--output', icon], logs / 'app-icon.log', cwd=HERE)
        return icon
    except BuildError as error:
        say(f'no app icon, the app gets the default one ({str(error).splitlines()[0][:120]})')
        return None


def make_data_bundle(work, logs):
    """The RomFS image as a bundle of its own, signed once and kept in the work folder, so a new packaging need not hash it."""
    image = work / f'extracted/{VERSION}/romfs/romfs.bin'
    bundle = work / f'build/data-bundles/GameData-{VERSION}.bundle'
    stamp = bundle.with_name(bundle.name + '.json')
    st = image.stat()
    want = dict(size=st.st_size, mtime_ns=st.st_mtime_ns, sha256=EXPECTED['romfs']['sha256'])
    try:
        if (bundle / 'Contents/_CodeSignature/CodeResources').is_file() and json.loads(stamp.read_text()) == want:
            return bundle
    except (OSError, ValueError):
        pass
    say('signing the game data once (later builds reuse it)')
    tmp = bundle.with_name(bundle.name + '.partial')
    shutil.rmtree(tmp, ignore_errors=True)
    clone(image, tmp / 'Contents/Resources/romfs.bin')
    (tmp / 'Contents/Info.plist').write_bytes(plistlib.dumps(dict(
        CFBundleIdentifier=f'{BUNDLE_ID}.data.{VERSION}', CFBundleName='Game data', CFBundlePackageType='BNDL',
        CFBundleShortVersionString=EXPECTED['display_version'], CFBundleVersion='1')))
    run(['codesign', '--force', '--sign', '-', tmp], logs / 'sign-data.log')
    shutil.rmtree(bundle, ignore_errors=True)
    tmp.rename(bundle)
    stamp.write_text(json.dumps(want))
    return bundle


def assemble(work, out, launcher, icon, logs):
    tmp = out.with_name(out.name + '.partial')
    shutil.rmtree(tmp, ignore_errors=True)
    res = tmp / 'Contents/Resources/eow'
    files = ['build/eow-native', 'build/spirv-cross/spirv-cross', 'build/shader-service/totk_xlate',
             f'extracted/{VERSION}/romfs/runtime-index.bin', *(f'rewritten/{VERSION}/{m}.patched.bin' for m in MODULES)]
    for rel in files:
        clone(work / rel, res / rel)
    clone(work / 'extracted/firmware/shared-fonts', res / 'extracted/firmware/shared-fonts')
    libs = work / 'build/shader-libs'
    if libs.is_dir():   # the shaders of earlier sessions, compiled ahead; src/ is only input for that compile
        clone(libs, res / 'build/shader-libs')
        shutil.rmtree(res / 'build/shader-libs/src', ignore_errors=True)
    bundle = make_data_bundle(work, logs)
    clone(bundle, tmp / 'Contents/Helpers' / bundle.name)
    link = res / f'extracted/{VERSION}/romfs/romfs.bin'
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(os.path.relpath(tmp / 'Contents/Helpers' / bundle.name / 'Contents/Resources/romfs.bin', link.parent))
    clone(launcher, tmp / f'Contents/MacOS/{EXECUTABLE}')
    info = dict(CFBundleDevelopmentRegion='en', CFBundleExecutable=EXECUTABLE, CFBundleIdentifier=BUNDLE_ID, CFBundleName=APP_NAME,
                CFBundleDisplayName=APP_NAME, CFBundlePackageType='APPL', CFBundleShortVersionString=EXPECTED['display_version'],
                CFBundleVersion=str(EXPECTED['version'] + 1), LSMinimumSystemVersion='26.0', NSHighResolutionCapable=True,
                LSApplicationCategoryType='public.app-category.adventure-games', LSSupportsGameMode=True,
                GCSupportsControllerUserInteraction=True)
    if icon:
        clone(icon, tmp / 'Contents/Resources/AppIcon.icns')
        info['CFBundleIconFile'] = 'AppIcon'
    (tmp / 'Contents/Info.plist').write_bytes(plistlib.dumps(info))
    (tmp / 'Contents/PkgInfo').write_bytes(b'APPL????')
    run(['codesign', '--force', '--sign', '-', tmp], logs / 'sign-app.log')
    run(['codesign', '--verify', '--strict', tmp], logs / 'verify-app.log')
    return tmp


def install(new, out):
    binary = str((out / 'Contents/Resources/eow/build/eow-native').resolve())
    running = subprocess.run(['ps', '-axo', 'comm='], capture_output=True, text=True, check=True).stdout.splitlines()
    if binary in (p.strip() for p in running):
        raise BuildError('the game is running from the installed app; close it and build again')
    if out.exists():
        old = out.with_name(f'{out.name}.replaced-{os.getpid()}')
        out.rename(old)
        new.rename(out)
        shutil.rmtree(old)
    else:
        new.rename(out)


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--work', type=Path, required=True, help='the finished work folder')
    ap.add_argument('--out', type=Path, required=True, help='the app to write')
    ap.add_argument('--game', type=Path, help='a file with the control data, for the icon')
    ap.add_argument('--keys', type=Path, help='prod.keys, for the icon')
    a = ap.parse_args(argv)
    work, out = a.work.expanduser().resolve(), a.out.expanduser().resolve()
    if out.suffix != '.app':
        raise BuildError(f'the output must end in .app: {out}')
    logs = work / 'logs/app'
    say('checking the work folder against the expected hashes')
    check_work(work)
    say('the launcher and the icon')
    launcher = make_launcher(work, logs)
    icon = make_icon(work, a.game and a.game.expanduser().resolve(), a.keys and a.keys.expanduser().resolve(), logs)
    say(f'assembling {out}')
    new = assemble(work, out, launcher, icon, logs)
    install(new, out)
    run([LSREGISTER, '-f', out], logs / 'register-app.log')
    say('macOS checks the app once (a few minutes for a new one)')
    r = subprocess.run([str(out / f'Contents/MacOS/{EXECUTABLE}'), '--check'], capture_output=True, text=True, timeout=1800)
    if r.returncode:
        raise BuildError(f'the app does not start: {(r.stdout + r.stderr).strip()}')
    say(f'done: {out}')


if __name__ == '__main__':
    try:
        main(sys.argv[1:])
    except BuildError as error:
        sys.exit(f'error: {error}')

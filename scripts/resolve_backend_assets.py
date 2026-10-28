"""Reuse pinned Vulkan/CUDA release assets when their component IDs exist."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from urllib.request import Request, urlopen
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from inference.backend_component import compatibility_key
from inference.vulkan import BINDING_COMMIT, LLAMA_COMMIT


def request(url: str, token: str = ''):
    headers = {'User-Agent': 'SeeName-release-builder', 'Accept': 'application/vnd.github+json'}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    return urlopen(Request(url, headers=headers), timeout=60)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def download(url: str, path: Path, token: str = '') -> None:
    with request(url, token) as response:
        if not response.url.startswith('https://'):
            raise ValueError('GitHub asset download redirected away from HTTPS')
        path.write_bytes(response.read())


def download_bytes(url: str) -> bytes:
    with request(url) as response:
        if not response.url.startswith('https://'):
            raise ValueError('GitHub asset download redirected away from HTTPS')
        return response.read()


def list_releases(repository: str, token: str):
    page = 1
    while True:
        url = f'https://api.github.com/repos/{repository}/releases?per_page=100&page={page}'
        with request(url, token) as response:
            data = json.loads(response.read().decode('utf-8'))
        if not data:
            return
        yield from data
        if len(data) < 100:
            return
        page += 1


def verify_archive(archive: Path, entry: dict) -> None:
    if archive.stat().st_size != entry['size'] or sha256_file(archive) != entry['sha256']:
        raise ValueError('Reused backend archive does not match its catalog checksum')
    with zipfile.ZipFile(archive) as zipped:
        if len(zipped.infolist()) != len(entry['files']) or set(zipped.namelist()) != set(entry['files']):
            raise ValueError('Reused backend archive inventory does not match its catalog')
        for name, info in entry['files'].items():
            item = zipped.getinfo(name)
            if item.file_size != info['size'] or sha256_bytes(zipped.read(name)) != info['sha256']:
                raise ValueError('Reused backend file failed catalog verification: ' + name)


def component_id(backend: str, version: str) -> str:
    if backend == 'vulkan':
        return f'vulkan-{LLAMA_COMMIT[:12]}-windows-x64-{version}'
    return f'cuda-12-4-{LLAMA_COMMIT[:12]}-windows-x64-{version}'


def reuse_component(*, repository: str, current_tag: str, backend: str, version: str,
                    output: Path, download_base_url: str, token: str) -> bool:
    identifier = component_id(backend, version)
    catalog_name = identifier + '.catalog.json'
    expected_compatibility = compatibility_key()
    historical = {}
    pinned_release = None
    pinned_assets = None
    pinned_entry = None
    for release in list_releases(repository, token):
        if release.get('tag_name') == current_tag or release.get('draft'):
            continue
        assets = {item['name']: item for item in release.get('assets', [])}
        prefix = ('vulkan-' if backend == 'vulkan' else 'cuda-12-4-') + LLAMA_COMMIT[:12] + '-windows-x64-'
        for name, asset in assets.items():
            if not name.startswith(prefix) or not name.endswith('.catalog.json'):
                continue
            with request(asset['browser_download_url']) as response:
                if not response.url.startswith('https://'):
                    raise ValueError('Backend catalog download redirected away from HTTPS')
                candidate_catalog = json.loads(response.read().decode('utf-8'))
            candidate = candidate_catalog.get('windows-x64') if candidate_catalog.get('schema') == 1 else None
            if not isinstance(candidate, dict) or not candidate.get('id') or candidate['id'] + '.catalog.json' != name:
                continue
            if candidate.get('binding_commit') != BINDING_COMMIT or candidate.get('llama_commit') != LLAMA_COMMIT:
                continue
            if candidate.get('compatibility_key', expected_compatibility) != expected_compatibility:
                continue
            binary_name = candidate['id'] + '.zip'
            if binary_name not in assets:
                continue
            candidate['compatibility_key'] = expected_compatibility
            candidate['url'] = f'https://github.com/{repository}/releases/download/{release["tag_name"]}/{binary_name}'
            previous = historical.get(candidate['id'])
            if previous and previous.get('sha256') != candidate.get('sha256'):
                raise ValueError(f'Immutable backend ID has conflicting hashes: {candidate["id"]}')
            historical.setdefault(candidate['id'], candidate)
            if name == catalog_name:
                pinned_release, pinned_assets, pinned_entry = release, assets, candidate

    if pinned_entry is None:
        output.mkdir(parents=True, exist_ok=True)
        (output / (backend + '-history.json')).write_text(
            json.dumps({'schema': 1, 'versions': list(historical.values())}, indent=2) + '\n', encoding='utf-8')
        print(f'No published {backend} asset for pinned ID {identifier}; a new build is required.', flush=True)
        return False

    archive_name = identifier + '.zip'
    source_name = identifier + '-source.zip'
    required = (archive_name, archive_name + '.sha256', source_name, source_name + '.sha256')
    missing = [name for name in required if name not in pinned_assets]
    if missing:
        raise ValueError(f'Pinned {backend} release is missing assets: ' + ', '.join(missing))

    output.mkdir(parents=True, exist_ok=True)
    archive = output / archive_name
    source = output / source_name
    download(pinned_assets[archive_name]['browser_download_url'], archive)
    verify_archive(archive, pinned_entry)
    download(pinned_assets[source_name]['browser_download_url'], source)
    source_checksum = download_bytes(pinned_assets[source_name + '.sha256']['browser_download_url'])
    checksum_match = re.match(rb'^([0-9a-fA-F]{64})\s+\*?' + source_name.encode('ascii') + rb'(?:\r?\n)?$', source_checksum)
    if not checksum_match or sha256_file(source).lower() != checksum_match.group(1).decode('ascii').lower():
        raise ValueError('Reused backend source archive does not match its published checksum')
    (output / (archive_name + '.sha256')).write_text(
        pinned_entry['sha256'] + '  ' + archive_name + '\n', encoding='ascii')
    (output / (source_name + '.sha256')).write_text(
        sha256_file(source) + '  ' + source_name + '\n', encoding='ascii')

    if download_base_url:
        pinned_entry['url'] = download_base_url.rstrip('/') + '/' + archive_name
    versions = [item for key, item in historical.items() if key != identifier]
    (output / catalog_name).write_text(
        json.dumps({'schema': 1, 'windows-x64': pinned_entry, 'versions': versions}, indent=2) + '\n', encoding='utf-8')
    print(f'Reused {backend} {identifier} from release {pinned_release["tag_name"]}; catalog includes {len(versions)} older compatible version(s)', flush=True)
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repository', required=True, help='owner/name of the current GitHub repository')
    parser.add_argument('--current-tag', required=True)
    parser.add_argument('--vulkan-version', required=True)
    parser.add_argument('--cuda-version', required=True)
    parser.add_argument('--output', type=Path, default=ROOT/'dist'/'backends')
    parser.add_argument('--download-base-url', default='', help='Current release URL for copied component assets')
    args = parser.parse_args()
    if '/' not in args.repository or (args.download_base_url and not args.download_base_url.startswith('https://')):
        parser.error('Use owner/name and HTTPS URLs.')
    token = os.environ.get('GITHUB_TOKEN', '')
    output = args.output.resolve()
    reused = {}
    for backend, version in (('vulkan', args.vulkan_version), ('cuda', args.cuda_version)):
        reused[backend] = reuse_component(repository=args.repository, current_tag=args.current_tag,
                                          backend=backend, version=version, output=output,
                                          download_base_url=args.download_base_url, token=token)
    output_file = os.environ.get('GITHUB_OUTPUT')
    if output_file:
        with open(output_file, 'a', encoding='utf-8') as stream:
            for backend, value in reused.items():
                stream.write(f'{backend}_reused={str(value).lower()}\n')
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as error:
        print('BACKEND ASSET RESOLUTION FAILED:', error, file=sys.stderr)
        raise SystemExit(1)

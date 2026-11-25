"""Copy image files from the primary server over the restricted read-only key.

The standby's writable databases are intentionally left alone. Database snapshots
are transferred separately and only applied during a planned failover.
"""

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
import subprocess
import tempfile


ROOT = Path('/opt/astrogate-img')
SOURCE = 'root@154.94.226.49:./'
KEY = '/root/.ssh/astrogate_img_replica_read'
DIRECTORIES = ('data/images', 'data/gateway-assets', 'upstream-data/images')


def _rsync(*args: str, timeout: int = 21600) -> str:
    result = subprocess.run(
        ['rsync', '-e',
         f'ssh -i {KEY} -o IdentitiesOnly=yes -o BatchMode=yes -o ConnectTimeout=12',
         *args],
        capture_output=True, text=True, timeout=timeout,
    )
    if result.returncode:
        raise RuntimeError(f'rsync exit {result.returncode}: {result.stderr[-300:]}')
    return result.stdout


def _remote_files(directory: str) -> list[tuple[int, str]]:
    listing = _rsync('-r', '--list-only', SOURCE + directory + '/', timeout=60)
    files = []
    for line in listing.splitlines():
        fields = line.split(maxsplit=4)
        if len(fields) == 5 and fields[0].startswith('-'):
            files.append((int(fields[1].replace(',', '')), fields[4]))
    return files


def pull_directory(directory: str, workers: int) -> tuple[int, int]:
    files = _remote_files(directory)
    if not files:
        return 0, 0
    buckets: list[list[str]] = [[] for _ in range(min(workers, len(files)))]
    totals = [0] * len(buckets)
    for size, name in sorted(files, reverse=True):
        index = min(range(len(buckets)), key=totals.__getitem__)
        buckets[index].append(name)
        totals[index] += size
    destination = ROOT / directory
    destination.mkdir(parents=True, exist_ok=True)

    def copy_bucket(index: int, manifest: str) -> None:
        _rsync('-a', '--numeric-ids', '--partial-dir=.rsync-partial',
               '--files-from=' + manifest, SOURCE + directory + '/',
               str(destination) + '/')

    with tempfile.TemporaryDirectory(prefix='media-sync-', dir=ROOT) as temp:
        manifests = []
        for index, names in enumerate(buckets):
            path = Path(temp) / f'{index}.txt'
            path.write_text('\n'.join(names) + '\n')
            manifests.append(str(path))
        with ThreadPoolExecutor(max_workers=len(manifests)) as executor:
            futures = [executor.submit(copy_bucket, i, manifest)
                       for i, manifest in enumerate(manifests)]
            for future in as_completed(futures):
                future.result()
    return len(files), sum(size for size, _ in files)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workers', type=int, default=4)
    arguments = parser.parse_args()
    if not 1 <= arguments.workers <= 24:
        parser.error('--workers must be between 1 and 24')
    for folder in DIRECTORIES:
        count, total = pull_directory(folder, arguments.workers)
        print(f'{folder}: {count} files, {total} source bytes', flush=True)

#!/usr/bin/env python
# License: BSD-3-Clause Copyright: 2026, Kovid Goyal <kovid at kovidgoyal.net>

"""Release kgcss: push master and a signed v<version> tag.

The tag starts .github/workflows/release.yml, which builds the sdist and the wheels and uploads
them to PyPI with Trusted Publishing. This script then waits for that workflow run to finish and for
the release to appear on PyPI. Set GITHUB_TOKEN to raise the GitHub API rate limit.
"""

import ast
import base64
import json
import os
import re
import shlex
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

NAME = 'kgcss'
BRANCH = 'master'
ROOT = Path(__file__).resolve().parent
REPO = 'kovidgoyal/kgcss'
ACTIONS_URL = f'https://github.com/{REPO}/actions/workflows/release.yml'
API_URL = f'https://api.github.com/repos/{REPO}'
POLL_INTERVAL = 60  # seconds, unauthenticated GitHub API requests are limited to 60 an hour
FIND_RUN_TIMEOUT = 10 * 60
RUN_TIMEOUT = 3 * 60 * 60
PYPI_TIMEOUT = 15 * 60


def read_version() -> str:
    """Read __version__ from src/kgcss/_version.py without importing anything from the package."""
    path = ROOT / 'src' / NAME / '_version.py'
    for node in ast.parse(path.read_text(encoding='utf-8'), str(path)).body:
        if (
            isinstance(node, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == '__version__' for t in node.targets)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ):
            return node.value.value
    raise SystemExit(f'No __version__ string found in {path}')


VERSION = read_version()
TAG = f'v{VERSION}'


def red(text: str) -> str:
    return '\033[91;1m' + text + '\033[39;22m'


def green(text: str) -> str:
    return '\033[92;1m' + text + '\033[39;22m'


def notify(title: str, body: str = '') -> None:
    """Ring the terminal bell and show a desktop notification with OSC 99, if stdout is a terminal."""
    if not sys.stdout.isatty():
        return

    def b64(text: str) -> str:
        return base64.b64encode(re.sub(r'\033\[[0-9;]*m', '', text).encode()).decode()

    ident = f'i={NAME}-publish:e=1'
    if body:
        osc = f'\033]99;{ident}:d=0;{b64(title)}\033\\\033]99;{ident}:p=body;{b64(body)}\033\\'
    else:
        osc = f'\033]99;{ident};{b64(title)}\033\\'
    sys.stdout.write('\a' + osc)
    sys.stdout.flush()


def run(*cmd: str) -> None:
    print(green(shlex.join(cmd)), flush=True)
    ret = subprocess.run(cmd, cwd=ROOT).returncode
    if ret != 0:
        raise SystemExit(ret)


def output(*cmd: str) -> str:
    return subprocess.run(cmd, cwd=ROOT, check=True, capture_output=True, text=True).stdout.strip()


def check_repo() -> None:
    if (branch := output('git', 'branch', '--show-current')) != BRANCH:
        raise SystemExit(red(f'Releases are made from {BRANCH}, not {branch or "a detached HEAD"}'))
    if output('git', 'status', '--porcelain', '--untracked-files=no'):
        raise SystemExit(red('The working tree has uncommitted changes'))
    if output('git', 'tag', '--list', TAG) or output('git', 'ls-remote', '--tags', 'origin', f'refs/tags/{TAG}'):
        raise SystemExit(red(f'The tag {TAG} already exists, bump the version in src/{NAME}/_version.py'))


def fetch_json(url: str, headers: dict[str, str] | None = None) -> object:
    """GET url and decode the JSON body, waiting out GitHub rate limits and retrying transient errors."""
    while True:
        req = urllib.request.Request(url, headers={'User-Agent': f'{NAME}-publish', **(headers or {})})
        try:
            with urllib.request.urlopen(req, timeout=60) as res:
                return json.load(res)
        except urllib.error.HTTPError as e:
            if e.code in (403, 429) and e.headers.get('X-RateLimit-Remaining') == '0':
                delay = max(5, int(e.headers.get('X-RateLimit-Reset', '0')) - int(time.time()) + 5)
                print(f'GitHub API rate limit reached, waiting {delay} seconds (set GITHUB_TOKEN to avoid this)')
                time.sleep(delay)
                continue
            if e.code < 500:
                raise
            print(f'{url} failed with HTTP {e.code}, retrying')
        except (urllib.error.URLError, TimeoutError) as e:
            print(f'{url} failed with {e}, retrying')
        time.sleep(POLL_INTERVAL)


def github(path: str) -> dict[str, object]:
    headers = {'Accept': 'application/vnd.github+json', 'X-GitHub-Api-Version': '2022-11-28'}
    if token := os.environ.get('GITHUB_TOKEN'):
        headers['Authorization'] = f'Bearer {token}'
    data = fetch_json(f'{API_URL}/{path}', headers)
    if not isinstance(data, dict):
        raise SystemExit(red(f'Unexpected response from the GitHub API for {path}'))
    return data


def find_run(sha: str) -> dict[str, object]:
    """The release workflow run started by pushing TAG, which points at the commit sha."""
    deadline = time.monotonic() + FIND_RUN_TIMEOUT
    while True:
        data = github(f'actions/workflows/release.yml/runs?event=push&head_sha={sha}&per_page=20')
        runs = data.get('workflow_runs')
        for run_ in runs if isinstance(runs, list) else ():
            if isinstance(run_, dict) and run_.get('head_branch') == TAG:
                return run_
        if time.monotonic() > deadline:
            raise SystemExit(red(f'No release workflow run for {TAG} appeared, check {ACTIONS_URL}'))
        time.sleep(10)


def report_jobs(run_id: object) -> None:
    jobs = github(f'actions/runs/{run_id}/jobs?per_page=100').get('jobs')
    for job in jobs if isinstance(jobs, list) else ():
        if isinstance(job, dict):
            conclusion = str(job.get('conclusion') or job.get('status'))
            print(
                f'  {job.get("name")}: {green(conclusion) if conclusion in ("success", "skipped") else red(conclusion)}'
            )


def wait_for_run(sha: str) -> None:
    run_ = find_run(sha)
    run_id, url = run_['id'], run_.get('html_url', ACTIONS_URL)
    print(f'Waiting for the release workflow to build and upload {TAG}, follow it at:\n{url}', flush=True)
    deadline = time.monotonic() + RUN_TIMEOUT
    last_status = None
    while (status := run_.get('status')) != 'completed':
        if status != last_status:
            print(f'Workflow run status: {status}', flush=True)
            if status == 'waiting':
                print('The run is waiting for a deployment to the pypi environment to be approved')
            last_status = status
        if time.monotonic() > deadline:
            raise SystemExit(red(f'Gave up waiting for the release workflow, check {url}'))
        time.sleep(POLL_INTERVAL)
        run_ = github(f'actions/runs/{run_id}')
    conclusion = run_.get('conclusion')
    report_jobs(run_id)
    if conclusion != 'success':
        raise SystemExit(red(f'The release workflow finished with: {conclusion}, see {url}'))
    print(green('The release workflow succeeded'))


def wait_for_pypi() -> None:
    """Wait for the new version to be visible on PyPI, which lags the upload by a little."""
    url = f'https://pypi.org/pypi/{NAME}/{VERSION}/json'
    deadline = time.monotonic() + PYPI_TIMEOUT
    while True:
        try:
            with urllib.request.urlopen(
                urllib.request.Request(url, headers={'User-Agent': f'{NAME}-publish'}), timeout=60
            ):
                break
        except urllib.error.URLError, TimeoutError:
            pass
        if time.monotonic() > deadline:
            raise SystemExit(red(f'{NAME} {VERSION} has not appeared at {url}'))
        time.sleep(15)
    print(green(f'{NAME} {VERSION} is published: https://pypi.org/project/{NAME}/{VERSION}/'))


def publish() -> None:
    run('git', 'push', 'origin', BRANCH)
    run('git', 'tag', '-s', TAG, '-m', f'version-{VERSION}')
    run('git', 'push', 'origin', TAG)
    sha = output('git', 'rev-parse', f'{TAG}^{{commit}}')
    try:
        wait_for_run(sha)
        wait_for_pypi()
    except KeyboardInterrupt:
        print(f'\nStopped waiting, the release workflow continues at:\n{ACTIONS_URL}', file=sys.stderr)
        raise SystemExit(1) from None


def main() -> None:
    check_repo()
    if input(f'Publish {NAME} version {red(VERSION)} [y/n]? ') != 'y':
        raise SystemExit(1)
    try:
        publish()
    except SystemExit as e:
        if not isinstance(e.__context__, KeyboardInterrupt):
            notify(f'Publishing {NAME} {VERSION} failed', e.code if isinstance(e.code, str) else f'Exit code: {e.code}')
        raise
    except Exception as e:
        notify(f'Publishing {NAME} {VERSION} failed', f'{type(e).__name__}: {e}')
        raise
    notify(f'{NAME} {VERSION} published', f'https://pypi.org/project/{NAME}/{VERSION}/')


if __name__ == '__main__':
    main()

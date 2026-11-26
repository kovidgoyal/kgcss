#!/usr/bin/env python3
# License: BSD-3-Clause Copyright: 2026, Kovid Goyal <kovid at kovidgoyal.net>

"""Extract the test cases of the stylelint rules that kgcss.lint implements.

Writes tests/stylelint-rules/<rule>.json from the test files of stylelint and
of @stylistic/stylelint-plugin (for no-extra-semicolons), at the versions
pinned below and in bench/stylelint/package.json. Needs git, node and npm:

    python -I tools/extract_stylelint_tests.py /tmp/stylelint-work -o tests/stylelint-rules

The work directory must be new or empty; the two repositories are cloned and
their dependencies installed into it. The test files are then imported by
tools/stylelint/record_tests.mjs, which records the testRule() schemas
instead of running them.

Cases are kept as the test harnesses check them. Groups for other syntaxes
(customSyntax), with other plugins, languageOptions, referenceFiles or a
file name, and skipped cases are left out, and counted. Each fixture file is

    {"rule": name, "source": ..., "groups": [{
        "config": the rule's config, with {"$regexp": [source, flags]},
        "compute_edit_info": whether the warnings' fix (edit info) is checked,
        "accept": [{"code", "description"?, "fixed"?}],
        "reject": [{"code", "description"?, "fixed"?, "warnings": [
            {"message", "line"?, "column"?, "endLine"?, "endColumn"?, "fix"?}]}]}]}

A case's "fixed" is the expected result of fixing it, and is absent when the
harness does not check fixing. Messages end with " (rule-name)", as
stylelint reports them. tests/test_stylelint_fixtures.py checks every case
against the real stylelint.
"""

import argparse
import json
import shutil
import subprocess
import sys
from collections import Counter
from pathlib import Path

STYLELINT_VERSION = '18.1.0'
STYLISTIC_VERSION = '6.0.1'
STYLELINT_REPO = 'https://github.com/stylelint/stylelint'
STYLISTIC_REPO = 'https://github.com/stylelint-stylistic/stylelint-stylistic'
STYLISTIC_PREFIX = '@stylistic/'

# The rules calibre's Check Book uses. no-extra-semicolons is in @stylistic/stylelint-plugin.
RULES = (
    'annotation-no-unknown',
    'at-rule-no-unknown',
    'block-no-empty',
    'color-no-invalid-hex',
    'comment-no-empty',
    'custom-property-no-missing-var-function',
    'declaration-block-no-duplicate-custom-properties',
    'declaration-block-no-duplicate-properties',
    'declaration-block-no-shorthand-property-overrides',
    'font-family-no-duplicate-names',
    'font-family-no-missing-generic-family-keyword',
    'function-calc-no-unspaced-operator',
    'function-linear-gradient-no-nonstandard-direction',
    'function-no-unknown',
    'keyframe-block-no-duplicate-selectors',
    'keyframe-declaration-no-important',
    'media-feature-name-no-unknown',
    'named-grid-areas-no-invalid',
    'no-descending-specificity',
    'no-duplicate-at-import-rules',
    'no-duplicate-selectors',
    'no-empty-source',
    'no-extra-semicolons',
    'no-invalid-double-slash-comments',
    'no-invalid-position-at-import-rule',
    'no-irregular-whitespace',
    'property-no-unknown',
    'selector-pseudo-class-no-unknown',
    'selector-pseudo-element-no-unknown',
    'selector-type-no-unknown',
    'string-no-newline',
    'unit-no-unknown',
)
STYLISTIC_RULES = frozenset({'no-extra-semicolons'})

# Schema keys that make a group something kgcss does not lint: other syntaxes and setups
UNSUPPORTED_SCHEMA_KEYS = frozenset({'customSyntax', 'plugins', 'languageOptions', 'referenceFiles', 'codeFilename'})
SCHEMA_KEYS = frozenset({'ruleName', 'config', 'accept', 'reject', 'fix', 'computeEditInfo', 'autoStripIndent'})
WARNING_KEYS = ('message', 'line', 'column', 'endLine', 'endColumn', 'fix')
CASE_KEYS = frozenset({'code', 'description', 'fixed', 'unfixable', 'warnings', 'skip', 'only', *WARNING_KEYS})

type Json = bool | int | float | str | list[Json] | dict[str, Json] | None


def run(cmd: list[str], cwd: Path) -> None:
    print('$', ' '.join(cmd), file=sys.stderr)
    subprocess.run(cmd, cwd=cwd, check=True)


def clone(url: str, tag: str, dest: Path) -> str:
    if not dest.exists():
        run(['git', 'clone', '--quiet', '--depth', '1', '--branch', tag, url, str(dest)], dest.parent)
    return subprocess.run(
        ['git', 'rev-parse', 'HEAD'], cwd=dest, check=True, capture_output=True, text=True
    ).stdout.strip()


def setup(work: Path) -> tuple[Path, Path, str, str]:
    """Clone both repositories into work and install what their tests import."""
    stylelint, stylistic = work / 'stylelint', work / 'stylelint-stylistic'
    stylelint_commit = clone(STYLELINT_REPO, STYLELINT_VERSION, stylelint)
    stylistic_commit = clone(STYLISTIC_REPO, f'v{STYLISTIC_VERSION}', stylistic)
    if not (stylelint / 'node_modules').exists():
        run(['npm', 'ci', '--ignore-scripts', '--no-audit', '--no-fund'], stylelint)
    if not (stylistic / 'node_modules').exists():
        # The plugin is developed with pnpm (npm refuses its devEngines without --force), and its
        # tests import only its runtime dependencies, its own sources and stylelint
        npm_install = ['npm', 'install', '--ignore-scripts', '--no-audit', '--no-fund', '--no-package-lock']
        run([*npm_install, '--omit=dev', '--force'], stylistic)
        (stylistic / 'node_modules' / 'stylelint').symlink_to(stylelint, target_is_directory=True)
    return stylelint, stylistic, stylelint_commit, stylistic_commit


def test_files(stylelint: Path, stylistic: Path) -> dict[str, list[Path]]:
    ans: dict[str, list[Path]] = {}
    for rule in RULES:
        if rule in STYLISTIC_RULES:
            files = sorted((stylistic / 'lib' / 'rules' / rule).glob('index.test.ts'))
        else:
            files = sorted((stylelint / 'lib' / 'rules' / rule / '__tests__').glob('*.mjs'))
        if not files:
            raise SystemExit(f'No test files for {rule}')
        ans[rule] = files
    return ans


def record(files: list[Path], cwd: Path, work: Path) -> list[dict[str, Json]]:
    recorder = Path(__file__).resolve().parent / 'stylelint' / 'record_tests.mjs'
    node = shutil.which('node')
    assert node is not None
    output = work / 'recorded.json'
    subprocess.run([node, str(recorder), str(output), *map(str, files)], cwd=cwd, check=True)
    with output.open(encoding='utf-8') as f:
        data = json.load(f)
    output.unlink()
    assert isinstance(data, list)
    return data


def has_function(value: Json) -> bool:
    if isinstance(value, dict):
        return '$function' in value or any(has_function(v) for v in value.values())
    if isinstance(value, list):
        return any(has_function(v) for v in value)
    return False


def as_dict(value: Json) -> dict[str, Json]:
    assert isinstance(value, dict), value
    return value


def as_list(value: Json) -> list[Json]:
    assert isinstance(value, list), value
    return value


def normalize_warning(w: dict[str, Json]) -> dict[str, Json]:
    unknown = set(w) - set(WARNING_KEYS) - CASE_KEYS
    assert not unknown, unknown
    return {k: w[k] for k in WARNING_KEYS if k in w and w[k] is not None}


def normalize_group(schema: dict[str, Json], harness: str, stats: Counter[str]) -> dict[str, Json] | None:
    """Convert a testRule() schema into a fixture group, or None to leave it out."""
    unknown = set(schema) - SCHEMA_KEYS - UNSUPPORTED_SCHEMA_KEYS - {'only', 'skip'}
    if unknown:
        raise SystemExit(f'Unknown schema keys: {sorted(unknown)}')
    cases = len(as_list(schema.get('accept') or [])) + len(as_list(schema.get('reject') or []))
    if found := UNSUPPORTED_SCHEMA_KEYS & set(schema):
        stats['dropped: ' + '/'.join(sorted(found))] += cases
        return None
    if schema.get('skip') or has_function(schema['config']):
        stats['dropped: skipped or config with functions'] += cases
        return None
    if schema.get('autoStripIndent'):
        raise SystemExit('autoStripIndent is not supported')
    # stylelint's harness checks fixing when the schema says fix: true; the plugin's harness
    # (@morev/stylelint-testing-library) checks it for every case that has a "fixed"
    schema_fix = harness == 'stylelint' and bool(schema.get('fix'))
    compute_edit_info = bool(schema.get('computeEditInfo'))
    accept: list[Json] = []
    for raw in as_list(schema.get('accept') or []):
        case = as_dict(raw)
        if case.get('skip') or 'codeFilename' in case:
            stats['dropped: skipped case or file name'] += 1
            continue
        # The harnesses read only the code of an accept case (a few give their description as "message")
        assert set(case) <= CASE_KEYS, case
        item: dict[str, Json] = {'code': case['code']}
        if case.get('description'):
            item['description'] = case['description']
        if schema_fix:
            item['fixed'] = case['code']
        accept.append(item)
    reject: list[Json] = []
    for raw in as_list(schema.get('reject') or []):
        case = as_dict(raw)
        if case.get('skip') or 'codeFilename' in case:
            stats['dropped: skipped case or file name'] += 1
            continue
        unknown = set(case) - CASE_KEYS
        assert not unknown, unknown
        warnings = (
            [normalize_warning(as_dict(w)) for w in as_list(case['warnings'])]
            if 'warnings' in case
            else [normalize_warning({k: v for k, v in case.items() if k in WARNING_KEYS})]
        )
        if any('fix' in w for w in warnings):
            compute_edit_info = True
        item = {'code': case['code']}
        if case.get('description'):
            item['description'] = case['description']
        if harness == 'stylelint':
            if schema_fix:
                # An unfixable case must come out of fixing unchanged
                item['fixed'] = case['code'] if case.get('unfixable') else case['fixed']
                assert isinstance(item['fixed'], str), case
        elif isinstance(case.get('fixed'), str):
            item['fixed'] = case['fixed']
        item['warnings'] = list(warnings)
        reject.append(item)
    if not accept and not reject:
        return None
    stats['kept'] += len(accept) + len(reject)
    return {'config': schema['config'], 'compute_edit_info': compute_edit_info, 'accept': accept, 'reject': reject}


def write_fixtures(work: Path, output: Path) -> None:
    stylelint, stylistic, stylelint_commit, stylistic_commit = setup(work)
    files = test_files(stylelint, stylistic)
    output.mkdir(parents=True, exist_ok=True)
    for old in output.glob('*.json'):
        old.unlink()
    for rule, paths in files.items():
        stylistic_rule = rule in STYLISTIC_RULES
        repo = stylistic if stylistic_rule else stylelint
        name = STYLISTIC_PREFIX + rule if stylistic_rule else rule
        stats: Counter[str] = Counter()
        groups: list[Json] = []
        for entry in record(paths, repo, work):
            schema = as_dict(entry['schema'])
            if schema['ruleName'] != name:
                raise SystemExit(f'{paths}: a test for {schema["ruleName"]}, expected {name}')
            harness = entry['harness']
            assert isinstance(harness, str)
            if (group := normalize_group(schema, harness, stats)) is not None:
                groups.append(group)
        sources = [str(p.relative_to(repo)) for p in paths]
        if stylistic_rule:
            version = f'@stylistic/stylelint-plugin {STYLISTIC_VERSION}'
        else:
            version = f'stylelint {STYLELINT_VERSION}'
        fixture = {'rule': name, 'source': f'{version}: {", ".join(sources)}', 'groups': groups}
        with (output / f'{rule}.json').open('w', encoding='utf-8') as f:
            json.dump(fixture, f, ensure_ascii=False, indent=1)
            f.write('\n')
        print(f'{rule}: ' + ', '.join(f'{k} {v}' for k, v in sorted(stats.items())))
    shutil.copyfile(stylelint / 'LICENSE', output / 'LICENSE-stylelint')
    shutil.copyfile(stylistic / 'LICENSE.md', output / 'LICENSE-stylistic')
    (output / 'VENDORED').write_text(
        'Test cases extracted by tools/extract_stylelint_tests.py from the rule tests of\n'
        f'stylelint {STYLELINT_VERSION} ({STYLELINT_REPO} at {stylelint_commit}) and\n'
        f'@stylistic/stylelint-plugin {STYLISTIC_VERSION} ({STYLISTIC_REPO} at {stylistic_commit}),\n'
        'both MIT licensed (see LICENSE-stylelint and LICENSE-stylistic). Do not edit;\n'
        're-run the tool, and record deliberate differences in the test runners instead.\n'
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('work', type=Path, help='Directory to clone and install into (new or empty)')
    parser.add_argument('-o', '--output', type=Path, required=True, help='Where to write the fixtures')
    args = parser.parse_args()
    work: Path = args.work.resolve()
    work.mkdir(parents=True, exist_ok=True)
    known = {'stylelint', 'stylelint-stylistic'}
    if any(p.name not in known for p in work.iterdir()):
        raise SystemExit(f'{work} has files other than the clones of an earlier run')
    write_fixtures(work, args.output.resolve())


if __name__ == '__main__':
    main()

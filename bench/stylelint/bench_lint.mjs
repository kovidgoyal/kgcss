// License: BSD-3-Clause Copyright: 2026, Kovid Goyal <kovid at kovidgoyal.net>

// Times stylelint.lint() on files, for bench/bench_lint.py.
//
//     node bench_lint.mjs INSTALL_DIR BUDGET_SECONDS RULES_JSON FILE...
//
// Prints one JSON line per file: {"file", "ms", "warnings"}, the best time of
// several runs in the same process (so startup is not counted).

import { readFileSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { pathToFileURL } from 'node:url';

const [installDir, budgetText, rulesJson, ...files] = process.argv.slice(2);
const modules = join(resolve(installDir), 'node_modules');
const { default: stylelint } = await import(pathToFileURL(join(modules, 'stylelint', 'lib', 'index.mjs')).href);
const { default: plugins } = await import(pathToFileURL(join(modules, '@stylistic', 'stylelint-plugin', 'dist', 'index.js')).href);
const config = { plugins, rules: JSON.parse(rulesJson) };
const budget = Number(budgetText) * 1000;

for (const file of files) {
	const code = readFileSync(file, 'utf8');
	const times = [];
	let warnings = 0;
	const start = performance.now();
	while (times.length < 3 || (times.reduce((a, b) => a + b, 0) < budget && times.length < 50)) {
		const t = performance.now();
		const { results } = await stylelint.lint({ code, config });
		times.push(performance.now() - t);
		warnings = results[0].warnings.length;
		if (times[0] > budget) break;
	}
	process.stdout.write(JSON.stringify({ file, ms: Math.min(...times), warnings, runs: times.length, total: performance.now() - start }) + '\n');
}

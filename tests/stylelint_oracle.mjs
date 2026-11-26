// License: BSD-3-Clause Copyright: 2026, Kovid Goyal <kovid at kovidgoyal.net>

// The stylelint oracle: lints requests read from stdin, one JSON object per
// line, and writes one JSON object per line for each. Started by
// tests/stylelint_oracle.py.
//
//     node stylelint_oracle.mjs INSTALL_DIR
//
// INSTALL_DIR has stylelint and @stylistic/stylelint-plugin in its
// node_modules (bench/.cache/stylelint). A request is
// {"code": str, "rules": {name: config}, "fix": bool, "computeEditInfo": bool}.
// In configs, {"$regexp": [source, flags]} stands for a regular expression.

import { createInterface } from 'node:readline';
import { join, resolve } from 'node:path';
import { pathToFileURL } from 'node:url';

const root = resolve(process.argv[2]);
const load = (name, entry) => import(pathToFileURL(join(root, 'node_modules', name, entry)).href);
const { default: stylelint } = await load('stylelint', 'lib/index.mjs');
const { default: plugins } = await load('@stylistic/stylelint-plugin', 'dist/index.js');

function reviver(key, value) {
	if (value && typeof value === 'object' && Array.isArray(value.$regexp)) return new RegExp(...value.$regexp);
	return value;
}

function warning(w) {
	const ans = { rule: w.rule, severity: w.severity, text: w.text, line: w.line, column: w.column };
	for (const key of ['endLine', 'endColumn', 'fix']) if (w[key] !== undefined) ans[key] = w[key];
	return ans;
}

async function lint(request) {
	const { results, code } = await stylelint.lint({
		code: request.code,
		config: { plugins, rules: request.rules },
		fix: Boolean(request.fix),
		computeEditInfo: Boolean(request.computeEditInfo),
	});
	const [result] = results;
	return {
		warnings: result.warnings.map(warning),
		parseErrors: result.parseErrors.map((e) => e.text),
		invalidOptionWarnings: result.invalidOptionWarnings.map((e) => e.text),
		code: request.fix ? code : null,
	};
}

for await (const line of createInterface({ input: process.stdin, crlfDelay: Infinity })) {
	let response;
	try {
		response = await lint(JSON.parse(line, reviver));
	} catch (err) {
		response = { error: String(err && err.stack ? err.stack : err) };
	}
	process.stdout.write(JSON.stringify(response) + '\n');
}

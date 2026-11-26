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
// A request {"parse": str, "safe": bool} returns the tree that PostCSS (or
// postcss-safe-parser) parses, or the error it raises, to compare with
// kgcss.linter._postcss.

import { createInterface } from 'node:readline';
import { join, resolve } from 'node:path';
import { pathToFileURL } from 'node:url';

const root = resolve(process.argv[2]);
const load = (name, entry) => import(pathToFileURL(join(root, 'node_modules', name, entry)).href);
const { default: stylelint } = await load('stylelint', 'lib/index.mjs');
const { default: plugins } = await load('@stylistic/stylelint-plugin', 'dist/index.js');
const { default: postcss } = await load('postcss', 'lib/postcss.js');
const { default: safeParse } = await load('postcss-safe-parser', 'lib/safe-parse.js');

function reviver(key, value) {
	if (value && typeof value === 'object' && Array.isArray(value.$regexp)) return new RegExp(...value.$regexp);
	return value;
}

function warning(w) {
	const ans = { rule: w.rule, severity: w.severity, text: w.text, line: w.line, column: w.column };
	for (const key of ['endLine', 'endColumn', 'fix']) if (w[key] !== undefined) ans[key] = w[key];
	return ans;
}

const NODE_FIELDS = ['name', 'params', 'selector', 'prop', 'value', 'important', 'text'];

function tree(node) {
	const ans = { type: node.type, start: node.source?.start?.offset ?? null, end: node.source?.end?.offset ?? null };
	for (const key of NODE_FIELDS) if (node[key] !== undefined) ans[key] = node[key];
	ans.raws = { ...node.raws };
	if (node.nodes !== undefined) ans.nodes = node.nodes.map(tree);
	return ans;
}

function parse(request) {
	try {
		return { tree: tree(request.safe ? safeParse(request.parse) : postcss.parse(request.parse)) };
	} catch (err) {
		if (err.name !== 'CssSyntaxError') throw err;
		return { error: { reason: err.reason, offset: err.input.offset, end: err.input.endOffset ?? null } };
	}
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
		code: request.fix ? (code ?? null) : null,
	};
}

for await (const line of createInterface({ input: process.stdin, crlfDelay: Infinity })) {
	let response;
	try {
		const request = JSON.parse(line, reviver);
		response = 'parse' in request ? parse(request) : await lint(request);
	} catch (err) {
		response = { failure: String(err && err.stack ? err.stack : err) };
	}
	process.stdout.write(JSON.stringify(response) + '\n');
}

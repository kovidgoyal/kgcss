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
// kgcss.linter._postcss. {"valueParse": str} returns the nodes of
// postcss-value-parser, and {"selectorParse": str} the tree of
// postcss-selector-parser (every own property of every node but its parent,
// with "toString" added: what the node prints as), or the error it throws.
// {"selectorSpecificity": str} returns the specificity that
// @csstools/selector-specificity gives the parsed root and each node it
// walks, and {"resolveNested": [selector, parent], "ignoreImplicitNesting":
// bool} the tree @csstools/selector-resolve-nested resolves selector to.

import { createInterface } from 'node:readline';
import { join, resolve } from 'node:path';
import { pathToFileURL } from 'node:url';

const root = resolve(process.argv[2]);
const load = (name, entry) => import(pathToFileURL(join(root, 'node_modules', name, entry)).href);
const { default: stylelint } = await load('stylelint', 'lib/index.mjs');
const { default: plugins } = await load('@stylistic/stylelint-plugin', 'dist/index.js');
const { default: postcss } = await load('postcss', 'lib/postcss.js');
const { default: safeParse } = await load('postcss-safe-parser', 'lib/safe-parse.js');
const { default: valueParser } = await load('postcss-value-parser', 'lib/index.js');
const { default: selectorParser } = await load('postcss-selector-parser', 'dist/index.js');
const { selectorSpecificity } = await load('@csstools/selector-specificity', 'dist/index.mjs');
const { resolveNestedSelector } = await load('@csstools/selector-resolve-nested', 'dist/index.mjs');

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
	ans.toString = node.toString();
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

function selectorTree(node) {
	const ans = {};
	for (const key of Object.keys(node)) {
		if (key === 'parent') continue;
		const value = node[key];
		if (key === 'nodes' && Array.isArray(value)) ans.nodes = value.map(selectorTree);
		else if (value !== undefined) ans[key] = value;
	}
	ans.toString = node.toString();
	return ans;
}

function parseSelector(text) {
	try {
		return { tree: selectorTree(selectorParser().astSync(text)) };
	} catch (err) {
		return { error: { name: err.name, message: String(err.message ?? err) } };
	}
}

function errorOf(err) {
	return { error: { name: err.name, message: String(err.message ?? err) } };
}

function specificities(text) {
	try {
		const root = selectorParser().astSync(text);
		const ans = [selectorSpecificity(root)];
		root.walk((node) => {
			ans.push(selectorSpecificity(node));
		});
		return { specificities: ans };
	} catch (err) {
		return errorOf(err);
	}
}

function resolveNested(request) {
	try {
		const [selector, parent] = request.resolveNested.map((text) => selectorParser().astSync(text));
		const options = { ignoreImplicitNesting: request.ignoreImplicitNesting };
		return { tree: selectorTree(resolveNestedSelector(selector, parent, options)) };
	} catch (err) {
		return errorOf(err);
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
		if ('parse' in request) response = parse(request);
		else if ('valueParse' in request) response = { nodes: valueParser(request.valueParse).nodes };
		else if ('selectorParse' in request) response = parseSelector(request.selectorParse);
		else if ('selectorSpecificity' in request) response = specificities(request.selectorSpecificity);
		else if ('resolveNested' in request) response = resolveNested(request);
		else response = await lint(request);
	} catch (err) {
		response = { failure: String(err && err.stack ? err.stack : err) };
	}
	process.stdout.write(JSON.stringify(response) + '\n');
}

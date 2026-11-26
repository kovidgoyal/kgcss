// License: BSD-3-Clause Copyright: 2026, Kovid Goyal <kovid at kovidgoyal.net>

// Dumps the data that the stylelint rules of kgcss.lint use, as JSON.
//
//     node dump_data.mjs INSTALL_DIR OUTPUT REFERENCE_NAME...
//
// INSTALL_DIR has stylelint in its node_modules (bench/.cache/stylelint).
// Writes {"versions": {package: version}, "lexer": {...}, "reference": {...}}:
//
// - lexer: from the css-tree lexer that stylelint builds by default
//   (utils/getLexer.mjs, css-tree with @csstools/css-syntax-patches-for-csstree):
//   "properties", the names of its properties; "dashedIdentProperties", those of
//   them whose value can be a <dashed-ident> (lexer.matchProperty(name, '--test')
//   matches, as in custom-property-no-missing-var-function); "functions", the
//   lowercased names of the functions in all its syntaxes (as in
//   function-no-unknown).
// - reference: the named exports of stylelint's lib/reference/*.mjs. Sets become
//   arrays, Maps become objects.
//
// Used by tools/generate_css_data.py.

import { readFileSync, readdirSync, writeFileSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { pathToFileURL } from 'node:url';

const [installDir, output, ...referenceNames] = process.argv.slice(2);
const modules = join(resolve(installDir), 'node_modules');
const load = (...parts) => import(pathToFileURL(join(modules, ...parts)).href);
const version = (name) => JSON.parse(readFileSync(join(modules, name, 'package.json'), 'utf8')).version;

const { default: getLexer } = await load('stylelint', 'lib', 'utils', 'getLexer.mjs');
const { definitionSyntax } = await load('css-tree', 'lib', 'index.js');
const lexer = getLexer({});

function functionNames(descriptor) {
	const names = [];
	if (descriptor?.syntax) {
		definitionSyntax.walk(descriptor.syntax, (node) => {
			if (node.type === 'Function') names.push(node.name.toLowerCase());
		});
	}
	return names;
}

const descriptors = [
	...Object.values(lexer.types),
	...Object.values(lexer.properties),
	...Object.values(lexer.atrules).flatMap((atrule) => [atrule.prelude, ...Object.values(atrule.descriptors ?? {})]),
];
const properties = Object.keys(lexer.properties);

function plain(value) {
	if (value instanceof Set) return [...value].map(plain);
	if (value instanceof Map) return Object.fromEntries([...value].map(([k, v]) => [k, plain(v)]));
	if (Array.isArray(value)) return value.map(plain);
	if (typeof value === 'string') return value;
	throw new Error(`Cannot dump ${typeof value}: ${String(value)}`);
}

const reference = {};
const referenceDir = join(modules, 'stylelint', 'lib', 'reference');
for (const file of readdirSync(referenceDir).filter((f) => f.endsWith('.mjs'))) {
	const exports = await import(pathToFileURL(join(referenceDir, file)).href);
	for (const name of referenceNames) {
		if (name in exports) reference[name] = plain(exports[name]);
	}
}
const missing = referenceNames.filter((name) => !(name in reference));
if (missing.length) throw new Error(`Not in stylelint's reference: ${missing.join(', ')}`);

const data = {
	versions: Object.fromEntries(
		['stylelint', 'css-tree', '@csstools/css-syntax-patches-for-csstree', 'mdn-data'].map((n) => [n, version(n)]),
	),
	lexer: {
		properties,
		dashedIdentProperties: properties.filter((name) => !lexer.matchProperty(name, '--test').error),
		functions: [...new Set(descriptors.flatMap(functionNames))],
	},
	reference,
};
writeFileSync(output, JSON.stringify(data));

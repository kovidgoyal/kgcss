// License: BSD-3-Clause Copyright: 2026, Kovid Goyal <kovid at kovidgoyal.net>

// Records the test cases of stylelint rule tests, without running them.
//
//     node record_tests.mjs OUTPUT TEST_FILE...
//
// Each test file is imported with its test harness replaced by a recorder:
// stylelint's testRule() (lib/testUtils/testRule.mjs) and the createTestRule()
// global of @stylistic/stylelint-plugin (from @morev/stylelint-testing-library).
// Tests written directly with node:test are not run: they test stylelint
// itself, such as its integration with postcss-import. Writes a JSON array with one entry for each testRule() call: the schema as
// given, with its file. Regular expressions and functions, which JSON cannot
// hold, become {"$regexp": [source, flags]} and {"$function": source}.
// Used by tools/extract_stylelint_tests.py.

import { writeFileSync } from 'node:fs';
import { registerHooks } from 'node:module';
import { pathToFileURL } from 'node:url';
import { resolve } from 'node:path';

const recorded = [];
let current = '';

globalThis.__kgcssRecord = (schema, harness) => recorded.push({ file: current, harness, schema });
globalThis.createTestRule = (factory) => (schema) => globalThis.__kgcssRecord({ ...factory, ...schema }, 'morev');
globalThis.createTestRuleConfig = () => () => {};

const STUBS = {
	'testUtils/testRule.mjs': 'export default (schema) => globalThis.__kgcssRecord(schema, "stylelint");',
	'testUtils/testRuleConfigs.mjs': 'export default () => {};',
};
const NODE_TEST = [
	'const noop = () => {};',
	'const suite = Object.assign(noop, { only: noop, skip: noop, todo: noop });',
	'export { suite as describe, suite as it, suite as test, suite as suite };',
	'export { noop as before, noop as after, noop as beforeEach, noop as afterEach, noop as mock };',
	'export default suite;',
].join('\n');

registerHooks({
	resolve(specifier, context, nextResolve) {
		if (specifier === 'node:test') {
			return { url: 'data:text/javascript,' + encodeURIComponent(NODE_TEST), shortCircuit: true };
		}
		for (const [suffix, source] of Object.entries(STUBS)) {
			if (specifier.endsWith('/' + suffix)) {
				return { url: 'data:text/javascript,' + encodeURIComponent(source), shortCircuit: true };
			}
		}
		return nextResolve(specifier, context);
	},
});

const [output, ...files] = process.argv.slice(2);

for (const file of files) {
	current = file;
	await import(pathToFileURL(resolve(file)).href);
}

function replacer(key, value) {
	if (value instanceof RegExp) return { $regexp: [value.source, value.flags] };
	if (typeof value === 'function') return { $function: String(value) };
	return value;
}

writeFileSync(output, JSON.stringify(recorded, replacer));

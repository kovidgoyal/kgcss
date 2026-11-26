// License: BSD-3-Clause Copyright: 2026, Kovid Goyal <kovid at kovidgoyal.net>

// The @csstools oracle: answers requests read from stdin, one JSON object per
// line, with one JSON object per line. Started by tests/csstools_oracle.py, to
// compare kgcss.linter._csstools and kgcss.linter._media_query with
// @csstools/css-tokenizer, @csstools/css-parser-algorithms and
// @csstools/media-query-list-parser.
//
//     node csstools_oracle.mjs INSTALL_DIR
//
// INSTALL_DIR has the packages in its node_modules (bench/.cache/stylelint).
// Every request has an "op" and the "css" text:
//
// tokenize: the tokens and the parse errors
// list: parseListOfComponentValues (or, with "comma": true,
//   parseCommaSeparatedListOfComponentValues): the node trees, and the visits of
//   a walk, which mutates the lists when "mutate" is true
// mutate: the tokens after mutateUnit(t, "unit") on every dimension token and
//   mutateIdent(t, "ident") on every ident token
// media: parse(), with "preserve" for preserveInvalidMediaQueries: the trees
//   and the visits of a walk of each query
// customMedia: parseCustomMedia(), with "preserve"
// ratio: [matchesRatio(), matchesRatioExactly()] of the component values
//
// Positions are in UTF-16 code units, numbers are given as their JavaScript
// strings, and a value that throws is {"error": message}.

import { createInterface } from 'node:readline';
import { join, resolve } from 'node:path';
import { pathToFileURL } from 'node:url';

const root = resolve(process.argv[2]);
const load = (name) => import(pathToFileURL(join(root, 'node_modules', name, 'dist', 'index.mjs')).href);
const T = await load('@csstools/css-tokenizer');
const P = await load('@csstools/css-parser-algorithms');
const M = await load('@csstools/media-query-list-parser');

function num(value) {
	return Object.is(value, -0) ? '-0' : String(value);
}

function dumpToken(token) {
	if (token === undefined) return null;
	const [type, raw, start, end, data] = token;
	let d = null;
	if (data !== undefined) {
		d = {};
		for (const key of Object.keys(data).sort()) {
			const value = data[key];
			if (value === undefined) continue;
			d[key] = typeof value === 'number' ? num(value) : value;
		}
	}
	return [type, raw, start, end, d];
}

function attempt(f) {
	try {
		return f();
	} catch (err) {
		return { error: String(err.message) };
	}
}

function dumpNode(node) {
	const ans = { type: node.type, str: attempt(() => node.toString()) };
	ans.tokens = node.tokens().map(dumpToken);
	ans.indices = P.sourceIndices(node);
	if (P.isFunctionNode(node)) {
		ans.name = node.getName();
		ans.nameToken = dumpToken(node.name);
		ans.endToken = dumpToken(node.endToken);
		ans.value = node.value.map(dumpNode);
	} else if (P.isSimpleBlockNode(node)) {
		ans.startToken = dumpToken(node.startToken);
		ans.endToken = dumpToken(node.endToken);
		ans.value = node.value.map(dumpNode);
	}
	const guards = [];
	for (const name of ['isFunctionNode', 'isSimpleBlockNode', 'isWhitespaceNode', 'isCommentNode', 'isWhiteSpaceOrCommentNode', 'isTokenNode']) {
		if (P[name](node)) guards.push(name);
	}
	ans.guards = guards;
	return ans;
}

function nodeName(node) {
	if (node === undefined || node === null) return null;
	if (Array.isArray(node)) return 'array';
	return node.type ?? 'list';
}

function describe(node) {
	return [nodeName(node), attempt(() => node.toString())];
}

function tokenOf(type, raw, value) {
	return [type, raw, -1, -1, value === undefined ? undefined : { value }];
}

// The mutations of a walk with "mutate": they exercise walkerIndexGenerator
function mutate(node, parent, index) {
	if (typeof index !== 'number' || !Array.isArray(parent.value)) return undefined;
	const list = parent.value;
	if (P.isWhitespaceNode(node)) {
		list.splice(index, 1);
	} else if (P.isTokenNode(node) && node.value[0] === T.TokenType.Ident) {
		const value = node.value[4].value;
		if (value === 'stop') return false;
		if (value === 'x') {
			list.splice(index, 1, new P.TokenNode(tokenOf(T.TokenType.Ident, 'y', 'y')), new P.TokenNode(tokenOf(T.TokenType.Ident, 'z', 'z')));
		}
		if (value === 'drop') list.splice(0, list.length);
	} else if (P.isTokenNode(node) && node.value[0] === T.TokenType.Delim) {
		if (node.value[4].value === '+') list.splice(index + 1, 0, new P.TokenNode(tokenOf(T.TokenType.Delim, '*', '*')));
		else if (node.value[4].value === '*' && index > 0) list.splice(index - 1, 1);
	} else if (P.isTokenNode(node) && node.value[0] === T.TokenType.Comma) {
		list.splice(index, 1, new P.WhitespaceNode([tokenOf(T.TokenType.Whitespace, ' ')]));
	}
	return undefined;
}

const MAX_VISITS = 5000;

function walker(visits, doMutate) {
	return ({ node, parent, state }, index) => {
		visits.push([...describe(node), nodeName(parent), index, state === undefined ? null : { ...state }]);
		if (visits.length > MAX_VISITS) return false;
		if (state !== undefined) {
			state.depth += 1;
			state.path += nodeName(node).slice(0, 3) + '/';
		}
		if (doMutate) return mutate(node, parent, index);
		return undefined;
	};
}

function list(request) {
	const tokens = T.tokenize({ css: request.css });
	const errors = [];
	const onParseError = (err) => errors.push({ message: err.message, start: err.sourceStart, end: err.sourceEnd });
	if (request.comma) {
		const lists = P.parseCommaSeparatedListOfComponentValues(tokens, { onParseError });
		const visits = [];
		const results = [];
		for (const nodes of lists) {
			results.push(P.walk(nodes, walker(visits, request.mutate), request.noState ? undefined : { depth: 0, path: '' }) ?? null);
		}
		return {
			lists: lists.map((nodes) => nodes.map(dumpNode)),
			stringify: attempt(() => P.stringify(lists)),
			visits,
			results,
			errors,
		};
	}
	const nodes = P.parseListOfComponentValues(tokens, { onParseError });
	const before = nodes.map(dumpNode);
	const visits = [];
	const result = P.walk(nodes, walker(visits, request.mutate), request.noState ? undefined : { depth: 0, path: '' });
	return {
		nodes: before,
		after: nodes.map(dumpNode),
		indices: P.sourceIndices(nodes),
		visits,
		result: result ?? null,
		errors,
	};
}

const MEDIA_GUARDS = [
	'isCustomMedia', 'isGeneralEnclosed', 'isMediaAnd', 'isMediaCondition', 'isMediaConditionList',
	'isMediaConditionListWithAnd', 'isMediaConditionListWithOr', 'isMediaFeature', 'isMediaFeatureBoolean',
	'isMediaFeatureName', 'isMediaFeaturePlain', 'isMediaFeatureRange', 'isMediaFeatureRangeNameValue',
	'isMediaFeatureRangeValueName', 'isMediaFeatureRangeValueNameValue', 'isMediaFeatureValue', 'isMediaInParens',
	'isMediaNot', 'isMediaOr', 'isMediaQuery', 'isMediaQueryInvalid', 'isMediaQueryWithType',
	'isMediaQueryWithoutType',
];

const TOKEN_LIST_FIELDS = ['modifier', 'mediaType', 'and', 'before', 'after', 'operator', 'valueOneOperator', 'valueTwoOperator'];
const NODE_FIELDS = ['media', 'leading', 'feature', 'name', 'value', 'valueOne', 'valueTwo'];

function dumpMedia(node) {
	if (node === undefined) return null;
	if (Array.isArray(node)) return node.map(dumpMedia);
	if (P.isFunctionNode(node) || P.isSimpleBlockNode(node) || P.isTokenNode(node) || P.isWhitespaceNode(node) || P.isCommentNode(node)) {
		return { componentValue: dumpNode(node) };
	}
	const ans = { type: node.type, str: attempt(() => node.toString()), tokens: node.tokens().map(dumpToken) };
	ans.guards = MEDIA_GUARDS.filter((name) => M[name](node));
	for (const key of TOKEN_LIST_FIELDS) {
		if (Array.isArray(node[key])) ans[key] = node[key].map(dumpToken);
	}
	if (node.colon !== undefined) ans.colon = dumpToken(node.colon);
	for (const key of NODE_FIELDS) {
		if (node[key] !== undefined && node[key] !== null && !(key === 'name' && Array.isArray(node[key]))) ans[key + 'Node'] = dumpMedia(node[key]);
	}
	if (node.list !== undefined) ans.list = node.list.map(dumpMedia);
	if (typeof node.getName === 'function') ans.getName = attempt(() => node.getName());
	if (typeof node.getNameToken === 'function') ans.getNameToken = attempt(() => dumpToken(node.getNameToken()));
	if (typeof node.getModifier === 'function') ans.getModifier = node.getModifier();
	if (typeof node.getMediaType === 'function') ans.getMediaType = node.getMediaType();
	if (typeof node.hasLeadingSpace === 'function') ans.hasLeadingSpace = attempt(() => node.hasLeadingSpace());
	if (typeof node.operatorKind === 'function') ans.operatorKind = node.operatorKind();
	if (typeof node.valueOneOperatorKind === 'function') {
		ans.valueOneOperatorKind = node.valueOneOperatorKind();
		ans.valueTwoOperatorKind = node.valueTwoOperatorKind();
	}
	return ans;
}

function media(request) {
	const queries = M.parse(request.css, { preserveInvalidMediaQueries: request.preserve });
	return {
		queries: queries.map((query) => {
			const visits = [];
			const result = query.walk(walker(visits, false), request.noState ? undefined : { depth: 0, path: '' });
			return { tree: dumpMedia(query), visits, result: result ?? null, negated: attempt(() => query.negateQuery().map((q) => q.toString())) };
		}),
	};
}

function customMedia(request) {
	const custom = M.parseCustomMedia(request.css, { preserveInvalidMediaQueries: request.preserve });
	if (custom === false) return { custom: false };
	return {
		custom: {
			str: attempt(() => custom.toString()),
			name: custom.getName(),
			nameToken: dumpToken(custom.getNameToken() ?? undefined),
			tokens: attempt(() => custom.tokens().map(dumpToken)),
			hasMediaQueryList: custom.hasMediaQueryList(),
			hasTrueKeyword: custom.hasTrueKeyword(),
			hasFalseKeyword: custom.hasFalseKeyword(),
			trueOrFalseKeyword: custom.trueOrFalseKeyword?.map(dumpToken) ?? null,
			queries: custom.mediaQueryList?.map(dumpMedia) ?? null,
		},
	};
}

function tokenize(request) {
	const errors = [];
	const tokens = T.tokenize({ css: request.css }, {
		onParseError: (err) => {
			errors.push({
				message: err.message,
				start: err.sourceStart,
				end: err.sourceEnd,
				token: err instanceof T.ParseErrorWithToken ? dumpToken(err.token) : null,
			});
		},
	});
	return { tokens: tokens.map(dumpToken), errors, stringify: T.stringify(tokens) };
}

function mutateTokens(request) {
	const tokens = T.tokenize({ css: request.css });
	try {
		for (const token of tokens) {
			if (T.isTokenDimension(token)) T.mutateUnit(token, request.unit);
			else if (T.isTokenIdent(token)) T.mutateIdent(token, request.ident);
		}
	} catch (err) {
		return { error: String(err.message) };
	}
	return { tokens: tokens.map(dumpToken) };
}

function ratio(request) {
	const nodes = P.parseListOfComponentValues(T.tokenize({ css: request.css }));
	return { ratio: [M.matchesRatio(nodes), M.matchesRatioExactly(nodes)] };
}

const OPS = { tokenize, list, mutate: mutateTokens, media, customMedia, ratio };

for await (const line of createInterface({ input: process.stdin, crlfDelay: Infinity })) {
	let response;
	try {
		const request = JSON.parse(line);
		response = OPS[request.op](request);
	} catch (err) {
		response = { failure: String(err && err.stack ? err.stack : err) };
	}
	process.stdout.write(JSON.stringify(response) + '\n');
}

// Compatible backport of colinhacks/zod@e06a510 plus an application input budget.
// Keep Zod 3's schema introspection API used by A2UI. Unknown source bytes fail
// installation; both ESM/CJS and the v3 entry point bundled in Zod 4 are covered.
import { createHash } from 'node:crypto';
import { readFileSync, writeFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

export const INPUT_LIMITS = { nodes: 10_000, depth: 64, characters: 2_097_152 };

// Serialized into the dependency, so it has no runtime import or global hook.
export function validationResourceIssue(data) {
    const stack = [[data, 0]];
    let nodes = 0;
    let characters = 0;
    const issue = () => ({ code: 'custom', path: [], message: 'Validation input exceeds the application resource budget' });
    while (stack.length) {
        const [value, depth] = stack.pop();
        if (++nodes > 10_000 || depth > 64) return issue();
        if (typeof value === 'string') characters += value.length;
        if (characters > 2_097_152) return issue();
        if (!value || typeof value !== 'object') continue;
        // Reject width before materializing an array/set/map of child entries.
        if (Array.isArray(value) || value instanceof Set || value instanceof Map) {
            const size = Array.isArray(value) ? value.length : value.size;
            const width = value instanceof Map ? size * 2 : size;
            if (nodes + stack.length + width > 10_000) return issue();
            for (const item of value) {
                if (value instanceof Map) {
                    stack.push([item[0], depth + 1], [item[1], depth + 1]);
                } else stack.push([item, depth + 1]);
            }
        } else {
            const keys = Object.keys(value);
            if (nodes + stack.length + keys.length > 10_000) return issue();
            for (const key of keys) {
                characters += key.length;
                if (characters > 2_097_152) return issue();
                stack.push([value[key], depth + 1]);
            }
        }
    }
    return null;
}

const originals = new Set([
    '9763a2938f0e7f983363ef4bf6530e7583e3f2d54f8a4a6684efad58febc6e8e', // 3.25.76 ESM
    '06487cc05ed45c5dd29789bf0aa75734d1ce06592a7f2665ba4ca90a68e8a3e1', // 3.25.76 CJS
    'd7214e1dacf9e6e70c9ca80247717b4be13b240510428fef132b7da3260d1a32', // 4.6.7 v3 ESM
    '7a2f06d88e280bdd198c60e0d292ed7f1e3993e877be4415de07e6abc7746dd1', // 4.6.7 v3 CJS
]);
const marker = '// LEGACY_ZOD_SECURITY_BACKPORT_V1\n';
const hash = (source) => createHash('sha256').update(source).digest('hex');

function replaceOnce(source, from, to) {
    if (source.split(from).length !== 2) throw new Error(`Unexpected Zod source at ${from.slice(0, 70)}`);
    return source.replace(from, to);
}

export function patchedSource(original, commonjs) {
    const invalid = commonjs ? 'parseUtil_js_1.INVALID' : 'INVALID';
    const error = commonjs ? 'ZodError_js_1.ZodError' : 'ZodError';
    let source = original;
    for (const [start, end, bound, replacement] of [
        ['class ZodArray extends ZodType', 'ZodArray.create =', 'exactLength', `status.dirty();\n                if (tooBig) return ${invalid};`],
        ['class ZodArray extends ZodType', 'ZodArray.create =', 'maxLength', `return ${invalid};`],
        ['class ZodSet extends ZodType', 'ZodSet.create =', 'maxSize', `return ${invalid};`],
    ]) {
        const a = source.indexOf(start);
        const b = source.indexOf(end, a);
        if (a < 0 || b < 0) throw new Error('Missing Zod container implementation');
        const section = source.slice(a, b);
        const offset = section.indexOf(`if (def.${bound} !== null)`);
        if (offset < 0) throw new Error(`Missing Zod bound: ${bound}`);
        const suffix = section.slice(offset);
        const position = suffix.indexOf('status.dirty();');
        if (position < 0) throw new Error('Missing Zod bound failure');
        const changed = section.slice(0, offset + position) + replacement + suffix.slice(position + 'status.dirty();'.length);
        source = source.slice(0, a) + changed + source.slice(b);
    }
    const check = `\n        const resourceIssue = validationResourceIssue(data);\n        if (resourceIssue) return { success: false, error: new ${error}([{ ...resourceIssue, path: params?.path || [] }]) };`;
    source = replaceOnce(source, '    safeParse(data, params) {', '    safeParse(data, params) {' + check);
    source = replaceOnce(source, '    async safeParseAsync(data, params) {', '    async safeParseAsync(data, params) {' + check);
    source = replaceOnce(source, '    "~validate"(data) {', '    "~validate"(data) {\n        const resourceIssue = validationResourceIssue(data);\n        if (resourceIssue) return { issues: [resourceIssue] };');
    return marker + (commonjs ? '"use strict";\n' : '') + validationResourceIssue.toString() + '\n' + source;
}

export function applyBackport(root, checkOnly = false) {
    const lock = JSON.parse(readFileSync(resolve(root, 'package-lock.json'), 'utf8'));
    let copies = 0;
    for (const [path, pkg] of Object.entries(lock.packages)) {
        if (!path.endsWith('node_modules/zod')) continue;
        if (!['3.25.76', '4.6.7'].includes(pkg.version)) throw new Error(`Review backport for ${path}@${pkg.version}`);
        const installed = JSON.parse(readFileSync(resolve(root, path, 'package.json'), 'utf8'));
        if (installed.version !== pkg.version) throw new Error(`Installed/locked version mismatch: ${path}`);
        for (const extension of ['js', 'cjs']) {
            const file = resolve(root, path, 'v3', `types.${extension}`);
            const source = readFileSync(file, 'utf8');
            const original = source;
            // Verify every byte, including the injected budget, on repeated runs.
            if (source.startsWith(marker)) {
                const pristine = readFileSync(resolve(dirname(fileURLToPath(import.meta.url)), 'legacy-zod-originals.json'), 'utf8');
                const record = JSON.parse(pristine)[hash(source)];
                if (!record || !originals.has(record.originalHash) || record.version !== pkg.version || record.format !== extension) throw new Error(`Backport bytes changed: ${file}`);
                continue;
            }
            if (!originals.has(hash(original))) throw new Error(`Unrecognized Zod source: ${file}`);
            if (checkOnly) throw new Error(`Missing security backport: ${file}`);
            const patched = patchedSource(original, extension === 'cjs');
            const records = JSON.parse(readFileSync(resolve(dirname(fileURLToPath(import.meta.url)), 'legacy-zod-originals.json'), 'utf8'));
            if (records[hash(patched)]?.originalHash !== hash(original)) throw new Error(`Unreviewed patch output: ${file}`);
            writeFileSync(file, patched);
        }
        copies++;
    }
    if (!copies) throw new Error('No Zod copies found');
    return copies;
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
    const count = applyBackport(resolve(dirname(fileURLToPath(import.meta.url)), '..'), process.argv.includes('--check'));
    console.log(`PASS verified Zod v3 security backport (${count} package copies, ESM and CJS)`);
}

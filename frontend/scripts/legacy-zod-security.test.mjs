import assert from 'node:assert/strict';
import { readFileSync, writeFileSync, mkdtempSync, mkdirSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { createRequire } from 'node:module';
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import test from 'node:test';
import { applyBackport, INPUT_LIMITS } from './patch-legacy-zod.mjs';

const root = process.env.ZOD_SECURITY_TEST_ROOT || resolve(import.meta.dirname, '..');
const lock = JSON.parse(readFileSync(resolve(root, 'package-lock.json')));
const require = createRequire(import.meta.url);

test('every installed copy is byte-verified and applying twice is harmless', () => {
    assert.equal(applyBackport(root, true), 6);
    assert.equal(applyBackport(root), 6);
});

for (const path of Object.keys(lock.packages).filter(p => p.endsWith('node_modules/zod'))) {
    for (const format of ['js', 'cjs']) {
        const file = resolve(root, path, 'v3', `index.${format}`);
        const { z } = format === 'cjs' ? require(file) : await import(pathToFileURL(file));
        test(`${path} ${format}: explicit bounds abort before element parsing, sync and async`, async () => {
            let visits = 0;
            const element = z.any().transform(() => ++visits);
            for (const schema of [z.array(element).max(2), z.array(element).length(2), z.set(element).max(2)]) {
                const data = schema instanceof z.ZodSet ? new Set([1, 2, 3]) : [1, 2, 3];
                for (const result of [schema.safeParse(data), await schema.safeParseAsync(data)]) {
                    assert.equal(result.success, false);
                    assert.equal(result.error.issues.length, 1);
                    assert.equal(result.error.issues[0].code, 'too_big');
                }
            }
            assert.equal(visits, 0);
            assert.deepEqual(z.array(z.number()).min(1).max(2).parse([1, 2]), [1, 2]);
            assert.equal(z.array(z.number()).min(1).safeParse([]).success, false);
        });
        test(`${path} ${format}: unbounded hostile inputs produce one issue without walking elements`, async () => {
            let visits = 0;
            const schema = z.array(z.any().transform(() => ++visits));
            const huge = new Array(1_000_000).fill(0);
            for (const result of [schema.safeParse(huge), await schema.safeParseAsync(huge)]) {
                assert.equal(result.success, false);
                assert.equal(result.error.issues.length, 1);
                assert.match(result.error.issues[0].message, /resource budget/);
            }
            assert.equal(visits, 0);
            assert.equal(schema['~standard'].validate(huge).issues.length, 1);
        });
        test(`${path} ${format}: nested, cyclic, string, set and map inputs are bounded`, () => {
            const cyclic = {}; cyclic.self = cyclic;
            let deep = 1;
            for (let n = 0; n < INPUT_LIMITS.depth + 1; n++) deep = { child: deep };
            const broad = Object.fromEntries(Array.from({ length: INPUT_LIMITS.nodes }, (_, i) => [i, i]));
            for (const data of [cyclic, deep, broad, 'x'.repeat(INPUT_LIMITS.characters + 1), new Set(Array.from({ length: INPUT_LIMITS.nodes }, (_, i) => i)), new Map(Array.from({ length: INPUT_LIMITS.nodes }, (_, i) => [i, i]))]) {
                const result = z.any().safeParse(data);
                assert.equal(result.success, false);
                assert.equal(result.error.issues.length, 1);
            }
            const normal = { trial: 1, messages: [{ role: 'user', content: 'Discuss this measurement.' }], state: { points: Array.from({ length: 1000 }, (_, i) => [i, i * 2]) } };
            assert.deepEqual(z.any().parse(normal), normal);
            assert.equal(z.any().safeParse('x'.repeat(INPUT_LIMITS.characters)).success, true);
        });
    }
}

test('unrecognized dependency source fails instead of silently skipping a copy', () => {
    const dir = mkdtempSync(resolve(tmpdir(), 'zod-backport-tamper-'));
    try {
        mkdirSync(resolve(dir, 'node_modules/zod/v3'), { recursive: true });
        writeFileSync(resolve(dir, 'package-lock.json'), JSON.stringify({ packages: { 'node_modules/zod': { version: '3.25.76' } } }));
        writeFileSync(resolve(dir, 'node_modules/zod/package.json'), JSON.stringify({ version: '3.25.76' }));
        writeFileSync(resolve(dir, 'node_modules/zod/v3/types.js'), 'unrecognized source');
        assert.throws(() => applyBackport(dir), /Unrecognized Zod source/);
    } finally { rmSync(dir, { recursive: true, force: true }); }
});

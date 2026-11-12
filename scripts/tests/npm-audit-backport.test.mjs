import assert from 'node:assert/strict';
import test from 'node:test';
import { spawnSync } from 'node:child_process';
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { remainingHighFindings } from '../audit-npm.mjs';

const url = 'https://github.com/advisories/GHSA-mrhx-c7xc-m3w5';
function report() {
    return { auditReportVersion: 2, vulnerabilities: {
        zod: { name: 'zod', severity: 'high', nodes: ['node_modules/@a2ui/react/node_modules/zod'], via: [{ url }] },
        renderer: { name: 'renderer', severity: 'high', nodes: ['node_modules/renderer'], via: ['zod'] },
        consumer: { name: 'consumer', severity: 'high', nodes: ['node_modules/consumer'], via: ['renderer'] },
    } };
}

test('the raw finding and dependent chains still fail without verified installed backport', () => {
    assert.equal(remainingHighFindings(report()).length, 3);
    assert.equal(remainingHighFindings(report(), true).length, 0);
});
test('another Zod advisory is never covered by this source backport', () => {
    const data = report(); data.vulnerabilities.zod.via.push({ url: 'https://github.com/advisories/another-cve' });
    assert.equal(remainingHighFindings(data, true).length, 3);
});
test('an unexpected or root Zod copy still blocks', () => {
    const data = report(); data.vulnerabilities.zod.nodes.push('node_modules/zod');
    assert.equal(remainingHighFindings(data, true).length, 3);
});
test('an independent vulnerability on a dependent library still blocks its consumers', () => {
    const data = report(); data.vulnerabilities.renderer.via.push({ url: 'https://github.com/advisories/new-cve' });
    assert.deepEqual(remainingHighFindings(data, true).map(v => v.name), ['renderer', 'consumer']);
});
test('unrelated high/critical findings remain blocking', () => {
    const data = report(); data.vulnerabilities.other = { name: 'other', severity: 'critical', nodes: ['node_modules/other'], via: [{ url: 'https://github.com/advisories/other' }] };
    assert.deepEqual(remainingHighFindings(data, true).map(v => v.name), ['other']);
});
test('scanner failures and changed report schemas fail closed', () => {
    for (const data of [{}, { ...report(), error: { code: 'EAUDIT' } }, { ...report(), auditReportVersion: 3 }]) assert.throws(() => remainingHighFindings(data, true));
    const data = report(); data.vulnerabilities.zod.severity = 'unknown';
    assert.throws(() => remainingHighFindings(data, true));
});

test('the real shell gate runs both Python scans and blocks scan failures, including on macOS Bash', () => {
    const directory = mkdtempSync(resolve(tmpdir(), 'audit-gate-errors-'));
    try {
        const trace = resolve(directory, 'scans.txt');
        writeFileSync(resolve(directory, 'npm'), '#!/bin/sh\nprintf \'%s\\n\' \'{"auditReportVersion":2,"vulnerabilities":{}}\'\n', { mode: 0o755 });
        writeFileSync(resolve(directory, 'uv'), '#!/bin/sh\nwhile [ "$#" -gt 0 ]; do\n if [ "$1" = --output-file ]; then shift; printf "example==1.0.0\\n" > "$1"; exit 0; fi\n shift\ndone\nexit 9\n', { mode: 0o755 });
        writeFileSync(resolve(directory, 'uvx'), '#!/bin/sh\nprintf "scan\\n" >> "$AUDIT_SCAN_TRACE"\nexit 9\n', { mode: 0o755 });
        const root = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
        const result = spawnSync('/bin/bash', ['scripts/security-check.sh'], { cwd: root, encoding: 'utf8', env: { ...process.env, PATH: `${directory}:${process.env.PATH}`, AUDIT_SCAN_TRACE: trace } });
        assert.equal(result.status, 1, result.stdout + result.stderr);
        assert.equal(readFileSync(trace, 'utf8'), 'scan\nscan\n');
        assert.match(result.stdout, /FAIL backend/);
        assert.match(result.stdout, /FAIL CLI/);
        assert.doesNotMatch(result.stdout, /PASS (backend|CLI)/);
    } finally { rmSync(directory, { recursive: true, force: true }); }
});

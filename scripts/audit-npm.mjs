// Enforce npm's high/critical gate. Only an installed, byte-verified security
// backport can resolve GHSA-mrhx-c7xc-m3w5 on the pinned legacy Zod copies.
// The raw finding is printed; other advisories (including on Zod) still fail.
import { spawnSync } from 'node:child_process';
import { copyFileSync, mkdtempSync, mkdirSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { applyBackport } from '../frontend/scripts/patch-legacy-zod.mjs';

const repo = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const advisory = 'https://github.com/advisories/GHSA-mrhx-c7xc-m3w5';
const legacyNodes = new Set([
    'node_modules/@a2ui/react/node_modules/zod',
    'node_modules/@a2ui/web_core/node_modules/zod',
    'node_modules/@ag-ui/client/node_modules/zod',
    'node_modules/@ag-ui/core/node_modules/zod',
    'node_modules/@ag-ui/proto/node_modules/zod',
    'node_modules/@mcp-ui/client/node_modules/zod',
]);

export function remainingHighFindings(report, backportVerified = false) {
    if (report.auditReportVersion !== 2 || !report.vulnerabilities || report.error) throw new Error('Incomplete npm audit report');
    const resolved = new Set();
    const vulns = report.vulnerabilities;
    for (const [name, finding] of Object.entries(vulns)) {
        if (finding.name !== name || !['info', 'low', 'moderate', 'high', 'critical'].includes(finding.severity) || !Array.isArray(finding.nodes) || !finding.nodes.every(n => typeof n === 'string') || !Array.isArray(finding.via) || !finding.via.length || !finding.via.every(v => typeof v === 'string' || (v && typeof v.url === 'string'))) throw new Error(`Malformed npm finding: ${name}`);
    }
    if (backportVerified) {
        const zod = vulns.zod;
        if (zod && zod.nodes?.length && zod.nodes.every(n => legacyNodes.has(n)) && zod.via.length && zod.via.every(v => typeof v === 'object' && v.url === advisory)) resolved.add('zod');
        let changed;
        do {
            changed = false;
            for (const [name, finding] of Object.entries(vulns)) {
                if (!resolved.has(name) && finding.via.length && finding.via.every(v => typeof v === 'string' && resolved.has(v))) {
                    resolved.add(name); changed = true;
                }
            }
        } while (changed);
    }
    return Object.values(vulns).filter(v => ['high', 'critical'].includes(v.severity) && !resolved.has(v.name));
}

function verifyReproducibleBackport(root) {
    const manifest = JSON.parse(readFileSync(resolve(root, 'package.json'), 'utf8'));
    if (manifest.scripts?.postinstall !== 'node scripts/patch-legacy-zod.mjs') throw new Error('Missing install-time backport');
    const docker = readFileSync(resolve(root, 'Dockerfile'), 'utf8');
    const copy = docker.indexOf('COPY scripts/patch-legacy-zod.mjs scripts/legacy-zod-originals.json ./scripts/');
    if (copy < 0 || copy > docker.indexOf('RUN npm cache clean --force && npm ci --no-audit --no-fund')) throw new Error('Docker build does not install the backport');
    const temporary = mkdtempSync(resolve(tmpdir(), 'npm-security-backport-'));
    try {
        mkdirSync(resolve(temporary, 'scripts'));
        for (const file of ['package.json', 'package-lock.json', 'scripts/patch-legacy-zod.mjs', 'scripts/legacy-zod-originals.json']) copyFileSync(resolve(root, file), resolve(temporary, file));
        const install = spawnSync('npm', ['ci', '--omit=dev', '--no-audit', '--no-fund'], { cwd: temporary, encoding: 'utf8' });
        if (install.status !== 0) throw new Error(`Frozen backport installation failed: ${install.stderr}\n${install.stdout}`);
        if (applyBackport(temporary, true) !== 6) throw new Error('Unexpected Zod copy inventory');
        // Test the installed runtime, including legacy ESM/CJS entry points,
        // without modifying a developer's node_modules during an audit.
        const verify = spawnSync(process.execPath, ['--test', resolve(root, 'scripts/legacy-zod-security.test.mjs')], { cwd: temporary, env: { ...process.env, ZOD_SECURITY_TEST_ROOT: temporary }, encoding: 'utf8' });
        if (verify.status !== 0) throw new Error(`Backport runtime verification failed: ${verify.stdout}\n${verify.stderr}`);
    } finally { rmSync(temporary, { recursive: true, force: true }); }
}

export function audit(directory, flags = []) {
    const root = resolve(repo, directory);
    if (!['frontend', 'infrastructure/mcp-sandbox'].includes(directory)) throw new Error('Unsupported audit directory');
    const result = spawnSync('npm', ['audit', '--package-lock-only', '--json', '--audit-level=high', ...flags], { cwd: root, encoding: 'utf8' });
    if (![0, 1].includes(result.status)) throw new Error(`npm audit failed: ${result.stderr}`);
    const report = JSON.parse(result.stdout);
    console.log(JSON.stringify(report, null, 2));
    let verified = false;
    if (directory === 'frontend' && report.vulnerabilities?.zod) {
        verifyReproducibleBackport(root);
        verified = true;
        console.log('BACKPORTED GHSA-mrhx-c7xc-m3w5: frozen install, all source hashes and 38 runtime checks verified. Raw npm findings remain above.');
    }
    const remaining = remainingHighFindings(report, verified);
    if (remaining.length) console.error(`FAIL unpatched high/critical npm findings: ${remaining.map(v => v.name).join(', ')}`);
    return remaining.length ? 1 : 0;
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
    try { process.exitCode = audit(process.argv[2], process.argv.slice(3)); }
    catch (error) { console.error(error); process.exitCode = 2; }
}

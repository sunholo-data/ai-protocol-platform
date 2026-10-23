// npm's advisory ranges can omit prereleases. Check the A2UI XSS fix explicitly.
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

export function atLeastStable(version, floor) {
  if (!/^\d+\.\d+\.\d+$/.test(version)) return false;
  const actual = version.split('.').map(Number);
  const minimum = floor.split('.').map(Number);
  for (let i = 0; i < 3; i++) {
    if (actual[i] !== minimum[i]) return actual[i] > minimum[i];
  }
  return true;
}

export function checkA2uiFloor(lock) {
  if (!lock.packages || typeof lock.packages !== 'object') throw new Error('Invalid npm lockfile');
  const copies = Object.entries(lock.packages).filter(([path]) => path.endsWith('node_modules/@a2ui/web_core'));
  if (!copies.length) throw new Error('Missing @a2ui/web_core from frontend lockfile');
  for (const [path, pkg] of copies) {
    if (!atLeastStable(pkg.version, '0.10.2')) {
      throw new Error(`${path}@${pkg.version}: GHSA-72qq-p3r5-f7wq requires stable @a2ui/web_core >=0.10.2`);
    }
  }
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  checkA2uiFloor(JSON.parse(readFileSync(new URL('../frontend/package-lock.json', import.meta.url), 'utf8')));
  console.log('PASS A2UI security floor (including prerelease and nested copies)');
}

import assert from 'node:assert/strict';
import { test } from 'node:test';
import { checkA2uiFloor } from '../check-security-floors.mjs';

const lock = (version, nested) => ({ packages: {
  'node_modules/@a2ui/web_core': { version },
  ...(nested ? { 'node_modules/renderer/node_modules/@a2ui/web_core': { version: nested } } : {}),
} });

test('rejects the affected prerelease that npm audit omitted', () => {
  assert.throws(() => checkA2uiFloor(lock('0.9.1-alpha.0')), /GHSA-72qq/);
});
test('rejects vulnerable nested copies even when the root is patched', () => {
  assert.throws(() => checkA2uiFloor(lock('0.10.7', '0.10.1')), /GHSA-72qq/);
});
test('accepts patched stable versions and rejects missing inventory', () => {
  checkA2uiFloor(lock('0.10.2'));
  checkA2uiFloor(lock('0.14.0'));
  assert.throws(() => checkA2uiFloor({ packages: {} }), /Missing/);
});

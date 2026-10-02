import test from 'node:test';
import assert from 'node:assert/strict';
import { readdirSync, readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join, relative } from 'node:path';

const webRoot = join(dirname(fileURLToPath(import.meta.url)), '..');
const checkedExtensions = new Set(['.js', '.py', '.html', '.webmanifest', '.md']);

function sourceFiles(directory) {
  return readdirSync(directory, { withFileTypes: true }).flatMap(entry => {
    const path = join(directory, entry.name);
    if (entry.isDirectory()) return entry.name === '__pycache__' ? [] : sourceFiles(path);
    return [...checkedExtensions].some(extension => path.endsWith(extension)) ? [path] : [];
  });
}

test('web UI, bridge fixtures, documentation, and tests contain no Chinese text', () => {
  for (const path of sourceFiles(webRoot)) {
    const content = readFileSync(path, 'utf8');
    assert.doesNotMatch(content, /[\u3400-\u9fff]/u, relative(webRoot, path));
  }
});

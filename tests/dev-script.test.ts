import { spawnSync } from 'node:child_process';
import { chmodSync, mkdtempSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..');

/** A stand-in interpreter: new enough to be chosen, without the backend installed. */
function stubPython(version: string): string {
  const directory = mkdtempSync(join(tmpdir(), 'kit-dev-'));
  const shim = join(directory, 'python');
  writeFileSync(
    shim,
    [
      '#!/bin/sh',
      // The launcher probes the version first, then tries to import the backend.
      `case "$2" in *version_info*) echo "${version}"; exit 0;; esac`,
      'echo "ModuleNotFoundError: No module named \'fastapi\'" >&2',
      'exit 1',
      '',
    ].join('\n'),
  );
  chmodSync(shim, 0o755);
  return shim;
}

function runDevScript(env: Record<string, string>) {
  return spawnSync(process.execPath, [resolve(ROOT, 'scripts/dev.mjs')], {
    cwd: ROOT,
    encoding: 'utf8',
    timeout: 30_000,
    env: { ...process.env, ...env },
  });
}

describe('npm run dev', () => {
  it('starts nothing when the backend dependencies are missing', () => {
    const result = runDevScript({ KIT_PYTHON: stubPython('3.12') });

    expect(result.stderr).toContain('Cannot start Kit.');
    expect(result.stderr).toContain('dependencies are not installed');
    expect(result.status).toBe(1);

    // The regression this guards: the launcher reported it could not start, then
    // started both halves anyway and left an orphaned frontend holding its port.
    expect(result.stdout).not.toContain('[web]');
    expect(result.stdout).not.toContain('[api]');
    expect(result.stdout).not.toContain('api ready');
  });

  it('stops when no interpreter is new enough', () => {
    const result = runDevScript({ KIT_PYTHON: stubPython('3.11') });

    expect(result.status).toBe(1);
    expect(result.stderr).toContain('3.12 or newer');
    expect(result.stdout).not.toContain('[web]');
  });
});

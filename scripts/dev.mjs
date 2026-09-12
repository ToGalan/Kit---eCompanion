/**
 * Start Kit: the FastAPI backend and the Vite frontend, together.
 *
 * They are two processes but one app. Starting the frontend alone leaves every
 * /api call proxying to a port nothing is listening on, and Vite answers a refused
 * upstream connection with a 500 — so a missing backend looks like a broken API
 * rather than an absent one. Running both from one command removes that failure.
 *
 * Ports come from KIT_WEB_PORT and KIT_API_PORT, which vite.config.ts reads too, so
 * the proxy target and the server it points at cannot drift apart.
 *
 * Arguments are forwarded to Vite: `npm run dev -- --host 0.0.0.0`.
 */

import { spawn, spawnSync } from 'node:child_process';
import { existsSync } from 'node:fs';
import { createRequire } from 'node:module';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const WEB_PORT = Number(process.env.KIT_WEB_PORT || 5175);
const API_PORT = Number(process.env.KIT_API_PORT || 8002);
const MINIMUM_PYTHON = [3, 12];

const paint = (code, text) => (process.stdout.isTTY ? `[${code}m${text}[0m` : text);
const LABELS = { api: paint('36', '[api]'), web: paint('35', '[web]') };

function note(message) {
  console.log(`${paint('2', '[dev]')} ${message}`);
}

function fail(message, ...detail) {
  console.error(`\n${paint('31', 'Cannot start Kit.')} ${message}`);
  for (const line of detail) console.error(`  ${line}`);
  console.error('');
  // Anything already started has to come down with us, or a failed launch leaves an
  // orphaned backend holding its port and the next attempt fails for a new reason.
  // SIGTERM rather than SIGKILL: uvicorn --reload runs a worker child, and only the
  // parent's own handler takes that worker down with it.
  shuttingDown = true;
  for (const child of children.values()) child.kill('SIGTERM');
  setTimeout(() => process.exit(1), 500).unref();
}

/** Interpreters worth trying, best first: an explicit choice, then the project venv. */
function pythonCandidates() {
  if (process.env.KIT_PYTHON) return [process.env.KIT_PYTHON];
  const venv = [join(ROOT, '.venv', 'bin', 'python'), join(ROOT, '.venv', 'Scripts', 'python.exe')];
  return [...venv.filter(existsSync), 'python3.13', 'python3.12', 'python3', 'python'];
}

function versionOf(command) {
  const probe = spawnSync(command, ['-c', 'import sys; print("%d.%d" % sys.version_info[:2])'], {
    encoding: 'utf8',
  });
  if (probe.error || probe.status !== 0) return null;
  const parts = String(probe.stdout).trim().split('.').map(Number);
  return parts.length === 2 && parts.every(Number.isInteger) ? parts : null;
}

const atLeast = ([major, minor], [wantMajor, wantMinor]) =>
  major > wantMajor || (major === wantMajor && minor >= wantMinor);

/**
 * Find an interpreter new enough to import the backend.
 *
 * kit/vault.py uses f-strings containing backslashes, which is a SyntaxError before
 * 3.12: on an older interpreter the server dies at import with a traceback that says
 * nothing about versions. Checking here turns that into one readable line.
 */
function resolvePython() {
  const wanted = MINIMUM_PYTHON.join('.');
  const seen = [];
  for (const candidate of pythonCandidates()) {
    const version = versionOf(candidate);
    if (!version) continue;
    if (atLeast(version, MINIMUM_PYTHON)) return { command: candidate, version: version.join('.') };
    seen.push(`${candidate} is ${version.join('.')}`);
  }
  fail(
    `no Python ${wanted} or newer was found.`,
    ...(seen.length ? seen : ['no python interpreter was found on PATH']),
    `Install Python ${wanted}+, or point KIT_PYTHON at one:`,
    '  KIT_PYTHON=/usr/bin/python3.12 npm run dev',
  );
}

function checkBackendImports(python) {
  const probe = spawnSync(python, ['-c', 'import fastapi, uvicorn'], { cwd: ROOT, encoding: 'utf8' });
  if (probe.status === 0) return;
  fail(
    'the backend dependencies are not installed.',
    'Install them into the interpreter you are using:',
    `  ${python} -m pip install -e . uvicorn`,
    String(probe.stderr || '').trim().split('\n').pop() || '',
  );
}

/** Vite's package exports do not expose its bin, so resolve the file rather than the subpath. */
function resolveVite() {
  const local = join(ROOT, 'node_modules', 'vite', 'bin', 'vite.js');
  if (existsSync(local)) return local;
  try {
    // Hoisted or linked installs: find the package root via a subpath it does export.
    const require = createRequire(import.meta.url);
    return join(dirname(require.resolve('vite/package.json')), 'bin', 'vite.js');
  } catch {
    fail('vite is not installed.', 'Run: npm install');
  }
}

const children = new Map();
let shuttingDown = false;

function start(name, command, args) {
  const child = spawn(command, args, {
    cwd: ROOT,
    env: { ...process.env, PYTHONPATH: ROOT, KIT_WEB_PORT: String(WEB_PORT), KIT_API_PORT: String(API_PORT) },
    stdio: ['ignore', 'pipe', 'pipe'],
  });

  const relay = (stream) => {
    let buffered = '';
    stream.setEncoding('utf8');
    stream.on('data', (chunk) => {
      buffered += chunk;
      const lines = buffered.split('\n');
      buffered = lines.pop() ?? '';
      for (const line of lines) console.log(`${LABELS[name]} ${line}`);
    });
  };
  relay(child.stdout);
  relay(child.stderr);

  child.on('error', (error) => fail(`could not start the ${name} process.`, String(error.message)));
  // One process is the app. If either half goes down the other is useless, and a
  // half-running dev environment is the thing this script exists to prevent.
  child.on('exit', (code, signal) => {
    children.delete(name);
    if (shuttingDown) return;
    console.log(`${LABELS[name]} exited (${signal || `code ${code}`}) — stopping the other half.`);
    shutdown(code ?? 1);
  });

  children.set(name, child);
  return child;
}

function shutdown(code) {
  if (shuttingDown) return;
  shuttingDown = true;
  for (const child of children.values()) child.kill('SIGTERM');
  const deadline = setTimeout(() => {
    for (const child of children.values()) child.kill('SIGKILL');
    process.exit(code);
  }, 5000);
  deadline.unref();
  const settle = setInterval(() => {
    if (children.size === 0) {
      clearInterval(settle);
      process.exit(code);
    }
  }, 100);
  settle.unref();
}

async function waitForApi() {
  const deadline = Date.now() + 30_000;
  while (Date.now() < deadline) {
    if (shuttingDown) return false;
    try {
      const response = await fetch(`http://127.0.0.1:${API_PORT}/api/health`);
      if (response.ok) return true;
    } catch {
      // Not listening yet. Uvicorn takes a moment, and the frontend starts in parallel.
    }
    await new Promise((done) => setTimeout(done, 250));
  }
  return false;
}

const python = resolvePython();
checkBackendImports(python.command);
note(`python ${python.version} (${python.command})`);

start('api', python.command, [
  '-m',
  'uvicorn',
  'server:app',
  '--reload',
  '--host',
  process.env.KIT_API_HOST || '127.0.0.1',
  '--port',
  String(API_PORT),
]);

start('web', process.execPath, [resolveVite(), ...process.argv.slice(2)]);

for (const signal of ['SIGINT', 'SIGTERM']) process.on(signal, () => shutdown(0));

if (await waitForApi()) {
  note(`api ready on http://127.0.0.1:${API_PORT} — the frontend proxies /api there`);
} else if (!shuttingDown) {
  note(`api has not answered on port ${API_PORT} yet; /api calls will fail until it does`);
}

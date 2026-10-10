import assert from 'node:assert/strict';
import { mkdtemp, mkdir, writeFile, rm, symlink } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import test from 'node:test';
import { auditPanelArtifacts } from './validate-panel-artifacts.mjs';

const mount = 'ReactDOM.createRoot(document.getElementById("root")).render(App);';
const html = '<script type="module" src="/assets/index.js"></script><link rel="stylesheet" href="/assets/style.css"><div id="root"></div>';

async function fixture(t, files = {}) {
  const root = await mkdtemp(path.join(os.tmpdir(), 'panel-artifact-test-'));
  t.after(() => rm(root, { recursive: true, force: true }));
  for (const [file, text] of Object.entries({ 'index.html': html, 'assets/index.js': mount, 'assets/style.css': '', ...files })) {
    await mkdir(path.dirname(path.join(root, file)), { recursive: true });
    await writeFile(path.join(root, file), text);
  }
  return root;
}

test('a clean graph has exactly one panel mount and resolves static/reexport/dynamic imports', async t => {
  const root = await fixture(t, {
    'assets/index.js': `import './shared.js'; export * from './reexport.js'; import('./lazy.js'); ${mount}`,
    'assets/shared.js': 'export const shared = 1;',
    'assets/reexport.js': 'export { shared } from "./shared.js";',
    'assets/lazy.js': 'export default 1;',
  });
  const result = await auditPanelArtifacts(root);
  assert.equal(result.passed, true);
  assert.equal(result.mounts.length, 1);
  assert.deepEqual(result.modules, ['assets/index.js', 'assets/lazy.js', 'assets/reexport.js', 'assets/shared.js']);
});

test('reachable historic entries mounting the same DOM container fail', async t => {
  const root = await fixture(t, {
    'assets/index.js': `import './overlay.js'; ${mount}`,
    'assets/overlay.js': 'export { old } from "./old-index.js";',
    'assets/old-index.js': `${mount} export const old = 1;`,
  });
  const result = await auditPanelArtifacts(root);
  assert.equal(result.passed, false);
  assert.equal(result.mounts.length, 2);
  assert.ok(result.errors.some(error => error.code === 'panel_root_mount_count' && error.actual === 2));
});

test('missing static and dynamic chunks are reported without hiding either defect', async t => {
  const root = await fixture(t, { 'assets/index.js': `import './missing-static.js'; import('./missing-lazy.js'); ${mount}` });
  const result = await auditPanelArtifacts(root);
  assert.equal(result.passed, false);
  assert.deepEqual(result.errors.filter(error => error.code === 'missing_asset').map(error => error.asset),
    ['assets/missing-static.js', 'assets/missing-lazy.js']);
});

test('missing HTML stylesheet blocks the artifact', async t => {
  const root = await fixture(t, { 'index.html': html.replace('style.css', 'missing.css') });
  const result = await auditPanelArtifacts(root);
  assert.equal(result.passed, false);
  assert.ok(result.errors.some(error => error.code === 'missing_asset' && error.asset === 'assets/missing.css'));
});

test('malformed reachable JavaScript blocks the artifact', async t => {
  const root = await fixture(t, { 'assets/index.js': 'export const = invalid' });
  const result = await auditPanelArtifacts(root);
  assert.equal(result.passed, false);
  assert.ok(result.errors.some(error => error.code === 'malformed_javascript'));
});

test('comments, string literals and commented HTML cannot fabricate imports or mounts', async t => {
  const root = await fixture(t, {
    'index.html': `<!-- <script type="module" src="/absent.js"></script> -->${html}`,
    'assets/index.js': `// import './absent.js'; ${mount}\nconst text = ${JSON.stringify(`import './also-absent.js'; ${mount}`)};\n${mount}`,
  });
  const result = await auditPanelArtifacts(root);
  assert.equal(result.passed, true);
  assert.equal(result.mounts.length, 1);
});

test('unreachable historic assets and the separate anamnese app are ignored', async t => {
  const root = await fixture(t, {
    'assets/old-index.js': `import './missing-legacy.js'; ${mount}`,
    'anamnese/leoft/index.html': '<script type="module" src="./anamnese.js"></script>',
    'anamnese/leoft/anamnese.js': mount,
  });
  const result = await auditPanelArtifacts(root);
  assert.equal(result.passed, true);
  assert.deepEqual(result.modules, ['assets/index.js']);
});

test('modulepreload requires an asset but does not evaluate a historic root', async t => {
  const root = await fixture(t, {
    'index.html': `${html}<link rel="modulepreload" href="/assets/old-index.js">`,
    'assets/old-index.js': mount,
  });
  assert.equal((await auditPanelArtifacts(root)).passed, true);
  await rm(path.join(root, 'assets/old-index.js'));
  assert.equal((await auditPanelArtifacts(root)).passed, false);
});

test('aliased React imports and querySelector identify the actual root call', async t => {
  const root = await fixture(t, {
    'assets/index.js': 'import { createRoot as mount } from "./react.js"; mount(document.querySelector("#root")).render(App);',
    'assets/react.js': 'export const createRoot = () => {};',
  });
  assert.equal((await auditPanelArtifacts(root)).passed, true);
});

test('zero or multiple HTML entry modules fail', async t => {
  for (const entryHtml of ['<div id="root"></div>', `${html}<script type="module" src="/assets/second.js"></script>`]) {
    const root = await fixture(t, { 'index.html': entryHtml, 'assets/second.js': 'export default 1;' });
    const result = await auditPanelArtifacts(root);
    assert.equal(result.passed, false);
    assert.ok(result.errors.some(error => error.code === 'module_entry_count'));
  }
});

test('external literal imports fail and runtime-selected imports remain explicit', async t => {
  const root = await fixture(t, {
    'assets/index.js': `import 'https://example.invalid/foreign.js'; import(variable); ${mount}`,
  });
  const result = await auditPanelArtifacts(root);
  assert.equal(result.passed, false);
  assert.ok(result.errors.some(error => error.code === 'external_reference'));
  assert.equal(result.runtimeDynamicImports.length, 1);
  assert.ok(!JSON.stringify(result).includes('example.invalid'));
});

test('generic vendor dynamic loaders do not create fictitious build dependencies', async t => {
  const root = await fixture(t, {
    'assets/index.js': `import './router.js'; ${mount}`,
    'assets/router.js': 'export async function load(route) { return import(route.module); }',
  });
  const result = await auditPanelArtifacts(root);
  assert.equal(result.passed, true);
  assert.deepEqual(result.runtimeDynamicImports, [{ file: 'assets/router.js', line: 1 }]);
});

test('symlink outside the artifact fails without disclosing host paths', async t => {
  const root = await fixture(t, { 'assets/index.js': `import './outside.js'; ${mount}` });
  await symlink(fileURLToPath(import.meta.url), path.join(root, 'assets/outside.js'));
  const result = await auditPanelArtifacts(root);
  assert.equal(result.passed, false);
  assert.ok(result.errors.some(error => error.code === 'reference_outside_artifact'));
  assert.ok(!JSON.stringify(result).includes(root));
});

test('CLI exits nonzero on a defective graph and returns structured safe diagnostics', async t => {
  const root = await fixture(t, { 'assets/index.js': `import './absent.js'; ${mount}` });
  const cli = spawnSync(process.execPath, [fileURLToPath(new URL('./validate-panel-artifacts.mjs', import.meta.url)), '--root', root], { encoding: 'utf8' });
  assert.equal(cli.status, 1);
  assert.equal(JSON.parse(cli.stdout).passed, false);
  assert.ok(!cli.stdout.includes(root));
});

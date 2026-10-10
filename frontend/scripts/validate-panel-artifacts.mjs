import { readFile, realpath, stat } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { parse } from 'acorn';

const ARTIFACT_ORIGIN = 'https://panel-artifact.invalid';

function walk(node, visit) {
  if (!node || typeof node !== 'object') return;
  if (typeof node.type === 'string') visit(node);
  for (const value of Object.values(node)) {
    if (Array.isArray(value)) value.forEach(child => walk(child, visit));
    else if (value && typeof value === 'object') walk(value, visit);
  }
}

function propertyName(node) {
  if (node?.type !== 'MemberExpression') return null;
  return node.computed ? node.property.value : node.property.name;
}

function unsequence(node) {
  return node?.type === 'SequenceExpression' ? node.expressions.at(-1) : node;
}

function isRootElement(node) {
  if (node?.type !== 'CallExpression') return false;
  const target = node.callee?.object;
  const isDocument = target?.type === 'Identifier' && target.name === 'document'
    || target?.type === 'MemberExpression' && target.object?.name === 'window'
      && propertyName(target) === 'document';
  if (!isDocument) return false;
  const method = propertyName(node.callee);
  const argument = node.arguments[0]?.value;
  return method === 'getElementById' && argument === 'root'
    || method === 'querySelector' && argument === '#root';
}

function attributes(tag) {
  const result = {};
  for (const match of tag.matchAll(/([^\s=<>]+)\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+))/g)) {
    result[match[1].toLowerCase()] = (match[2] ?? match[3] ?? match[4]).replaceAll('&amp;', '&');
  }
  return result;
}

/** Audit only the panel graph rooted in index.html. No artifact is changed. */
export async function auditPanelArtifacts(rootDirectory) {
  const root = path.resolve(rootDirectory);
  const errors = [];
  const mounts = [];
  const runtimeDynamicImports = [];
  const visited = new Set();
  const report = (code, file, details = {}) => errors.push({ code, file, ...details });
  let canonicalRoot;
  try { canonicalRoot = await realpath(root); }
  catch { return { passed: false, entries: [], modules: [], mounts, errors: [{ code: 'missing_artifact_root', file: '.' }] }; }

  async function resolveAsset(specifier, from, isImport = false) {
    const local = specifier.startsWith('/') && !specifier.startsWith('//')
      || specifier.startsWith('./') || specifier.startsWith('../');
    if (isImport && !local || /^(?:[a-z][a-z\d+.-]*:|\/\/)/i.test(specifier)) {
      report('external_reference', from);
      return null;
    }
    let relative;
    try {
      const url = new URL(specifier, `${ARTIFACT_ORIGIN}/${from}`);
      relative = decodeURIComponent(url.pathname).replace(/^\/+/, '');
      const absolute = path.resolve(root, relative);
      const canonical = await realpath(absolute);
      if (canonical !== canonicalRoot && !canonical.startsWith(`${canonicalRoot}${path.sep}`)) {
        report('reference_outside_artifact', from);
        return null;
      }
      if (!(await stat(canonical)).isFile()) throw new Error('Not a file');
      return relative;
    } catch {
      // Emit artifact-relative paths, never host paths or asset contents.
      report('missing_asset', from, relative ? { asset: relative } : {});
      return null;
    }
  }

  async function inspectModule(file) {
    if (visited.has(file)) return;
    visited.add(file);
    let ast;
    try {
      ast = parse(await readFile(path.join(root, file), 'utf8'), {
        ecmaVersion: 'latest', sourceType: 'module', locations: true,
      });
    } catch (error) {
      report('malformed_javascript', file, error.loc ? { line: error.loc.line } : {});
      return;
    }
    const createRootAliases = new Set();
    const imports = [];
    for (const node of ast.body) {
      if (node.type === 'ImportDeclaration') {
        for (const specifier of node.specifiers) {
          if (specifier.type === 'ImportSpecifier' && (specifier.imported.name ?? specifier.imported.value) === 'createRoot') {
            createRootAliases.add(specifier.local.name);
          }
        }
      }
    }
    walk(ast, node => {
      if (node.type === 'ImportDeclaration' || node.type === 'ExportNamedDeclaration' || node.type === 'ExportAllDeclaration') {
        if (node.source) imports.push(node.source.value);
      } else if (node.type === 'ImportExpression') {
        if (node.source.type === 'Literal' && typeof node.source.value === 'string') imports.push(node.source.value);
        // React Router includes a generic import(route.module) loader even in
        // a BrowserRouter bundle. Only literal imports define this build graph.
        else runtimeDynamicImports.push({ file, line: node.loc.start.line });
      }
      if (node.type === 'CallExpression') {
        const callee = unsequence(node.callee);
        const isCreateRoot = propertyName(callee) === 'createRoot'
          || callee?.type === 'Identifier' && createRootAliases.has(callee.name);
        if (isCreateRoot && isRootElement(node.arguments[0])) mounts.push({ file, line: node.loc.start.line });
      }
    });
    for (const specifier of imports) {
      const dependency = await resolveAsset(specifier, file, true);
      if (dependency && /\.(?:m?js)$/i.test(dependency)) await inspectModule(dependency);
    }
  }

  let html;
  try { html = await readFile(path.join(root, 'index.html'), 'utf8'); }
  catch { return { passed: false, entries: [], modules: [], mounts, errors: [{ code: 'missing_html', file: 'index.html' }] }; }
  const entries = [];
  const tags = html.replace(/<!--[\s\S]*?-->/g, '').matchAll(/<(script|link)\b[^>]*>/gi);
  for (const match of tags) {
    const tag = match[1].toLowerCase();
    const attrs = attributes(match[0]);
    const specifier = tag === 'script' ? attrs.src : attrs.href;
    if (tag === 'script' && attrs.type === 'module' && !specifier) report('inline_module_entry', 'index.html');
    if (!specifier) continue;
    const asset = await resolveAsset(specifier, 'index.html');
    if (tag === 'script' && attrs.type === 'module' && asset) entries.push(asset);
    // Preloading fetches a module; only an actual import makes it reachable.
  }
  if (entries.length !== 1) report('module_entry_count', 'index.html', { actual: entries.length, expected: 1 });
  for (const entry of entries) await inspectModule(entry);
  if (mounts.length !== 1) report('panel_root_mount_count', 'index.html', { actual: mounts.length, expected: 1 });
  return { passed: errors.length === 0, entries, modules: [...visited].sort(), mounts, runtimeDynamicImports, errors };
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const args = process.argv.slice(2);
  if (args.length !== 2 || args[0] !== '--root') {
    process.stderr.write('Usage: node scripts/validate-panel-artifacts.mjs --root <panel-build-directory>\n');
    process.exitCode = 1;
  } else {
    const result = await auditPanelArtifacts(args[1]);
    process.stdout.write(`${JSON.stringify(result, null, 2)}\n`);
    process.exitCode = result.passed ? 0 : 1;
  }
}

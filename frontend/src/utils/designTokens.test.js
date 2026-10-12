import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { designTokens } from './designTokens.js';

const css = readFileSync(new URL('../styles/design-tokens.css', import.meta.url), 'utf8');
const tokens = Object.fromEntries([...css.matchAll(/--fm-([\w-]+):\s*([^;]+);/g)].map((match) => [match[1], match[2].trim()]));
const luminance = (hex) => {
  const [r, g, b] = hex.slice(1).match(/../g).map((channel) => parseInt(channel, 16) / 255).map((value) => value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4);
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
};
const contrast = (foreground, background) => {
  const [lighter, darker] = [luminance(foreground), luminance(background)].sort((a, b) => b - a);
  return (lighter + 0.05) / (darker + 0.05);
};
const assertContrast = (foreground, background, minimum) => {
  const ratio = contrast(tokens[foreground], tokens[background]);
  assert.ok(ratio >= minimum, `${foreground} on ${background}: ${ratio.toFixed(2)} < ${minimum}`);
};

test('JS consumers resolve defined CSS tokens without a divergent palette', () => {
  const visit = (value) => {
    if (typeof value === 'object') {
      assert.ok(Object.isFrozen(value), 'shared token objects must be immutable');
      Object.values(value).forEach(visit);
    } else {
      const name = value.match(/^var\(--fm-([\w-]+)\)$/)?.[1];
      assert.ok(name && tokens[name], `undefined semantic token: ${value}`);
    }
  };
  visit(designTokens);
});

test('small text and bronze hierarchy meet 4.5:1 on app surfaces', () => {
  for (const foreground of ['text', 'muted', 'secondary', 'primary', 'bronze']) {
    for (const background of ['canvas', 'surface', 'raised']) assertContrast(foreground, background, 4.5);
  }
});

test('status labels retain readable text in every semantic state', () => {
  for (const status of ['success', 'warning', 'danger', 'info']) {
    assertContrast(`status-${status}`, `status-${status}-soft`, 4.5);
  }
  assert.ok(contrast('#ffffff', tokens.primary) >= 4.5, 'primary button text');
  assert.ok(contrast('#ffffff', tokens['primary-hover']) >= 4.5, 'primary hovered button text');
});

test('sidebar navigation remains readable in normal and selected states', () => {
  for (const text of ['sidebar-text', 'sidebar-muted']) {
    for (const background of ['sidebar', 'sidebar-raised']) assertContrast(text, background, 4.5);
  }
});

test('form borders and keyboard focus are visible at 3:1', () => {
  for (const background of ['canvas', 'surface', 'raised']) assertContrast('focus', background, 3);
  assertContrast('border-strong', 'raised', 3);
  assertContrast('bronze-light', 'sidebar', 3);
  assertContrast('bronze-light', 'sidebar-raised', 3);
});

// CSS variables are the source of truth; JS consumers share the same theme.
const token = (name) => `var(--fm-${name})`;
export const designTokens = Object.freeze({
  appCanvas: token("canvas"), sidebar: token("sidebar"), surface: token("surface"),
  raised: token("raised"), inset: token("inset"), border: token("border"),
  text: token("text"), muted: token("muted"), primary: token("primary"),
  secondary: token("secondary"), focus: token("focus"), bronze: token("bronze"),
  status: Object.freeze(Object.fromEntries(["success", "warning", "danger", "info"].map((name) => [name, token(`status-${name}`)]))),
  spacing: Object.freeze(Object.fromEntries([1, 2, 3, 4, 5, 6, 8, 10].map((size) => [size, token(`space-${size}`)]))),
  radius: Object.freeze(Object.fromEntries(["sm", "md", "lg"].map((size) => [size, token(`radius-${size}`)]))),
  type: Object.freeze(Object.fromEntries(["xs", "sm", "base", "lg", "xl"].map((size) => [size, token(`text-${size}`)]))),
  shadow: Object.freeze({ card: token("shadow-card"), raised: token("shadow-raised") }),
});

/**
 * Licensed under the Apache License, Version 2.0 (the "License");
 * Copyright 2024 Øivind Loe
 * See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
 * ~
 **/
import MarkdownIt from 'markdown-it';
import markdownItKatex from '@vscode/markdown-it-katex';
import DOMPurify from 'dompurify';
import mermaid from 'mermaid';
import 'katex/dist/katex.min.css';

const targetGenerations = new WeakMap();
const sanitizedNamedPropertyPrefix = 'user-content-';
let mermaidInitialized = false;
let renderSequence = 0;

const markdown = new MarkdownIt({
  html: false,
  linkify: true,
  typographer: false,
});
// The KaTeX plugin is CommonJS; normalize its default wrapper in the standalone esbuild bundle.
const katexPluginImport = /** @type {any} */ (markdownItKatex);
const katexPlugin =
  typeof katexPluginImport === 'function' ? katexPluginImport : katexPluginImport.default;
markdown.use(katexPlugin, { throwOnError: false });

/**
 * Convert non-web destinations to inert text and decorate accepted web links.
 * Only explicit HTTP(S) destinations match the product's external-navigation contract.
 */
markdown.core.ruler.after('inline', 'chief_http_links', (state) => {
  for (const blockToken of state.tokens) {
    const inlineTokens = blockToken.children ?? [];
    const linkTags = [];
    for (const token of inlineTokens) {
      if (token.type === 'link_open') {
        const href = token.attrGet('href') ?? '';
        const accepted = /^https?:\/\//i.test(href);
        linkTags.push(accepted ? 'a' : 'span');
        if (accepted) {
          token.attrSet('target', '_blank');
          token.attrSet('rel', 'noopener noreferrer');
        } else {
          token.tag = 'span';
          token.attrs = [];
        }
      } else if (token.type === 'link_close') {
        token.tag = linkTags.pop() ?? 'span';
      }
    }
  }
});

/** Render image syntax as visible text so untrusted output cannot initiate image requests. */
markdown.renderer.rules.image = (tokens, index) => {
  const token = tokens[index];
  const source = token.attrGet('src') ?? '';
  const title = token.attrGet('title');
  const titleSuffix = title === null ? '' : ` "${title}"`;
  return markdown.utils.escapeHtml(`![${token.content}](${source}${titleSuffix})`);
};

// Delegate non-Mermaid fences to MarkdownIt's canonical renderer.
const defaultFence =
  markdown.renderer.rules.fence ??
  ((tokens, index, options, _environment, renderer) => renderer.renderToken(tokens, index, options));

/**
 * Emit inert Mermaid source placeholders while preserving ordinary fence rendering.
 * Only an info string that trims exactly to "mermaid" opts into diagram rendering.
 */
markdown.renderer.rules.fence = (tokens, index, options, environment, renderer) => {
  const token = tokens[index];
  if (token.info.trim() !== 'mermaid') {
    return defaultFence(tokens, index, options, environment, renderer);
  }
  const escapedSource = markdown.utils.escapeHtml(token.content);
  return `<div class="rich-mermaid-source" data-mermaid-index="${index}"><pre><code>${escapedSource}</code></pre></div>\n`;
};

/**
 * Configure Mermaid once for explicit, strict, dark-theme browser rendering.
 * Callers may safely invoke this before every render.
 */
export function initializeRichContent() {
  if (mermaidInitialized) {
    return;
  }
  mermaid.initialize({
    startOnLoad: false,
    securityLevel: 'strict',
    theme: 'dark',
    suppressErrorRendering: true,
    secure: [
      'secure',
      'securityLevel',
      'startOnLoad',
      'maxTextSize',
      'theme',
      'themeCSS',
      'themeVariables',
      'fontFamily',
      'altFontFamily',
      'fontSize',
      'darkMode',
      'htmlLabels',
    ],
  });
  mermaidInitialized = true;
}

/**
 * Append a local, accessible rendering status without interpreting message text as markup.
 * The message is assumed to be application-owned text.
 */
function appendRenderFailure(container, message) {
  const status = document.createElement('span');
  status.className = 'rich-render-failure';
  status.setAttribute('role', 'status');
  status.textContent = message;
  container.append(status);
}

/**
 * Invalidate pending asynchronous work for a target without changing its current DOM.
 * The target's next render will start from the incremented generation.
 */
export function cancelRichContent(target) {
  targetGenerations.set(target, (targetGenerations.get(target) ?? 0) + 1);
}

/**
 * Build a literal-source fallback using DOM text handling only.
 * The supplied source is untrusted and must never be interpreted as HTML.
 */
function createSourceFallback(source) {
  const sourceFallback = document.createElement('pre');
  sourceFallback.className = 'rich-content-source-fallback';
  sourceFallback.textContent = source;
  return sourceFallback;
}

/**
 * Decode CSS escapes before policy matching so obfuscated resource functions remain visible.
 * Mermaid source itself is not changed; this normalized copy is used only for rejection.
 */
function decodeCssEscapes(source) {
  return source
    .replace(/\\(?:\r\n|[\n\r\f])/g, '')
    .replace(/\\([0-9a-f]{1,6})(?:\r\n|[ \t\r\n\f])?/gi, (_match, hex) =>
      String.fromCodePoint(Number.parseInt(hex, 16)),
    )
    .replace(/\\([^\r\n\f0-9a-f])/gi, '$1');
}

/**
 * Accept only diagram structure and labels before Mermaid can parse source or create DOM.
 * Styling, configuration, navigation, images, and resource-like URLs are outside product scope.
 */
function mermaidSourceIsSafe(source) {
  const normalized = decodeCssEscapes(source).replace(/\/\*[\s\S]*?\*\//g, '').normalize('NFKC');
  const forbiddenStyleDirective =
    /(?:^|[;\r\n])\s*(?:style|classDef|class|linkStyle)\b/i;
  const forbiddenNavigationDirective =
    /(?:^|[;\r\n])\s*(?:click|href|links?|callback|navigate|navigation)\b/i;
  return !(
    /^\s*%\s*%\s*\{/m.test(normalized) ||
    /^\s*---\s*$(?:[\s\S]*?^\s*---\s*$)?/m.test(normalized) ||
    forbiddenStyleDirective.test(normalized) ||
    forbiddenNavigationDirective.test(normalized) ||
    /!\s*\[[^\]]*\]\s*\(/i.test(normalized) ||
    /@\s*\{[^}]*\b(?:img|image|icon)\s*:/i.test(normalized) ||
    /\b(?:img|image)\s*:/i.test(normalized) ||
    /(?:<|&lt;)\s*(?:img|image|a|link|style)\b/i.test(normalized) ||
    /@import\b/i.test(normalized) ||
    /\burl\s*\(/i.test(normalized) ||
    /(?:https?:)?\/\//i.test(normalized) ||
    /\b(?:about|blob|callto|chrome|chrome-extension|cid|data|facetime|facetime-audio|file|filesystem|ftp|geo|intent|irc|ircs|javascript|mailto|market|mid|moz-extension|resource|sip|sips|sms|tel|urn|vbscript|view-source|webcal|ws|wss):/i.test(
      normalized,
    ) ||
    /(?:^|[\s"'(])(?:\.\.?\/|\/(?!\/))[\w.%~/-]+/m.test(normalized)
  );
}

/**
 * Reconnect safe same-document SVG references after DOMPurify prefixes named properties.
 * Only fragment IDs with a matching sanitized element are rewritten.
 */
function secureSvgFragmentReferences(svgTree) {
  const sanitizedIds = new Map();
  for (const element of svgTree.querySelectorAll('[id]')) {
    const sanitizedId = element.getAttribute('id');
    if (sanitizedId?.startsWith(sanitizedNamedPropertyPrefix)) {
      sanitizedIds.set(sanitizedId.slice(sanitizedNamedPropertyPrefix.length), sanitizedId);
    }
  }

  // Resolve only IDs observed in the sanitized tree; all other resource references become inert.
  const referencedId = (originalId) => sanitizedIds.get(originalId);
  const urlReferencePattern = /url\(\s*(['"]?)([^)'"]+)\1\s*\)/gi;
  const secureCss = (value) =>
    value
      .replace(/@import\s+(?:url\([^)]*\)|["'][^"']*["'])[^;]*;?/gi, '')
      .replace(urlReferencePattern, (_match, _quote, reference) => {
        const trimmedReference = reference.trim();
        if (!trimmedReference.startsWith('#')) {
          return 'none';
        }
        const sanitizedId = referencedId(trimmedReference.slice(1));
        return sanitizedId ? `url(#${sanitizedId})` : 'none';
      });

  for (const image of svgTree.querySelectorAll('image')) {
    image.remove();
  }
  for (const style of svgTree.querySelectorAll('style')) {
    style.textContent = secureCss(style.textContent ?? '');
  }
  for (const element of svgTree.querySelectorAll('*')) {
    for (const attribute of [...element.attributes]) {
      const attributeName = attribute.name.toLowerCase();
      let repairedValue = secureCss(attribute.value);
      if (attributeName === 'href' || attributeName === 'xlink:href') {
        const sanitizedId = repairedValue.startsWith('#')
          ? referencedId(repairedValue.slice(1))
          : undefined;
        if (!sanitizedId) {
          element.removeAttributeNode(attribute);
          continue;
        }
        repairedValue = `#${sanitizedId}`;
      } else if (attributeName === 'src') {
        element.removeAttributeNode(attribute);
        continue;
      } else if (attribute.name === 'aria-labelledby' || attribute.name === 'aria-describedby') {
        repairedValue = repairedValue
          .split(/\s+/)
          .map((originalId) => referencedId(originalId))
          .filter(Boolean)
          .join(' ');
        if (!repairedValue) {
          element.removeAttributeNode(attribute);
          continue;
        }
      }
      attribute.value = repairedValue;
    }
  }
}

/**
 * Render untrusted rich text into a browser element if this generation remains current.
 * The caller must provide a live Element that it owns.
 */
export async function renderRichContent(target, source) {
  const generation = (targetGenerations.get(target) ?? 0) + 1;
  targetGenerations.set(target, generation);
  try {
    initializeRichContent();
    const sequence = ++renderSequence;
    target.innerHTML = DOMPurify.sanitize(markdown.render(source), {
      USE_PROFILES: { html: true },
      SANITIZE_NAMED_PROPS: true,
      ADD_ATTR: ['target'],
    });

    for (const invalidFormula of target.querySelectorAll('.katex-error')) {
      appendRenderFailure(invalidFormula.parentElement ?? target, 'Formula could not be rendered');
    }

    const placeholders = [...target.querySelectorAll('[data-mermaid-index]')];
    for (const [diagramIndex, placeholder] of placeholders.entries()) {
      if (targetGenerations.get(target) !== generation) {
        return false;
      }
      const diagramSource = placeholder.textContent;
      try {
        if (!mermaidSourceIsSafe(diagramSource)) {
          appendRenderFailure(placeholder, 'Diagram could not be rendered');
          continue;
        }
        const result = await mermaid.render(`chief-mermaid-${sequence}-${diagramIndex}`, diagramSource);
        if (targetGenerations.get(target) !== generation) {
          return false;
        }
        const safeSvg = DOMPurify.sanitize(result.svg, {
          USE_PROFILES: { svg: true, svgFilters: true },
          SANITIZE_NAMED_PROPS: true,
        });
        const svgTemplate = document.createElement('template');
        svgTemplate.innerHTML = safeSvg;
        secureSvgFragmentReferences(svgTemplate.content);
        placeholder.replaceWith(svgTemplate.content);
      } catch {
        if (targetGenerations.get(target) !== generation) {
          return false;
        }
        appendRenderFailure(placeholder, 'Diagram could not be rendered');
      }
    }
    return true;
  } catch {
    if (targetGenerations.get(target) !== generation) {
      return false;
    }
    target.replaceChildren(createSourceFallback(source));
    return false;
  }
}

// Expose the module API for Alpine expressions loaded by a classic inline script.
Object.assign(window, {
  chiefRichContent: {
    cancelRichContent,
    initializeRichContent,
    renderRichContent,
  },
});

// Announce only after the complete documented global is available to classic scripts.
window.dispatchEvent(new CustomEvent('chief:rich-content-ready'));

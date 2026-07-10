/**
 * Licensed under the Apache License, Version 2.0 (the "License");
 * Copyright 2024 Øivind Loe
 * See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
 * ~
 **/
/**
 * Agent config YAML editor (CodeMirror 6) with schema autocomplete and save/mutate.
 */
import { EditorView, keymap, lineNumbers, drawSelection, highlightActiveLine, ViewPlugin } from '@codemirror/view';
import { defaultKeymap, history, historyKeymap, indentWithTab } from '@codemirror/commands';
import { yaml } from '@codemirror/lang-yaml';
import { autocompletion, completionKeymap } from '@codemirror/autocomplete';
import { oneDark } from '@codemirror/theme-one-dark';
import { EditorState } from '@codemirror/state';

function readPageData() {
  const el = document.getElementById('config-page-data');
  if (!el) {
    return { initialYaml: '', catalog: { schema_keys: [], tool_types: [] }, mode: 'edit', urls: {} };
  }
  try {
    return JSON.parse(el.textContent || '{}');
  } catch (err) {
    const parent = document.getElementById('config-editor');
    if (parent) {
      parent.textContent = `Failed to load editor config: ${err instanceof Error ? err.message : 'invalid JSON'}`;
    }
    return { initialYaml: '', catalog: { schema_keys: [], tool_types: [] }, mode: 'edit', urls: {} };
  }
}

function schemaCompletions(context, keys) {
  const word = context.matchBefore(/[a-zA-Z0-9_.-]+/);
  if (!word || (word.from === word.to && !context.explicit)) return null;
  return {
    from: word.from,
    options: keys.map((label) => ({ label, type: 'property' })),
  };
}

function showErrors(errors) {
  const box = document.getElementById('validation-errors');
  const list = box?.querySelector('ul');
  if (!box || !list) return;
  list.innerHTML = '';
  if (!errors?.length) {
    box.style.display = 'none';
    return;
  }
  for (const err of errors) {
    const li = document.createElement('li');
    const prefix = err.path ? `${err.path}: ` : '';
    const line = err.line ? ` (line ${err.line})` : '';
    li.textContent = `${prefix}${err.message}${line}`;
    list.appendChild(li);
  }
  box.style.display = 'block';
}

function existingToolIds(yaml) {
  const ids = new Set();
  for (const match of yaml.matchAll(/^\s+-\s+id:\s*([^\s#]+)/gm)) {
    ids.add(match[1]);
  }
  return ids;
}

function suggestToolId(type, yaml) {
  const ids = existingToolIds(yaml);
  if (!ids.has(type)) return type;
  for (let n = 2; n < 100; n += 1) {
    const candidate = `${type}-${n}`;
    if (!ids.has(candidate)) return candidate;
  }
  return `${type}-${Date.now()}`;
}

function parseSpecSummary(yaml) {
  const tools = [];
  const triggers = [];
  const queues = [];
  let section = '';
  /** @type {{ id: string, type: string } | { name: string, kind: string } | { id: string } | null} */
  let current = null;

  const flush = () => {
    if (!current) return;
    if (section === 'tools' && 'type' in current && current.id) {
      tools.push({ id: current.id, type: current.type || '?' });
    } else if (section === 'triggers' && 'kind' in current && current.name) {
      triggers.push({ name: current.name, kind: current.kind || '?' });
    } else if (section === 'queues' && 'id' in current && current.id && !('type' in current)) {
      queues.push({ id: current.id });
    }
    current = null;
  };

  for (const line of yaml.split('\n')) {
    if (/^tools:\s*$/.test(line)) {
      flush();
      section = 'tools';
      continue;
    }
    if (/^triggers:\s*$/.test(line)) {
      flush();
      section = 'triggers';
      continue;
    }
    if (/^queues:\s*$/.test(line)) {
      flush();
      section = 'queues';
      continue;
    }
    if (/^[a-zA-Z_][\w]*:/.test(line) && !/^\s/.test(line)) {
      flush();
      section = '';
      continue;
    }

    if (section === 'tools' && /^\s+-\s/.test(line)) {
      flush();
      current = { id: '', type: '?' };
      const inlineId = line.match(/id:\s*([^\s#]+)/);
      if (inlineId) current.id = inlineId[1];
      const inlineType = line.match(/type:\s*([^\s#]+)/);
      if (inlineType) current.type = inlineType[1];
      continue;
    }
    if (section === 'triggers' && /^\s+-\s/.test(line)) {
      flush();
      current = { name: '', kind: '?' };
      const inlineName = line.match(/name:\s*([^\s#]+)/);
      if (inlineName) current.name = inlineName[1];
      const inlineKind = line.match(/kind:\s*([^\s#]+)/);
      if (inlineKind) current.kind = inlineKind[1];
      continue;
    }
    if (section === 'queues' && /^\s+-\s/.test(line)) {
      flush();
      current = { id: '' };
      const inlineId = line.match(/id:\s*([^\s#]+)/);
      if (inlineId) current.id = inlineId[1];
      continue;
    }

    if (section === 'tools' && current && 'type' in current) {
      const idMatch = line.match(/^\s+id:\s*([^\s#]+)/);
      const typeMatch = line.match(/^\s+type:\s*([^\s#]+)/);
      if (idMatch) current.id = idMatch[1];
      if (typeMatch) current.type = typeMatch[1];
    }
    if (section === 'triggers' && current && 'kind' in current) {
      const nameMatch = line.match(/^\s+name:\s*([^\s#]+)/);
      const kindMatch = line.match(/^\s+kind:\s*([^\s#]+)/);
      if (nameMatch) current.name = nameMatch[1];
      if (kindMatch) current.kind = kindMatch[1];
    }
    if (section === 'queues' && current && !('type' in current)) {
      const idMatch = line.match(/^\s+id:\s*([^\s#]+)/);
      if (idMatch) current.id = idMatch[1];
    }
  }
  flush();
  return { tools, triggers, queues };
}

function fillSelect(select, items, formatOption) {
  if (!select) return;
  select.innerHTML = '';
  for (const item of items) {
    const opt = document.createElement('option');
    const formatted = formatOption(item);
    opt.value = formatted.value;
    opt.textContent = formatted.label;
    select.appendChild(opt);
  }
}

function refreshRemoveHelpers(yaml) {
  const summary = parseSpecSummary(yaml);

  const toolHelper = document.getElementById('remove-tool-helper');
  const toolSelect = document.getElementById('remove-tool-select');
  if (toolHelper && toolSelect) {
    toolHelper.hidden = summary.tools.length === 0;
    fillSelect(toolSelect, summary.tools, (t) => ({
      value: t.id,
      label: `${t.id} (${t.type})`,
    }));
  }

  const triggerHelper = document.getElementById('remove-trigger-helper');
  const triggerSelect = document.getElementById('remove-trigger-select');
  if (triggerHelper && triggerSelect) {
    triggerHelper.hidden = summary.triggers.length === 0;
    fillSelect(triggerSelect, summary.triggers, (t) => ({
      value: t.name,
      label: `${t.name} (${t.kind})`,
    }));
  }

  const queueHelper = document.getElementById('remove-queue-helper');
  const queueSelect = document.getElementById('remove-queue-select');
  const sourceHelper = document.getElementById('add-source-helper');
  const sourceQueueSelect = document.getElementById('add-source-queue-select');
  if (queueHelper && queueSelect) {
    queueHelper.hidden = summary.queues.length === 0;
    fillSelect(queueSelect, summary.queues, (q) => ({ value: q.id, label: q.id }));
  }
  if (sourceHelper && sourceQueueSelect) {
    sourceHelper.hidden = summary.queues.length === 0;
    fillSelect(sourceQueueSelect, summary.queues, (q) => ({ value: q.id, label: q.id }));
  }
}

function formToMutation(form) {
  const action = form.dataset.mutation;
  const data = { action };
  const fd = new FormData(form);
  for (const [key, value] of fd.entries()) {
    if (key === 'csrfmiddlewaretoken') continue;
    if (key.startsWith('config_')) continue;
    if (key === 'allow_action' || key === 'model_choice') continue;
    const str = String(value).trim();
    if (!str) continue;
    if (key === 'temperature' || key === 'max_attempts') {
      data[key] = Number(str);
    } else if (key === 'config_json') {
      data.config = JSON.parse(str);
    } else {
      data[key] = str;
    }
  }

  if (action === 'set_llm') {
    const modelChoice = /** @type {HTMLSelectElement | null} */ (
      form.querySelector('[name=model_choice]')
    );
    const selected = modelChoice?.selectedOptions?.[0];
    if (selected) {
      data.provider = selected.dataset.provider || '';
      data.model = selected.dataset.model || '';
    }
  }

  if (action === 'add_tool') {
    const checked = [...form.querySelectorAll('input[name=allow_action]:checked')].map(
      (el) => /** @type {HTMLInputElement} */ (el).value,
    );
    data.allow = checked.length ? checked : ['*'];
  }

  if (action === 'add_source' && !data.config) {
    const adapterType = data.type;
    if (adapterType === 'test') {
      data.config = {
        prefix: String(fd.get('config_prefix') || 'test').trim() || 'test',
        batch_size: Number(fd.get('config_batch_size') || '1'),
      };
    } else {
      const raw = String(fd.get('config_json') || '{}').trim() || '{}';
      data.config = JSON.parse(raw);
    }
  }
  return data;
}

async function postForm(url, body, headers = {}) {
  const page = readPageData();
  const urls = page.urls || {};
  const res = await fetch(url, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/x-www-form-urlencoded',
      'X-CSRFToken': urls.csrf || '',
      ...headers,
    },
    body: new URLSearchParams(body),
  });
  const json = await res.json().catch(() => ({}));
  return { ok: res.ok, status: res.status, json };
}

function credentialOptions(credentials, credentialType) {
  const options = [{ value: '', label: '(default)' }];
  if (!credentials) return options;
  const types = credentialType ? [credentialType] : Object.keys(credentials);
  for (const type of types) {
    for (const cred of credentials[type] || []) {
      const status = cred.is_set ? 'Set' : 'Not set';
      options.push({
        value: cred.name,
        label: `${cred.name} — ${status} (${type})`,
      });
    }
  }
  return options;
}

function fillCredentialSelect(select, credentials, credentialType) {
  if (!select) return;
  select.innerHTML = '';
  for (const opt of credentialOptions(credentials, credentialType)) {
    const el = document.createElement('option');
    el.value = opt.value;
    el.textContent = opt.label;
    select.appendChild(el);
  }
}

function renderToolActionCheckboxes(container, toolTypes, toolType, _yaml) {
  if (!container) return;
  container.innerHTML = '';
  const meta = toolTypes.find((t) => t.type === toolType);
  if (!meta?.functions?.length) {
    const note = document.createElement('p');
    note.className = 'muted';
    note.style.fontSize = '.85rem';
    note.textContent = 'No actions registered for this tool type.';
    container.appendChild(note);
    return;
  }
  const readFns = meta.functions.filter((fn) => fn.readonly);
  const writeFns = meta.functions.filter((fn) => !fn.readonly);
  const groups = [
    ['Read', readFns],
    ['Write', writeFns],
  ];
  for (const [label, fns] of groups) {
    if (!fns.length) continue;
    const fieldset = document.createElement('fieldset');
    const legend = document.createElement('legend');
    legend.textContent = label;
    fieldset.appendChild(legend);
    for (const fn of fns) {
      const wrap = document.createElement('label');
      const input = document.createElement('input');
      input.type = 'checkbox';
      input.name = 'allow_action';
      input.value = fn.name;
      input.checked = true;
      wrap.appendChild(input);
      wrap.appendChild(document.createTextNode(fn.name));
      fieldset.appendChild(wrap);
    }
    container.appendChild(fieldset);
  }
}

function initAdapterConfigPanels() {
  const adapterType = /** @type {HTMLSelectElement | null} */ (
    document.getElementById('helper-adapter-type')
  );
  const testPanel = document.getElementById('adapter-config-test');
  const jsonPanel = document.getElementById('adapter-config-json');
  const sync = () => {
    const isTest = adapterType?.value === 'test';
    if (testPanel) testPanel.hidden = !isTest;
    if (jsonPanel) jsonPanel.hidden = isTest;
  };
  adapterType?.addEventListener('change', sync);
  sync();
}

function initCredentialPickers(catalog) {
  const credentials = catalog.credentials || {};
  const toolType = /** @type {HTMLSelectElement | null} */ (
    document.getElementById('helper-tool-type')
  );
  const toolCred = document.getElementById('helper-tool-credential');
  const adapterType = /** @type {HTMLSelectElement | null} */ (
    document.getElementById('helper-adapter-type')
  );
  const sourceCred = document.getElementById('helper-source-credential');

  const syncToolCreds = () => {
    const credType = toolType?.selectedOptions?.[0]?.dataset?.credentialType || '';
    fillCredentialSelect(toolCred, credentials, credType || null);
  };
  const syncSourceCreds = () => {
    const credType = adapterType?.selectedOptions?.[0]?.dataset?.credentialType || '';
    fillCredentialSelect(sourceCred, credentials, credType || null);
  };

  toolType?.addEventListener('change', syncToolCreds);
  adapterType?.addEventListener('change', syncSourceCreds);
  syncToolCreds();
  syncSourceCreds();
}

function initTriggerHelper(catalog) {
  const kindSelect = /** @type {HTMLSelectElement | null} */ (
    document.getElementById('helper-trigger-kind')
  );
  const promptRow = document.getElementById('helper-trigger-prompt-row');
  const promptInput = /** @type {HTMLTextAreaElement | null} */ (
    document.getElementById('helper-trigger-prompt')
  );
  const cronRow = document.getElementById('helper-trigger-cron-row');
  const defaults = catalog.trigger_prompt_defaults || {};

  /** Show prompt/cron fields appropriate for the selected trigger kind. */
  const sync = () => {
    const kind = kindSelect?.value || 'manual';
    const isManual = kind === 'manual';
    if (promptRow) promptRow.hidden = isManual;
    if (cronRow) cronRow.hidden = kind !== 'schedule';
    if (promptInput) {
      promptInput.required = !isManual;
      promptInput.placeholder = defaults[kind] || '';
    }
  };

  kindSelect?.addEventListener('change', sync);
  sync();
}

function initToolHelper(catalog, getYaml) {
  const toolType = /** @type {HTMLSelectElement | null} */ (
    document.getElementById('helper-tool-type')
  );
  const toolId = /** @type {HTMLInputElement | null} */ (document.getElementById('helper-tool-id'));
  const actions = document.getElementById('tool-allow-actions');
  const sync = () => {
    const type = toolType?.value || '';
    if (toolId && type) {
      toolId.value = suggestToolId(type, getYaml());
    }
    renderToolActionCheckboxes(actions, catalog.tool_types || [], type, getYaml());
  };
  toolType?.addEventListener('change', sync);
  sync();
}

function initSidebarResize() {
  const resizer = document.getElementById('config-sidebar-resizer');
  const sidebar = document.getElementById('config-sidebar');
  if (!resizer || !sidebar) return;

  const storageKey = 'chief.configSidebarWidth';
  const saved = localStorage.getItem(storageKey);
  if (saved) {
    sidebar.style.setProperty('--config-sidebar-width', saved);
  }

  let dragging = false;
  const onMove = (event) => {
    if (!dragging) return;
    const workspace = sidebar.parentElement;
    if (!workspace) return;
    const rect = workspace.getBoundingClientRect();
    const width = Math.min(Math.max(rect.right - event.clientX, 16 * 16), rect.width * 0.5);
    const value = `${Math.round(width)}px`;
    sidebar.style.setProperty('--config-sidebar-width', value);
    localStorage.setItem(storageKey, value);
  };
  const stop = () => {
    dragging = false;
    resizer.classList.remove('dragging');
    document.body.style.cursor = '';
    document.body.style.userSelect = '';
    window.removeEventListener('mousemove', onMove);
    window.removeEventListener('mouseup', stop);
  };

  resizer.addEventListener('mousedown', (event) => {
    event.preventDefault();
    dragging = true;
    resizer.classList.add('dragging');
    document.body.style.cursor = 'col-resize';
    document.body.style.userSelect = 'none';
    window.addEventListener('mousemove', onMove);
    window.addEventListener('mouseup', stop);
  });
}

/** Grow the editor with document content instead of using an inner scroll area. */
const autoHeightTheme = EditorView.theme({
  '&': { height: 'auto' },
  '.cm-scroller': { overflow: 'visible' },
  '.cm-content': { minHeight: '8rem' },
});

const autoHeightPlugin = ViewPlugin.fromClass(
  class {
    constructor(view) {
      this.updateHeight(view);
    }

    update(update) {
      if (update.docChanged || update.geometryChanged) {
        this.updateHeight(update.view);
      }
    }

    /** Set the editor DOM height from CodeMirror's measured content height. */
    updateHeight(view) {
      view.dom.style.height = `${view.contentHeight}px`;
    }
  },
);

function init() {
  const data = readPageData();
  const urls = data.urls || {};
  const restored = sessionStorage.getItem('agentConfigRestoreYaml');
  if (restored && !data.readOnly) {
    sessionStorage.removeItem('agentConfigRestoreYaml');
    data.initialYaml = restored;
  }

  initCredentialPickers(data.catalog || {});
  initAdapterConfigPanels();
  initTriggerHelper(data.catalog || {});
  initSidebarResize();

  const schemaKeys = data.catalog?.schema_keys || [];
  const extensions = [
    lineNumbers(),
    drawSelection(),
    highlightActiveLine(),
    history(),
    yaml(),
    oneDark,
    autoHeightTheme,
    autoHeightPlugin,
    keymap.of([...defaultKeymap, ...historyKeymap, ...completionKeymap, indentWithTab]),
    autocompletion({
      override: [(ctx) => schemaCompletions(ctx, schemaKeys)],
    }),
    EditorView.lineWrapping,
    EditorState.readOnly.of(Boolean(data.readOnly)),
    EditorView.editable.of(!data.readOnly),
  ];

  const parent = document.getElementById('config-editor');
  if (!parent) return;

  const view = new EditorView({
    doc: data.initialYaml || '',
    extensions,
    parent,
  });

  const getYaml = () => view.state.doc.toString();
  initToolHelper(data.catalog || {}, getYaml);
  refreshRemoveHelpers(getYaml());

  const isCreate = data.mode === 'create';

  document.getElementById('save-config')?.addEventListener('click', async () => {
    const spec_yaml = getYaml();
    const body = { spec_yaml };
    const nameEl = document.getElementById('agent-name');
    if (nameEl instanceof HTMLInputElement && nameEl.value.trim()) {
      body.name = nameEl.value.trim();
    }
    const identifierEl = document.getElementById('agent-identifier');
    if (identifierEl instanceof HTMLInputElement && identifierEl.value.trim()) {
      body.identifier = identifierEl.value.trim();
    }
    const headers = isCreate ? { Accept: 'application/json' } : {};
    const { ok, json } = await postForm(urls.save, body, headers);
    if (!ok) {
      showErrors(json.errors || [{ path: '', message: isCreate ? 'Create failed' : 'Save failed' }]);
      return;
    }
    showErrors([]);
    if (isCreate && json.redirect) {
      window.location.href = json.redirect;
      return;
    }
    window.location.reload();
  });

  document.querySelectorAll('form.helper-form').forEach((form) => {
    form.addEventListener('submit', async (event) => {
      event.preventDefault();
      let mutation;
      try {
        mutation = JSON.stringify(formToMutation(form));
      } catch (err) {
        showErrors([{ path: '', message: err instanceof Error ? err.message : 'Invalid form data' }]);
        return;
      }
      const spec_yaml = getYaml();
      const { ok, json } = await postForm(urls.mutate, { spec_yaml, mutation });
      if (!ok) {
        showErrors(json.errors || [{ path: '', message: 'Mutation failed' }]);
        return;
      }
      showErrors([]);
      const newYaml = json.yaml || '';
      view.dispatch({
        changes: { from: 0, to: view.state.doc.length, insert: newYaml },
      });
      refreshRemoveHelpers(newYaml);
      if (form.dataset.mutation === 'add_tool') {
        initToolHelper(data.catalog || {}, getYaml);
      }
    });
  });
}

init();

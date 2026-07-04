/**
 * Licensed under the Apache License, Version 2.0 (the "License");
 * Copyright 2024 Øivind Loe
 * See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
 * ~
 **/
/**
 * Agent config YAML editor (CodeMirror 6) with schema autocomplete and save/mutate.
 */
import { EditorView, keymap, lineNumbers, drawSelection, highlightActiveLine } from '@codemirror/view';
import { defaultKeymap, history, historyKeymap, indentWithTab } from '@codemirror/commands';
import { yaml } from '@codemirror/lang-yaml';
import { autocompletion, completionKeymap } from '@codemirror/autocomplete';
import { oneDark } from '@codemirror/theme-one-dark';

function readPageData() {
  const el = document.getElementById('config-page-data');
  if (!el) return { initialYaml: '', catalog: { schema_keys: [] } };
  return JSON.parse(el.textContent || '{}');
}

function schemaCompletions(context, keys) {
  const word = context.matchBefore(/[a-zA-Z0-9_.\[\]-]*/);
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

function formToMutation(form) {
  const action = form.dataset.mutation;
  const data = { action };
  const fd = new FormData(form);
  for (const [key, value] of fd.entries()) {
    if (key === 'csrfmiddlewaretoken') continue;
    const str = String(value).trim();
    if (!str) continue;
    if (key === 'allow') {
      data.allow = str.split(',').map((s) => s.trim()).filter(Boolean);
    } else if (key === 'temperature' || key === 'max_attempts') {
      data[key] = Number(str);
    } else if (key === 'config_json') {
      data.config = JSON.parse(str);
    } else {
      data[key] = str;
    }
  }
  return data;
}

async function postForm(url, body) {
  const urls = window.__AGENT_CONFIG_URLS__ || {};
  const res = await fetch(url, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/x-www-form-urlencoded',
      'X-CSRFToken': urls.csrf || '',
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

function initCredentialPickers() {
  const urls = window.__AGENT_CONFIG_URLS__ || {};
  const credentials = urls.credentials || {};
  const toolType = document.getElementById('helper-tool-type');
  const toolCred = document.getElementById('helper-tool-credential');
  const adapterType = document.getElementById('helper-adapter-type');
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

function init() {
  const data = readPageData();
  const urls = window.__AGENT_CONFIG_URLS__ || {};
  const restored = sessionStorage.getItem('agentConfigRestoreYaml');
  if (restored) {
    sessionStorage.removeItem('agentConfigRestoreYaml');
    data.initialYaml = restored;
  }

  initCredentialPickers();

  const schemaKeys = data.catalog?.schema_keys || [];
  const extensions = [
    lineNumbers(),
    drawSelection(),
    highlightActiveLine(),
    history(),
    yaml(),
    oneDark,
    keymap.of([...defaultKeymap, ...historyKeymap, ...completionKeymap, indentWithTab]),
    autocompletion({
      override: [(ctx) => schemaCompletions(ctx, schemaKeys)],
    }),
    EditorView.lineWrapping,
  ];

  const parent = document.getElementById('config-editor');
  if (!parent) return;

  const view = new EditorView({
    doc: data.initialYaml || '',
    extensions,
    parent,
  });

  document.getElementById('save-config')?.addEventListener('click', async () => {
    const spec_yaml = view.state.doc.toString();
    const { ok, json } = await postForm(urls.save, { spec_yaml });
    if (!ok) {
      showErrors(json.errors || [{ path: '', message: 'Save failed' }]);
      return;
    }
    showErrors([]);
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
      const spec_yaml = view.state.doc.toString();
      const { ok, json } = await postForm(urls.mutate, { spec_yaml, mutation });
      if (!ok) {
        showErrors(json.errors || [{ path: '', message: 'Mutation failed' }]);
        return;
      }
      showErrors([]);
      view.dispatch({
        changes: { from: 0, to: view.state.doc.length, insert: json.yaml || '' },
      });
    });
  });
}

init();

/**
 * Licensed under the Apache License, Version 2.0 (the "License");
 * Copyright 2024 Øivind Loe
 * See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
 * ~
 **/
/**
 * Agent config YAML editor (CodeMirror 6) with schema autocomplete and save/mutate.
 */
import { EditorView, keymap, lineNumbers, drawSelection, highlightActiveLine } from 'https://esm.sh/codemirror@6.0.1';
import { defaultKeymap, history, historyKeymap, indentWithTab } from 'https://esm.sh/@codemirror/commands@6.6.0';
import { yaml } from 'https://esm.sh/@codemirror/lang-yaml@6.1.1';
import { autocompletion, completionKeymap } from 'https://esm.sh/@codemirror/autocomplete@6.18.1';
import { oneDark } from 'https://esm.sh/@codemirror/theme-one-dark@6.1.2';

function readPageData() {
  const el = document.getElementById('config-page-data');
  if (!el) return { initialYaml: '', catalog: { schema_keys: [] } };
  return JSON.parse(el.textContent || '{}');
}

function schemaCompletions(context, keys) {
  const word = context.matchBefore(/[a-zA-Z0-9_.-]*/);
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

function init() {
  const data = readPageData();
  const urls = window.__AGENT_CONFIG_URLS__ || {};
  const restored = sessionStorage.getItem('agentConfigRestoreYaml');
  if (restored) {
    sessionStorage.removeItem('agentConfigRestoreYaml');
    data.initialYaml = restored;
  }

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
      const spec_yaml = view.state.doc.toString();
      const mutation = JSON.stringify(formToMutation(form));
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

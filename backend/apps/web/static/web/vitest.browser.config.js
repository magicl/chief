/**
 * Licensed under the Apache License, Version 2.0 (the "License");
 * Copyright 2024 Øivind Loe
 * See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
 * ~
 **/
import { execFileSync } from 'node:child_process';
import { existsSync, readFileSync, statSync } from 'node:fs';
import { dirname, extname, join, resolve, sep } from 'node:path';
import process from 'node:process';
import { fileURLToPath } from 'node:url';
import { playwright } from '@vitest/browser-playwright';
import { defineConfig } from 'vitest/config';
import { createTemporaryBuild } from './temporary_build.mjs';

const packageDirectory = dirname(fileURLToPath(import.meta.url));
const probeRequests = [];
const { cleanup: cleanupBundle, directory: bundleDirectory } = createTemporaryBuild((directory) => {
  execFileSync('pnpm', ['run', 'build:rich-content'], {
    cwd: packageDirectory,
    env: { ...process.env, CHIEF_RICH_CONTENT_OUTDIR: directory },
    stdio: 'inherit',
  });
});

/** Return a browser content type for generated modules and their dependent assets. */
function contentType(filePath) {
  return (
    {
      '.css': 'text/css',
      '.js': 'text/javascript',
      '.ttf': 'font/ttf',
      '.woff': 'font/woff',
      '.woff2': 'font/woff2',
    }[extname(filePath)] ?? 'application/octet-stream'
  );
}

/** Serve only the isolated build and lifecycle bridge needed by the browser smoke. */
const richContentAssets = {
  name: 'chief-rich-content-browser-assets',
  configureServer(server) {
    server.httpServer?.once('close', cleanupBundle);
    server.middlewares.use((request, response, next) => {
      const requestPath = decodeURIComponent((request.url ?? '').split('?')[0]);
      if (requestPath.startsWith('/network-probe/')) {
        probeRequests.push(requestPath);
        response.statusCode = 204;
        response.end();
        return;
      }
      if (requestPath === '/rich-content-probe-log') {
        if (request.method === 'DELETE') {
          probeRequests.length = 0;
          response.statusCode = 204;
          response.end();
        } else {
          response.setHeader('Content-Type', 'application/json');
          response.end(JSON.stringify(probeRequests));
        }
        return;
      }
      let filePath;
      if (requestPath === '/rich-content-source/rich_content_lifecycle.js') {
        filePath = join(packageDirectory, 'rich_content_lifecycle.js');
      } else if (requestPath.startsWith('/rich-content-bundle/')) {
        const relativePath = requestPath.slice('/rich-content-bundle/'.length);
        const candidate = resolve(bundleDirectory, relativePath);
        if (candidate.startsWith(`${bundleDirectory}${sep}`)) {
          filePath = candidate;
        }
      }
      if (!filePath || !existsSync(filePath) || !statSync(filePath).isFile()) {
        next();
        return;
      }
      response.statusCode = 200;
      response.setHeader('Content-Type', contentType(filePath));
      response.end(readFileSync(filePath));
    });
  },
};

export default defineConfig({
  plugins: [richContentAssets],
  test: {
    include: ['rich_content.browser.test.js'],
    browser: {
      enabled: true,
      headless: true,
      provider: playwright(),
      instances: [{ browser: 'chromium' }],
    },
  },
});

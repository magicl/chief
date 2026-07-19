import { execFileSync } from 'node:child_process';
import {
  copyFileSync,
  mkdirSync,
  mkdtempSync,
  readdirSync,
  renameSync,
  rmSync,
  statSync,
  writeFileSync,
} from 'node:fs';
import { tmpdir } from 'node:os';
import { basename, dirname, join, relative, resolve, sep } from 'node:path';
import process from 'node:process';
import { fileURLToPath } from 'node:url';

const packageDirectory = dirname(fileURLToPath(import.meta.url));
const outputDirectory = resolve(
  process.env.CHIEF_RICH_CONTENT_OUTDIR ?? '/mnt/infra-assets/chief/js/gen',
);
const stagingDirectory = mkdtempSync(join(tmpdir(), 'chief-rich-content-build-'));
const fixedEntryFiles = new Set(['rich_content.bundle.css', 'rich_content.bundle.js']);
const staleDependencyRetentionMs = 24 * 60 * 60 * 1000;

/** Return every file below a directory as a normalized relative path. */
function listFiles(directory, currentDirectory = directory) {
  const files = [];
  for (const name of readdirSync(currentDirectory)) {
    const absolutePath = join(currentDirectory, name);
    if (statSync(absolutePath).isDirectory()) {
      files.push(...listFiles(directory, absolutePath));
    } else {
      files.push(relative(directory, absolutePath).split(sep).join('/'));
    }
  }
  return files;
}

/** Publish one file with an atomic rename while preserving the mounted output directory. */
function publishFile(relativePath) {
  const sourcePath = join(stagingDirectory, relativePath);
  const destinationPath = join(outputDirectory, relativePath);
  mkdirSync(dirname(destinationPath), { recursive: true });
  const temporaryPath = join(
    dirname(destinationPath),
    `.${basename(destinationPath)}.publishing-${process.pid}`,
  );
  copyFileSync(sourcePath, temporaryPath);
  renameSync(temporaryPath, destinationPath);
}

/**
 * Remove obsolete entries immediately but retain old hashed dependencies for cached pages.
 * Dependency age is measured from mtime and must exceed 24 hours before collection.
 */
function removeStaleFiles(freshFiles, currentDirectory = outputDirectory, now = Date.now()) {
  for (const name of readdirSync(currentDirectory)) {
    const absolutePath = join(currentDirectory, name);
    const relativePath = relative(outputDirectory, absolutePath).split(sep).join('/');
    if (statSync(absolutePath).isDirectory()) {
      removeStaleFiles(freshFiles, absolutePath, now);
      if (readdirSync(absolutePath).length === 0) {
        rmSync(absolutePath, { recursive: true });
      }
    } else if (!freshFiles.has(relativePath)) {
      const retainedDependency =
        relativePath.startsWith('assets/') &&
        now - statSync(absolutePath).mtimeMs <= staleDependencyRetentionMs;
      if (!retainedDependency) {
        rmSync(absolutePath);
      }
    }
  }
}

try {
  execFileSync(
    'pnpm',
    [
      'exec',
      'esbuild',
      'rich_content.js',
      '--bundle',
      '--format=esm',
      '--splitting',
      `--outdir=${stagingDirectory}`,
      '--entry-names=rich_content.bundle',
      '--chunk-names=assets/[name]-[hash]',
      '--asset-names=assets/[name]-[hash]',
      '--loader:.woff=file',
      '--loader:.woff2=file',
      '--loader:.ttf=file',
      '--legal-comments=external',
      '--minify',
    ],
    { cwd: packageDirectory, stdio: 'inherit' },
  );

  mkdirSync(outputDirectory, { recursive: true });
  const stagedFiles = listFiles(stagingDirectory);
  const dependencyFiles = stagedFiles.filter((path) => !fixedEntryFiles.has(path));
  const entryFiles = ['rich_content.bundle.css', 'rich_content.bundle.js'].filter((path) =>
    stagedFiles.includes(path),
  );
  const publicationOrder = [...dependencyFiles, ...entryFiles];
  for (const relativePath of publicationOrder) {
    publishFile(relativePath);
  }
  if (process.env.CHIEF_RICH_CONTENT_PUBLISH_LOG) {
    writeFileSync(process.env.CHIEF_RICH_CONTENT_PUBLISH_LOG, `${publicationOrder.join('\n')}\n`);
  }
  removeStaleFiles(new Set(stagedFiles));
} finally {
  rmSync(stagingDirectory, { recursive: true, force: true });
}

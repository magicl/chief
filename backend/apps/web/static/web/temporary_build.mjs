import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import process from 'node:process';

/**
 * Create an isolated browser build lane and guarantee cleanup if its build callback throws.
 * Successful callers own the returned idempotent cleanup function until their server closes.
 *
 * @param {(directory: string) => void} executeBuild
 * @returns {{directory: string, cleanup: () => void}}
 */
export function createTemporaryBuild(executeBuild) {
  const directory = mkdtempSync(join(tmpdir(), 'chief-rich-content-browser-'));
  let cleaned = false;
  const cleanup = () => {
    if (cleaned) {
      return;
    }
    cleaned = true;
    process.removeListener('exit', cleanup);
    rmSync(directory, { recursive: true, force: true });
  };

  // Register before invoking caller code so synchronous setup failures cannot leak the directory.
  process.once('exit', cleanup);
  try {
    executeBuild(directory);
    return { cleanup, directory };
  } catch (cause) {
    cleanup();
    throw cause;
  }
}

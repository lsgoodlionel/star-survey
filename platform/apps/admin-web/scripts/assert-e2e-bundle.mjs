import { readdir, readFile } from 'node:fs/promises';
import { extname, join } from 'node:path';
import process from 'node:process';
import { fileURLToPath, URL } from 'node:url';

const required = ['/dev/token', '端到端测试登录', '测试令牌'];
const textExtensions = new Set(['.css', '.html', '.js', '.json', '.map', '.svg', '.txt']);

async function listFiles(directory) {
  const entries = await readdir(directory, { withFileTypes: true });
  const nested = await Promise.all(entries.map((entry) => {
    const path = join(directory, entry.name);
    return entry.isDirectory() ? listFiles(path) : [path];
  }));
  return nested.flat();
}

const files = (await listFiles(fileURLToPath(new URL('../dist', import.meta.url))))
  .filter((file) => textExtensions.has(extname(file)));
const contents = (await Promise.all(files.map((file) => readFile(file, 'utf8')))).join('\n');
const missing = required.filter((marker) => !contents.includes(marker));

if (missing.length) {
  process.stderr.write(`E2E bundle is missing token entry markers: ${missing.join(', ')}\n`);
  process.exitCode = 1;
} else {
  process.stdout.write(`E2E bundle contains the test-only token UI (${files.length} files checked).\n`);
}

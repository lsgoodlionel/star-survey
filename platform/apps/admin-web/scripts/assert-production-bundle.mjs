import { readdir, readFile } from 'node:fs/promises';
import { extname, join } from 'node:path';
import process from 'node:process';
import { fileURLToPath, URL } from 'node:url';

const forbidden = ['/dev/token', '开发令牌', '开发环境登录', '测试令牌', '端到端测试登录'];
const textExtensions = new Set(['.css', '.html', '.js', '.json', '.map', '.svg', '.txt']);

async function listFiles(directory) {
  const entries = await readdir(directory, { withFileTypes: true });
  const nested = await Promise.all(
    entries.map((entry) => {
      const path = join(directory, entry.name);
      return entry.isDirectory() ? listFiles(path) : [path];
    }),
  );
  return nested.flat();
}

const files = (await listFiles(fileURLToPath(new URL('../dist', import.meta.url)))).filter((file) =>
  textExtensions.has(extname(file)),
);

for (const file of files) {
  const contents = await readFile(file, 'utf8');
  const match = forbidden.find((value) => contents.includes(value));
  if (match) {
    process.stderr.write(
      `Production bundle contains forbidden development token marker ${JSON.stringify(match)} in ${file}\n`,
    );
    process.exitCode = 1;
  }
}

if (!process.exitCode) {
  process.stdout.write(`Production bundle excludes development token UI (${files.length} files checked).\n`);
}

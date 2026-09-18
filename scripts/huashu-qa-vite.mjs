import { pathToFileURL } from 'node:url';
import path from 'node:path';
const [desktop, runRoot] = process.argv.slice(2);
const { createServer } = await import(pathToFileURL(path.join(desktop, 'node_modules/vite/dist/node/index.js')).href);
const server = await createServer({ root: desktop, cacheDir: path.join(runRoot, 'vite-cache'), configLoader: 'runner',
  server: { host: '127.0.0.1', port: 1420, strictPort: true } });
await server.listen();

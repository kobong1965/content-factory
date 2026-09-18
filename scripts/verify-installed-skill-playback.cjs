// Read-only against installed API; no generation, uploads, edits or paid requests.
const path = require('node:path');
const fs = require('node:fs/promises');
const assert = require('node:assert/strict');
const { pathToFileURL } = require('node:url');
const root = path.resolve(__dirname, '..');
const output = process.argv[2];
assert(path.resolve(output).startsWith(path.resolve('E:/Codex工作盘') + path.sep));
(async () => {
  await fs.mkdir(output, { recursive: true });
  const { createServer } = await import(pathToFileURL(path.join(root, 'apps/desktop/node_modules/vite/dist/node/index.js')).href);
  const server = await createServer({ root: path.join(root, 'apps/desktop'), cacheDir: path.join(output, 'vite-cache'), configLoader: 'runner', server: { host: '127.0.0.1', port: 1420, strictPort: true } });
  await server.listen();
  const { chromium } = require('E:/Codex工作盘/caches/npm-playwright-cli/_npx/31e32ef8478fbf80/node_modules/playwright');
  const browser = await chromium.launch({ channel: 'chrome', headless: true });
  const page = await browser.newPage({ viewport: { width: 1707, height: 960 } });
  const result = [];
  try {
    await page.route('http://127.0.0.1:8766/**', route => { assert.equal(route.request().method(), 'GET'); return route.continue(); });
    await page.goto('http://127.0.0.1:1420');
    await page.getByRole('button', { name: '查看详情', exact: true }).first().waitFor();
    const count = await page.getByRole('button', { name: '查看详情', exact: true }).count();
    assert.equal(count, 13);
    for (let i = 0; i < count; i++) {
      const cover = page.locator('.creator-skill-card').nth(i).locator('.skill-cover img');
      await cover.scrollIntoViewIfNeeded();
      await cover.evaluate(img => img.decode());
      assert(await cover.evaluate(img => img.naturalWidth > 0 && img.naturalHeight > 0));
      await page.getByRole('button', { name: '查看详情', exact: true }).nth(i).click();
      const dialog = page.getByRole('dialog'); const video = dialog.locator('video');
      await page.waitForFunction(() => { const v = document.querySelector('.skill-playback-dialog video'); return v && v.readyState >= 2 && v.duration > 0; }, { timeout: 15000 });
      await video.evaluate(v => { v.muted = true; return v.play(); });
      await page.waitForFunction(() => document.querySelector('.skill-playback-dialog video').currentTime > .3);
      await video.evaluate(v => v.pause());
      const before = await video.boundingBox(); await dialog.locator('.creator-chapters').evaluate(e => { e.scrollTop = 300; });
      assert.equal((await video.boundingBox()).y, before.y);
      const name = await dialog.locator('h2').innerText();
      const detail = await video.evaluate(v => ({ duration: v.duration, time: v.currentTime, width: v.videoWidth, height: v.videoHeight, source: v.currentSrc }));
      assert(detail.width > 0 && detail.height > 0);
      if (i === 0) { await dialog.getByRole('button', { name: /00:12.0 播放此段/ }).click(); await page.waitForFunction(() => Math.abs(document.querySelector('.skill-playback-dialog video').currentTime - 12) < .2); await page.screenshot({ path: path.join(output, 'installed-real-4.20-J85.png') }); }
      result.push({ name, ...detail, coverDecoded: true, passed: true });
      await dialog.getByRole('button', { name: '返回', exact: true }).click();
    }
    console.log(JSON.stringify(result, null, 2));
  } finally { await fs.writeFile(path.join(output, 'results.json'), JSON.stringify(result, null, 2)); await browser.close(); await server.close(); }
})().catch(e => { console.error(e); process.exitCode = 1; });

// Runs only against verify-s5-dialog.ps1's isolated services; no paid calls.
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const { execFileSync } = require('node:child_process');
const args = process.argv.slice(2);
const arg = n => args[args.indexOf(n) + 1];
const url = arg('--url'), api = arg('--api-url'), output = path.resolve(arg('--output'));
assert.equal(new URL(api).port, '18767');
assert(output.startsWith(path.resolve('E:/Codex工作盘') + path.sep));
const { chromium } = require(arg('--playwright-module'));
const results = [], errors = [];
async function drop(page, locator, files) {
  const transfer = await page.evaluateHandle(files => { const dt = new DataTransfer(); for (const f of files) dt.items.add(new File([Uint8Array.from(atob(f.data), c => c.charCodeAt(0))], f.name, { type: f.type })); return dt; }, files);
  await locator.dispatchEvent('drop', { dataTransfer: transfer }); await transfer.dispose();
}
async function json(route) { const r = await fetch(`${api}/s4/${route}`); assert(r.ok); return r.json(); }
(async () => {
  await fs.mkdir(output, { recursive: true });
  const videoPath = path.join(output, 'synthetic-playback.mp4');
  execFileSync('ffmpeg', ['-hide_banner', '-loglevel', 'error', '-f', 'lavfi', '-i', 'testsrc2=size=270x480:rate=15', '-t', '12', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-movflags', '+faststart', videoPath]);
  const videoBytes = await fs.readFile(videoPath);
  const browser = await chromium.launch({ channel: 'chrome', headless: true });
  const page = await browser.newPage({ viewport: { width: 1920, height: 1080 } });
  page.on('pageerror', e => errors.push(e.message));
  try {
    await page.goto(url); await page.getByRole('heading', { name: '创作工作台', exact: true }).waitFor();
    assert.equal(await page.getByText('旧版功能', { exact: true }).count(), 0, 'new navigation must not expose the duplicate legacy group');
    const png = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAFgAI/ScLbtAAAAABJRU5ErkJggg==';
    await drop(page, page.locator('.creator-upload .file-drop-input'), [{ name: 'QA正面.png', type: 'image/png', data: png }, { name: 'QA背面.png', type: 'image/png', data: png }]);
    await page.getByText(/待保存 2 张/).waitFor();
    await page.getByLabel('商品名称', { exact: true }).fill('QA 多图复用裤子');
    await page.getByRole('button', { name: '上传并保存商品', exact: true }).click();
    await page.getByRole('dialog', { name: '确认商品卖点', exact: true }).waitFor();
    await page.getByRole('button', { name: '返回创作工作台', exact: true }).click();
    const all = await json('products'); const product = all.find(p => p.name === 'QA 多图复用裤子'); assert(product);
    const profile = await json(`products/${product.product_id}`); assert.equal(profile.sources.length, 1, 'identical images are deduplicated');
    await page.getByLabel('补充商品资料', { exact: true }).fill('QA 长期资料；颜色需确认；0012 不应丢零');
    await page.getByLabel(/本次使用：/).first().check();
    await page.getByLabel('设为主图', { exact: true }).first().check();
    await page.getByRole('button', { name: '保存资料与素材选择', exact: true }).click();
    await page.getByText(/资料与勾选已保存/).waitFor();
    let workspace = await json(`products/${product.product_id}/workspace`); assert.equal(workspace.selected_asset_ids.length, 1); assert(workspace.notes.includes('0012'));
    await page.reload(); await page.getByLabel('补充商品资料', { exact: true }).waitFor();
    await page.waitForFunction(() => document.querySelector('.product-assets textarea')?.value.includes('0012'));
    assert(await page.getByLabel(/本次使用：/).first().isChecked());
    await page.getByLabel(/本次使用：/).first().uncheck(); await page.getByRole('button', { name: '保存资料与素材选择', exact: true }).click(); await page.getByText(/资料与勾选已保存/).waitFor();
    assert.equal((await json(`products/${product.product_id}`)).sources.length, 1, 'unchecking does not delete the asset');
    results.push({ case: 'multi-image DOM drop, deduplication, notes + selection save/reopen', passed: true });
    await page.getByLabel('补充商品资料', { exact: true }).fill('QA 未保存内容应保留');
    await page.locator('.creator-upload-section summary').click();
    assert(await page.locator('.creator-upload input[type=file]').isDisabled(), 'dirty product cannot be replaced by creating another product');
    await page.route(`${api}/s4/products/*/workspace`, async route => route.request().method() === 'PUT' ? route.fulfill({ status: 503, json: { detail: 'QA 模拟保存失败' } }) : route.continue());
    await page.getByRole('button', { name: '保存资料与素材选择', exact: true }).click(); await page.getByText('QA 模拟保存失败').waitFor();
    assert.equal(await page.getByLabel('补充商品资料', { exact: true }).inputValue(), 'QA 未保存内容应保留');
    await page.unroute(`${api}/s4/products/*/workspace`); await page.getByRole('button', { name: '保存资料与素材选择', exact: true }).click(); await page.getByText(/资料与勾选已保存/).waitFor();
    results.push({ case: 'failed save preserves draft and retry persists', passed: true });
    await drop(page, page.locator('.creator-upload .file-drop-input'), [{ name: 'QA损坏.png', type: 'image/png', data: Buffer.from('not an image').toString('base64') }]);
    await page.getByLabel('商品名称', { exact: true }).fill('QA 部分失败恢复款');
    await page.getByRole('button', { name: '上传并保存商品', exact: true }).click();
    await page.getByText(/尚未完成，未上传图片已保留/).waitFor();
    assert.equal(await page.locator('.creator-upload input[type=file]').isDisabled(), false, 'bad image can be replaced');
    const countAfterFailure = (await json('products')).length;
    await drop(page, page.locator('.creator-upload .file-drop-input'), [{ name: 'QA替换.png', type: 'image/png', data: png }]);
    // Force the detail reload (not upload) to fail and confirm no old-product editor opens.
    await page.route(`${api}/s4/products/*/versions`, route => route.fulfill({ status: 503, json: { detail: 'QA 重新读取失败' } }));
    await page.getByRole('button', { name: '继续保存 / 重新读取本款', exact: true }).click();
    await page.getByText(/商品已保存，但重新读取失败/).waitFor();
    assert.equal(await page.getByRole('dialog', { name: '确认商品卖点', exact: true }).count(), 0);
    await page.unroute(`${api}/s4/products/*/versions`);
    await page.getByRole('button', { name: '继续保存 / 重新读取本款', exact: true }).click();
    await page.getByRole('dialog', { name: '确认商品卖点', exact: true }).waitFor();
    await page.getByRole('button', { name: '返回创作工作台', exact: true }).click();
    assert.equal((await json('products')).length, countAfterFailure, 'retry never creates a second product');
    results.push({ case: 'bad upload replace/retry, failed reload does not open old product, no duplicate product', passed: true });
    await page.getByRole('button', { name: '导入商品表格（XLSX / CSV）', exact: true }).click();
    const sheet = page.getByRole('dialog', { name: '导入商品表格', exact: true });
    await drop(page, sheet.locator('.file-drop-input'), [{ name: 'QA商品.csv', type: 'text/csv', data: Buffer.from('商品名称,款号,资料\nQA 表格款,0012,黑色待确认').toString('base64') }]);
    await sheet.getByRole('button', { name: '读取表格预览' }).click(); await sheet.getByLabel('导入第1行').check();
    await sheet.getByRole('button', { name: '保存勾选的 1 个商品' }).click(); await sheet.getByText(/已保存 1 个商品资料草稿/).waitFor();
    assert((await json('products')).some(p => p.sku === '0012'));
    await sheet.getByRole('button', { name: '关闭', exact: true }).click();
    results.push({ case: 'CSV DOM drop, preview, selected import and persisted SKU', passed: true });
    await page.screenshot({ path: path.join(output, 'product-persisted.png'), fullPage: true });
    // The seeded legacy QA Skill has no real video. Intercept only template timing/media
    // for browser decoding/layout; real binding/Range is separately HTTP tested.
    await page.route(`${api}/s5/templates`, async route => { const response = await route.fetch(); const rows = await response.json(); rows[0].distinct_video_count = 1; rows[0].steps = Array.from({ length: 18 }, (_, i) => ({ id: `qa_${i}`, order: i + 1, description: `${i === 0 ? '00:00.000—00:04.000' : i === 1 ? '00:04.000—00:08.000' : '00:08.000—00:12.000'} QA 固定直播间展示长文本，检验独立滚动与时间定位。`.repeat(2) })); await route.fulfill({ response, json: rows }); });
    await page.route(`${api}/s5/skills/*/sources/*/media`, route => {
      const range = route.request().headers().range?.match(/bytes=(\d+)-(\d*)/);
      if (!range) return route.fulfill({ status: 200, contentType: 'video/mp4', headers: { 'Accept-Ranges': 'bytes' }, body: videoBytes });
      const start = +range[1], end = range[2] ? Math.min(+range[2], videoBytes.length - 1) : videoBytes.length - 1;
      return route.fulfill({ status: 206, contentType: 'video/mp4', headers: { 'Accept-Ranges': 'bytes', 'Content-Range': `bytes ${start}-${end}/${videoBytes.length}` }, body: videoBytes.subarray(start, end + 1) });
    });
    // Synthetic image is explicitly limited to isolated UI geometry tests; production
    // cover provenance and actual source images are verified by separate API checks.
    await page.route(`${api}/s5/skills/*/sources/*/cover`, route => route.fulfill({ status: 200, contentType: 'image/png', body: Buffer.from(png, 'base64') }));
    for (const [width, height, mode] of [[2560,1440,'normal'],[1920,1080,'normal'],[2048,1152,'normal'],[1707,960,'normal'],[960,640,'normal'],[1280,800,'text200']]) {
      await page.setViewportSize({ width, height }); await page.reload();
      const cover = page.locator('.creator-skill-card img').first();
      await cover.waitFor();
      await cover.evaluate(img => new Promise((resolve, reject) => { if (img.complete) return img.naturalWidth ? resolve() : reject(new Error('cover failed decoding')); img.addEventListener('load', resolve, { once: true }); img.addEventListener('error', reject, { once: true }); }));
      const coverBox = await cover.boundingBox(); assert(coverBox.width >= 64 && coverBox.height >= 80, 'card needs a recognizable video cover');
      await page.getByRole('button', { name: '查看详情', exact: true }).first().click();
      const dialog = page.getByRole('dialog'); const video = dialog.locator('video');
      if (mode === 'text200') await page.addStyleTag({ content: '.modal-dialog { font-size: 200%; } .modal-dialog p,.modal-dialog li,.modal-dialog button,.modal-dialog label { font-size: inherit; }' });
      await video.evaluate(v => new Promise((resolve, reject) => { if (v.readyState >= 1) return resolve(); v.addEventListener('loadedmetadata', resolve, { once: true }); v.addEventListener('error', reject, { once: true }); }));
      const step = dialog.getByRole('button', { name: '00:04.0 播放此段' });
      const stepBox = await step.boundingBox();
      assert(stepBox.height >= 64, 'the full script paragraph must be the click target, not just its timestamp');
      const frame = await step.evaluate(e => { const s = getComputedStyle(e.closest('li')); return { border:parseFloat(s.borderTopWidth), style:s.borderTopStyle }; });
      assert(frame.border >= 1 && frame.style !== 'none', 'every step needs a visible frame before it is active');
      await step.click({ position: { x: Math.min(stepBox.width - 12, 120), y: stepBox.height - 12 } });
      await page.waitForFunction(() => Math.abs(document.querySelector('.skill-playback-dialog video').currentTime - 4) < .2);
      assert(Math.abs(await video.evaluate(v => v.currentTime) - 4) < .2);
      await dialog.locator('.creator-source-tabs button').first().click();
      const sourceGeometry = await dialog.locator('.creator-source-tabs').evaluate(e => {
        const r = e.getBoundingClientRect(), buttons = [...e.querySelectorAll('button')].map(b => { const x = b.getBoundingClientRect(); return { top:x.top, bottom:x.bottom, height:x.height }; });
        const heading = [...e.parentElement.querySelectorAll('h3')].find(h => h.textContent === '原话');
        return { top:r.top, bottom:r.bottom, height:r.height, scrollHeight:e.scrollHeight, clientHeight:e.clientHeight, buttons, headingTop:heading?.getBoundingClientRect().top };
      });
      assert(sourceGeometry.height >= 40, `source selector collapsed: ${JSON.stringify(sourceGeometry)}`);
      assert(sourceGeometry.buttons.every(b => b.height >= 36 && b.bottom <= sourceGeometry.bottom + 1 && b.top >= sourceGeometry.top - 1), `source choices clipped: ${JSON.stringify(sourceGeometry)}`);
      assert(sourceGeometry.headingTop >= sourceGeometry.bottom - 1, `dialogue heading overlaps source choices: ${JSON.stringify(sourceGeometry)}`);
      assert.equal(await dialog.getByLabel('素材时间线', { exact: true }).isDisabled(), false, 'same source stays seekable');
      await dialog.getByRole('button', { name: '00:04.0 播放此段' }).click();
      await video.evaluate(v => { v.muted = true; return v.play(); });
      await page.waitForFunction(() => document.querySelector('.skill-playback-dialog video').currentTime > 4.2);
      await video.evaluate(v => v.pause());
      const before = await video.boundingBox();
      await dialog.locator('.creator-chapters').evaluate(e => { e.scrollTop = 500; });
      const after = await video.boundingBox(); assert.equal(before.y, after.y);
      const geometry = await dialog.evaluate(e => { const r = e.getBoundingClientRect(); return { bottom:r.bottom, height:innerHeight, overflow:e.scrollWidth-e.clientWidth, body:e.querySelector('.modal-dialog-body').getBoundingClientRect().height }; });
      assert(geometry.bottom <= height + 1 && geometry.overflow <= 1 && geometry.body > 100, JSON.stringify(geometry));
      await page.screenshot({ path: path.join(output, `player-${width}x${height}-${mode}.png`) });
      await dialog.getByRole('button', { name: '返回', exact: true }).click();
      results.push({ case: `play seek independent-scroll ${width}x${height} CSS viewport (not OS scaling)`, passed: true });
    }
    assert.deepEqual(errors, []);
  } catch (e) { results.push({ passed: false, error: e.stack }); await page.screenshot({ path: path.join(output, 'failure.png'), fullPage: true }); throw e; }
  finally { await fs.writeFile(path.join(output, 'results.json'), JSON.stringify({ results, errors }, null, 2)); await browser.close(); }
  console.log(JSON.stringify(results, null, 2));
})().catch(e => { console.error(e); process.exitCode = 1; });

// Browser acceptance uses only an inert, mocked project; never starts inference.
const fs = require('node:fs/promises');
const path = require('node:path');
const assert = require('node:assert/strict');
const arg = name => process.argv[process.argv.indexOf(name) + 1];

async function main() {
  const output = path.resolve(arg('--output'));
  const api = arg('--api-url');
  assert.equal(new URL(api).origin, 'http://127.0.0.1:18767');
  assert.equal(new URL(arg('--url')).origin, 'http://127.0.0.1:1420');
  const relative = path.relative('E:/Codex工作盘/temp', output);
  assert(relative && !relative.startsWith('..') && !path.isAbsolute(relative));
  assert(process.env.CONTENT_FACTORY_S7_DATA_DIR.startsWith(path.dirname(output)), 'Use an isolated QA profile');
  await fs.mkdir(output, { recursive: true });
  const checkpoint = { manifest_path: 'outputs/qa/manifest.json', manifest_sha256: 'a'.repeat(64) };
  const source = { file_name: '模拟主播录播.mp4', duration_ms: 300000, sha256: 'b'.repeat(64) };
  let project = {
    project_id: 'auto_edit_browser_registration_034', title: '模拟登记失败 · J85', sku: 'J85',
    status: 'failed', revision: 9, progress: 95, error: '422: 第 4 条内部编号不合法；测试数据，无真实任务。',
    registration_checkpoint: checkpoint, output_batch_id: null, source, sources: [{ ...source, source_id: 'source_01' }],
    settings: { target_count: 5, duration_min_ms: 20000, duration_max_ms: 40000, subtitle_font_size: 68, keyword_color: '#FFD400', keyword_scale: 1.3, top_title_enabled: false },
    selected_skill: { snapshot: { skill_id: 'qa_skill', revision: 1, name: '模拟剪辑方法', mechanism: '仅用于浏览器验收' }, reason: '演示数据，不调用模型' },
    analysis_summary: '测试方案已保存',
    plan: [{ candidate_id: 'candidate_01', title: '模拟成片 · 裤脚与版型', duration_ms: 25000, clips: [{ start_ms: 1000, end_ms: 26000 }] }],
    created_at: '2026-09-22T00:00:00Z', updated_at: '2026-09-22T00:00:00Z',
  };
  const { chromium } = require(arg('--playwright-module'));
  const browser = await chromium.launch({ channel: 'chrome', headless: true });
  const errors = [], forbiddenWrites = [], retryBodies = [];
  const page = await browser.newPage({ viewport: { width: 1920, height: 1080 } });
  page.on('pageerror', error => errors.push(error.message));
  await page.route(`${api}/**`, async route => {
    const request = route.request();
    const pathname = new URL(request.url()).pathname;
    if (request.method() === 'GET' && pathname === '/s7/auto-edit-projects') return route.fulfill({ json: { projects: [project] } });
    if (request.method() === 'POST' && pathname === `/s7/auto-edit-projects/${project.project_id}/retry`) {
      retryBodies.push(request.postDataJSON());
      await new Promise(resolve => setTimeout(resolve, 350));
      project = { ...project, status: 'render_pending', revision: 10, error: null };
      return route.fulfill({ json: project });
    }
    if (!['GET', 'HEAD', 'OPTIONS'].includes(request.method())) {
      forbiddenWrites.push(`${request.method()} ${pathname}`);
      return route.abort('blockedbyclient');
    }
    return route.continue();
  });
  try {
    await page.goto(arg('--url'), { waitUntil: 'networkidle' });
    const retry = page.getByRole('button', { name: '重试登记', exact: true });
    await retry.waitFor();
    assert.equal(await page.getByRole('button', { name: '重新尝试', exact: true }).count(), 0);
    const failure = page.locator('.project-failure');
    assert((await failure.innerText()).includes('不会重新分析或剪辑'));
    const details = failure.locator('details');
    assert.equal(await details.getAttribute('open'), null);
    await retry.focus();
    await page.keyboard.press('Tab');
    const summary = details.locator('summary');
    assert(await summary.evaluate(element => document.activeElement === element));
    await page.keyboard.press('Enter');
    assert.notEqual(await details.getAttribute('open'), null);
    assert.equal(await failure.locator('pre').innerText(), project.error);
    const sizes = [{ width: 1920, height: 1080 }, { width: 800, height: 760 }];
    for (const viewport of sizes) {
      await page.setViewportSize(viewport);
      assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
      await retry.click({ trial: true });
      await page.screenshot({ path: path.join(output, `registration-retry-${viewport.width}.png`), fullPage: true });
    }
    await page.setViewportSize({ width: 1920, height: 1080 });
    await retry.click();
    assert(await retry.isDisabled(), 'Submitting registration disables repeat clicks');
    await page.getByRole('status').filter({ hasText: '将复用现有成片，不会重新调用模型' }).waitFor();
    assert.equal(retryBodies.length, 1);
    assert.deepEqual(retryBodies[0], { expected_revision: 9 });
    assert.equal(await page.locator('.auto-edit-plan').getByText('模拟成片 · 裤脚与版型', { exact: true }).count(), 1);
    assert.equal(await page.locator('.project-source-list li').count(), 1);
    await page.reload({ waitUntil: 'networkidle' });
    assert.equal(await page.locator('.auto-edit-plan').getByText('模拟成片 · 裤脚与版型', { exact: true }).count(), 1);
    // Old projects must still show the ordinary paid-retry warning.
    project = { ...project, status: 'failed', revision: 11, registration_checkpoint: null, error: 'unexpected worker response' };
    await page.reload({ waitUntil: 'networkidle' });
    await page.getByRole('button', { name: '重新尝试', exact: true }).waitFor();
    assert((await failure.innerText()).includes('重新尝试可能重新调用模型并产生费用'));
    assert.deepEqual(forbiddenWrites, []);
    assert.deepEqual(errors, []);
    await fs.writeFile(path.join(output, 'registration-retry-results.json'), JSON.stringify({
      passed: true, mockOnly: true, actualModelCalls: 0, retryBodies, errors, forbiddenWrites,
      checks: ['checkpoint-specific action and explanation', 'keyboard diagnostic expansion', '1920/800 no horizontal overflow', 'busy action disabled', 'revision-safe retry request', 'source and plan retained after response/reload', 'legacy warning retained'],
    }, null, 2));
  } catch (error) {
    await page.screenshot({ path: path.join(output, 'registration-retry-failure.png'), fullPage: true });
    throw error;
  } finally { await browser.close(); }
}
main().catch(error => { console.error(error); process.exitCode = 1; });

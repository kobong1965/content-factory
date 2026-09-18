/* Real Chromium + isolated FastAPI. Model/media are explicitly simulated. */
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const args = process.argv.slice(2);
const option = (name) => args[args.indexOf(name) + 1];
const root = path.resolve(option('--root'));
assert(root.startsWith(path.resolve('E:/Codex工作盘') + path.sep));
const { chromium } = require(option('--playwright-module'));
const api = 'http://127.0.0.1:18768';
const url = 'http://127.0.0.1:1420';
const output = path.join(root, 'evidence');
const results = [];
const errors = [];
const sleep = (ms) => new Promise(resolve => setTimeout(resolve, ms));
async function json(url) { const response = await fetch(url); assert(response.ok, url); return response.json(); }
async function poll(check, message) {
  for (let i = 0; i < 80; i++) { if (await check()) return; await sleep(250); }
  throw new Error(message);
}
async function main() {
  await fs.mkdir(output, { recursive: true });
  const manifest = JSON.parse(await fs.readFile(path.join(root, 'qa-manifest.json'), 'utf8'));
  assert.equal(manifest.fixture_data, true);
  const browser = await chromium.launch({ channel: 'chrome', headless: true });
  const page = await browser.newPage({ viewport: { width: 1707, height: 960 } });
  page.on('pageerror', err => errors.push(err.message));
  try {
    await page.goto(url);
    await page.getByRole('button', { name: /爆点研究/ }).click();
    await page.getByRole('button', { name: '分析一条爆款', exact: true }).waitFor();
    await page.locator('.analysis-task-select').first().click();
    await page.getByText('分析方法：基础爆点分析 · 1.2.0').waitFor();
    results.push('旧报告保持基础方法标签');
    await page.getByRole('button', { name: '分析一条爆款', exact: true }).click();
    const dialog = page.getByRole('dialog', { name: '新建分析任务' });
    await dialog.getByLabel('已完成本地处理的视频', { exact: true }).selectOption(manifest.media_task_id);
    await dialog.getByText('已启用 Huashu 七维爆点拆解', { exact: true }).waitFor();
    const dimensions = ['钩子分析', '分镜结构', '节奏设计', '视觉元素', '转化设计', '合规检查', '可复制要素'];
    for (const text of dimensions) assert((await dialog.innerText()).includes(text));
    const profiles = [[2560,1440,false,false],[1920,1080,false,false],[2048,1152,false,false],
      [1707,960,false,false],[960,640,false,false],[1280,800,true,false],[1280,800,false,true]];
    for (const [width,height,large,text200] of profiles) {
      await page.setViewportSize({ width, height });
      await page.evaluate(({ large, text200 }) => {
        const root = document.documentElement;
        root.dataset.uiScale = large ? 'large' : 'comfortable';
        const tokens = ['assist','label','nav','button','table','body','subtitle','section','title'];
        for (const key of tokens) root.style.removeProperty(`--font-${key}`);
        if (text200) {
          const values = tokens.map(key => parseFloat(getComputedStyle(root).getPropertyValue(`--font-${key}`)) * 2);
          tokens.forEach((key, i) => root.style.setProperty(`--font-${key}`, `${values[i]}px`));
        }
      }, { large, text200 });
      await page.evaluate(() => document.fonts.ready);
      const submit = dialog.getByRole('button', { name: '开始爆点研究智能体', exact: true });
      await submit.scrollIntoViewIfNeeded();
      const geometry = await dialog.evaluate(el => {
        const body = el.querySelector('.modal-dialog-body');
        const submit = el.querySelector('button[type="submit"]');
        const r = submit.getBoundingClientRect(); const b = body.getBoundingClientRect();
        return { width: innerWidth, height: innerHeight, scrollWidth: body.scrollWidth, clientWidth: body.clientWidth,
          submitTop: r.top, submitBottom: r.bottom, bodyTop: b.top, bodyBottom: b.bottom,
          fontSize: getComputedStyle(el.querySelector('p')).fontSize };
      });
      assert(geometry.scrollWidth <= geometry.clientWidth + 2, JSON.stringify(geometry));
      assert(geometry.submitTop >= geometry.bodyTop - 2 && geometry.submitBottom <= geometry.bodyBottom + 2);
      assert(geometry.submitBottom <= height && geometry.submitTop >= 0);
      await submit.click({ trial: true });
      const name = `${width}x${height}${large ? '-large' : ''}${text200 ? '-text200' : ''}`;
      await page.screenshot({ path: path.join(output, `${name}.png`) });
      results.push({ name, geometry, simulatedScaling: true });
    }
    await page.setViewportSize({width: 1707, height: 960});
    await page.evaluate(() => { document.documentElement.removeAttribute('style'); document.documentElement.dataset.uiScale = 'comfortable'; });
    // A simulated failure must preserve the selected video and allow a retry.
    await page.route(`${api}/s3/analyses`, route => route.fulfill({status:503, contentType:'application/json', body:JSON.stringify({detail:'QA 模拟临时失败'})}), {times:1});
    await dialog.getByRole('button', { name: '开始爆点研究智能体', exact: true }).click();
    await dialog.getByRole('alert').filter({hasText:'QA 模拟临时失败'}).waitFor({state:'visible',timeout:5000});
    assert.equal(await dialog.locator('#analysis-media').inputValue(), manifest.media_task_id);
    results.push('模拟创建失败后保留视频选择');
    const createdResponse = page.waitForResponse(r => r.url() === `${api}/s3/analyses` && r.status() === 202);
    await dialog.getByRole('button', { name: '开始爆点研究智能体', exact: true }).click();
    const created = await (await createdResponse).json();
    assert.notEqual(created.task_id, manifest.legacy_task_id);
    await poll(async () => (await json(`${api}/s3/tasks/${created.task_id}`)).status === 'succeeded', 'Analysis did not complete');
    await page.getByText('分析方法：Huashu 七维爆点拆解 · 适配版 1.0', {exact:true}).waitFor();
    const report = await json(`${api}/s3/tasks/${created.task_id}/report`);
    assert.equal(report.processing.prompt_version, '1.3.0');
    assert.equal(report.processing.upload_summary.original_video_uploaded, false);
    assert.equal(report.shots.length, 3);
    const input = JSON.parse(await fs.readFile(path.join(root,'analysis','tasks',created.task_id,'input.json'),'utf8'));
    assert.equal(input._analysis_method_snapshot.instructions_sha256, manifest.method.instructions_sha256);
    const edit = 'QA 人工修订：仅用于验证七维报告保存、刷新和重新打开。';
    await page.locator('#overall-conclusion').fill(edit);
    const saved = page.waitForResponse(r => r.url().endsWith('/report') && r.request().method() === 'PATCH');
    await page.getByRole('button', {name:'保存人工修订', exact:true}).click();
    assert.equal((await saved).status(), 200);
    await page.reload();
    await page.getByRole('button', {name:/爆点研究/}).click();
    await page.locator('.analysis-task-select').first().click();
    await poll(async () => (await page.locator('#overall-conclusion').inputValue()) === edit, 'Saved content lost after reopening');
    const reopened = await json(`${api}/s3/tasks/${created.task_id}/report`);
    assert.equal(reopened.revision, report.revision + 1);
    assert.equal(reopened.processing.prompt_version,'1.3.0');
    const calls = JSON.parse(await fs.readFile(path.join(root,'model-calls.json'),'utf8'));
    assert.equal(calls.filter(c => c.method === 'huashu').length,4);
    results.push('真实 API 创建→后台分段处理→方法快照/报告落盘→人工保存→刷新重开；模型响应模拟');
    await page.screenshot({path:path.join(output,'new-report.png')});
    assert.equal(errors.length,0,errors.join('\n'));
    await fs.writeFile(path.join(output,'result.json'),JSON.stringify({passed:true,results,errors,taskId:created.task_id,realCloudTest:false},null,2));
    console.log(`Passed ${results.length} checks. Evidence: ${output}`);
  } catch(error) {
    await page.screenshot({path:path.join(output,'failure.png')});
    await fs.writeFile(path.join(output,'failure.json'),JSON.stringify({results,errors,error:String(error)},null,2));
    throw error;
  } finally { await browser.close(); }
}
main().catch(error => { console.error(error); process.exitCode=1; });

/* Real Chromium regression. Requires the isolated seed-s5-qa.py API and Vite.
 * See docs/qa/2026-09-09-s5-dialog-scroll-0.1.13.md for the complete command.
 * No production data or paid model calls are used by this test.
 */
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');

const args = process.argv.slice(2);
const option = (name, fallback) => args.includes(name) ? args[args.indexOf(name) + 1] : fallback;
const url = option('--url', 'http://127.0.0.1:1420');
const api = option('--api-url', 'http://127.0.0.1:8767');
const output = path.resolve(option('--output', 'E:/Codex工作盘/artifacts/test-builds/男装编剪器-0.1.13/dialog'));
const storage = path.resolve('E:/Codex工作盘') + path.sep;
assert(output.toLowerCase().startsWith(storage.toLowerCase()), 'Evidence must be stored under E:/Codex工作盘');
for (const address of [url, api]) {
  const parsed = new URL(address);
  assert.equal(parsed.hostname, '127.0.0.1', 'Only the isolated localhost QA services are allowed');
  assert.notEqual(parsed.port, '8766', 'Never run mutation tests against the installed user API');
}
const { chromium } = require(option('--playwright-module', 'playwright'));
const results = [];
const errors = [];
const profiles = [
  { name: 'reported-2173x1240', width: 2173, height: 1240 },
  { name: '2k-2560x1440', width: 2560, height: 1440 },
  { name: '1080p-1920x1080', width: 1920, height: 1080 },
  { name: '125pct-equivalent-2048x1152', width: 2048, height: 1152 },
  { name: '150pct-equivalent-1707x960', width: 1707, height: 960 },
  { name: 'small-960x540', width: 960, height: 540 },
  { name: 'large-font-1280x800', width: 1280, height: 800, scale: 'large' },
  { name: 'long-evidence-1280x800', width: 1280, height: 800, longEvidence: true },
  { name: 'text200-simulation-1280x800', width: 1280, height: 800, text200: true },
];

async function openCreate(page) {
  await page.goto(url);
  await page.getByRole('navigation', { name: '主要工作区' }).getByRole('button', { name: '作品项目', exact: true }).click();
  await page.getByRole('button', { name: '生成新脚本', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: '建立脚本生成任务' });
  await dialog.getByRole('radio').first().check();
  await page.evaluate(() => document.fonts.ready);
  return dialog;
}

async function geometry(dialog) {
  return dialog.evaluate((element) => {
    const rect = (el) => {
      const r = el.getBoundingClientRect();
      return { top: r.top, bottom: r.bottom, left: r.left, right: r.right, height: r.height };
    };
    const body = element.querySelector('.modal-dialog-body');
    const submit = element.querySelector('button[type="submit"]');
    return {
      dialog: rect(element), body: rect(body), submit: rect(submit),
      clientHeight: body.clientHeight, scrollHeight: body.scrollHeight,
      clientWidth: body.clientWidth, scrollWidth: body.scrollWidth,
      gridRows: getComputedStyle(element).gridTemplateRows,
      viewport: { width: innerWidth, height: innerHeight },
    };
  });
}

async function checkLayout(browser, profile, product) {
  const page = await browser.newPage({ viewport: { width: profile.width, height: profile.height } });
  page.on('pageerror', (error) => errors.push(error.message));
  try {
    if (profile.scale) await page.addInitScript((scale) => localStorage.setItem('content-factory.ui-scale', scale), profile.scale);
    if (profile.longEvidence) await page.route(`${api}/s5/templates`, async (route) => {
      const response = await route.fetch();
      const skills = await response.json();
      for (const skill of skills) for (const source of skill.representative_sources) {
        source.dialogue = 'QA 长口播压力数据：主播在固定机位内展示裤腰、口袋、版型，保持连续原声；未知价格和面料参数不编造。'.repeat(30);
        source.source_name = 'QA-直播间开播前长镜头-垂感直筒男裤-同款裤腰与走线展示-长文件名.mp4';
      }
      await route.fulfill({ response, json: skills });
    });
    const dialog = await openCreate(page);
    if (profile.text200) {
      // Text-size simulation, NOT a claim of real Windows display scaling.
      await page.evaluate(() => {
        const root = document.documentElement;
        const style = getComputedStyle(root);
        const tokens = ['assist', 'label', 'nav', 'button', 'table', 'body', 'subtitle', 'section', 'title'];
        const values = tokens.map((token) => parseFloat(style.getPropertyValue(`--font-${token}`)) * 2);
        tokens.forEach((token, index) => root.style.setProperty(`--font-${token}`, `${values[index]}px`));
      });
    }
    const dimensions = await geometry(dialog);
    await page.screenshot({ path: path.join(output, `${profile.name}-top.png`) });
    results.push({ profile: profile.name, dimensions });
    assert(dimensions.body.bottom <= dimensions.dialog.bottom + 1,
      `${profile.name}: scroll body extends below dialog; controls are clipped: ${JSON.stringify(dimensions)}`);
    assert(dimensions.clientHeight < dimensions.scrollHeight, `${profile.name}: long evidence must have a scrollable body`);
    assert(dimensions.scrollWidth <= dimensions.clientWidth + 1, `${profile.name}: body overflows horizontally`);
    assert(dimensions.dialog.top >= 0 && dimensions.dialog.bottom <= profile.height + 1);
    assert(dimensions.submit.top >= dimensions.body.bottom - 1, 'Submit must stay in the footer outside scrolling evidence');
    assert(dimensions.submit.bottom <= dimensions.dialog.bottom, 'Submit is clipped');
    const body = dialog.locator('.modal-dialog-body');
    const box = await body.boundingBox();
    await page.mouse.move(box.x + box.width - 32, box.y + box.height / 2);
    await page.mouse.wheel(0, 1200);
    await page.waitForFunction(() => document.querySelector('.modal-dialog-body').scrollTop > 0);
    await dialog.getByRole('combobox', { name: /^自有商品/ }).selectOption(product.product_id);
    await dialog.getByLabel('目标人群', { exact: true }).fill('QA 通勤男装消费者，关注真实可见版型与同款细节');
    const submit = dialog.getByRole('button', { name: '生成 3 版脚本', exact: true });
    assert(await submit.isEnabled(), 'Valid selections must enable generation');
    await page.screenshot({ path: path.join(output, `${profile.name}-bottom.png`) });
    await submit.focus();
    await page.keyboard.press('Tab');
    assert(await dialog.getByRole('button', { name: '关闭建立脚本生成任务' }).evaluate((el) => el === document.activeElement), 'Tab must remain in the dialog');
    await page.keyboard.press('Escape');
    await dialog.waitFor({ state: 'hidden' });
    assert(await page.getByRole('button', { name: '生成新脚本', exact: true }).evaluate((el) => el === document.activeElement), 'Focus must return to the opener');
    assert.equal(await page.evaluate(() => document.body.style.overflow), '', 'Closing must restore page scroll');
    results.at(-1).passed = true;
  } finally {
    await page.close();
  }
}

async function checkGeneration(browser, product) {
  const page = await browser.newPage({ viewport: { width: 1280, height: 800 } });
  page.on('pageerror', (error) => errors.push(error.message));
  const audience = `QA 弹窗验收 ${Date.now()}：固定直播间、单主播、真实商品细节`;
  let submitCount = 0;
  let rejectRequest = true;
  try {
    const dialog = await openCreate(page);
    assert(await dialog.getByText('请在表单中选择自有商品。', { exact: true }).isVisible());
    await dialog.getByRole('combobox', { name: /^自有商品/ }).selectOption(product.product_id);
    await dialog.getByLabel('目标人群', { exact: true }).fill('   ');
    assert(await dialog.getByRole('button', { name: '生成 3 版脚本', exact: true }).isDisabled());
    assert(await dialog.getByText('请填写目标人群。', { exact: true }).isVisible());
    await dialog.getByLabel('目标人群', { exact: true }).fill(audience);
    await page.route(`${api}/s5/generations`, async (route) => {
      if (route.request().method() !== 'POST') return route.continue();
      submitCount += 1;
      await new Promise((resolve) => setTimeout(resolve, 600));
      if (rejectRequest) return route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ detail: 'QA 模拟连接中断，请重试' }) });
      return route.continue();
    });
    await dialog.getByRole('button', { name: '生成 3 版脚本', exact: true }).click();
    const failure = dialog.getByRole('alert');
    await failure.waitFor();
    assert.match(await failure.innerText(), /QA 模拟连接中断/);
    const failedRect = await failure.boundingBox();
    const dialogRect = await dialog.boundingBox();
    assert(failedRect.y >= dialogRect.y && failedRect.y + failedRect.height <= dialogRect.y + dialogRect.height, 'Error must be visible, not hidden behind the modal');
    assert.equal(await dialog.getByLabel('目标人群', { exact: true }).inputValue(), audience);
    assert.equal(await dialog.getByRole('combobox', { name: /^自有商品/ }).inputValue(), product.product_id);
    assert.equal(submitCount, 1);
    await page.screenshot({ path: path.join(output, 'generation-error-preserves-input.png') });
    await page.keyboard.press('Escape');
    await page.getByRole('button', { name: '生成新脚本', exact: true }).click();
    assert.equal(await dialog.getByLabel('目标人群', { exact: true }).inputValue(), audience, 'Reopening must preserve unsent inputs');
    rejectRequest = false;
    const createdResponse = page.waitForResponse((response) => response.url() === `${api}/s5/generations` && response.request().method() === 'POST' && response.status() === 202);
    await dialog.getByRole('button', { name: '生成 3 版脚本', exact: true }).dblclick();
    const task = await (await createdResponse).json();
    await dialog.waitFor({ state: 'hidden' });
    assert.equal(submitCount, 2, 'Rapid double click must create exactly one additional task');
    const deadline = Date.now() + 60000;
    let completed;
    while (Date.now() < deadline) {
      const tasks = await (await fetch(`${api}/s5/tasks`)).json();
      completed = tasks.find((item) => item.task_id === task.task_id);
      assert.notEqual(completed?.status, 'failed', `Isolated script generation failed: ${completed?.error}`);
      if (completed?.status === 'completed') break;
      await new Promise((resolve) => setTimeout(resolve, 500));
    }
    assert.equal(completed?.status, 'completed', 'Task must reach persisted completed state');
    const stored = await (await fetch(`${api}/s5/scripts/${completed.script_id}`)).json();
    assert.equal(stored.target_audience, audience);
    assert.equal(stored.versions.length, 3);
    assert(stored.versions.every((version) => version.shots.length > 0), 'Generated versions must contain real script segments');
    await page.reload();
    await page.getByRole('navigation', { name: '主要工作区' }).getByRole('button', { name: '作品项目', exact: true }).click();
    await page.locator('.script-package-list button').filter({ hasText: product.name }).first().click();
    await page.locator('.script-editor').waitFor();
    assert.equal(await page.locator('.script-editor').getByLabel('目标人群', { exact: true }).inputValue(), audience, 'Reopening must load persisted script content');
    await page.screenshot({ path: path.join(output, 'generated-script-reopened.png') });
    results.push({ profile: 'generation-failure-retry-persistence', passed: true, task_id: task.task_id, script_id: completed.script_id, version_count: stored.versions.length, submitCount });
  } finally {
    await page.close();
  }
}

async function checkSharedDialogs(browser) {
  const page = await browser.newPage({ viewport: { width: 960, height: 540 } });
  const cases = [
    ['爆点研究', '分析一条爆款', '新建分析任务'],
    ['商品资料', '上传商品图', '上传商品图，建立临时商品'],
    ['素材中心', '上传本次拍摄素材', '批量导入本次拍摄素材'],
    ['剪辑工作台', '建立剪辑工程', '建立剪辑工程'],
    ['剪辑工作台', '导入有授权的 BGM', '导入有授权的 BGM'],
  ];
  page.on('pageerror', (error) => errors.push(error.message));
  try {
    await page.goto(url);
    await page.locator('.legacy-navigation > summary').click();
    for (const [navigation, trigger, title] of cases) {
      await page.getByRole('button', { name: new RegExp(navigation) }).first().click();
      await page.getByRole('button', { name: trigger, exact: true }).click();
      const dialog = page.getByRole('dialog', { name: title, exact: true });
      const body = dialog.locator('.modal-dialog-body');
      const bounds = await dialog.boundingBox();
      const content = await body.boundingBox();
      assert(content.y + content.height <= bounds.y + bounds.height + 1, `${title}: content escapes dialog`);
      assert(bounds.y >= 0 && bounds.y + bounds.height <= 541, `${title}: modal escapes small window`);
      assert(await body.evaluate((el) => el.scrollWidth <= el.clientWidth + 1), `${title}: horizontal overflow`);
      // Tab to the final control so a scrollable body must bring it into view.
      const buttons = dialog.getByRole('button');
      const lastButton = buttons.last();
      await lastButton.scrollIntoViewIfNeeded();
      const buttonBox = await lastButton.boundingBox();
      assert(buttonBox.y >= content.y && buttonBox.y + buttonBox.height <= bounds.y + bounds.height, `${title}: final action is clipped`);
      await page.screenshot({ path: path.join(output, `shared-${navigation}-${trigger}.png`) });
      await page.keyboard.press('Escape');
      await dialog.waitFor({ state: 'hidden' });
      assert.equal(await page.evaluate(() => document.body.style.overflow), '');
      results.push({ profile: `shared-${title}`, passed: true });
    }
  } finally {
    await page.close();
  }
}

async function checkPrerequisites(browser) {
  const cases = [
    { endpoint: 'readiness', amend: (value) => ({ ...value, gateway_configured: false }), hint: '请先连接可用于脚本生成的模型。', action: '打开模型设置' },
    { endpoint: 'templates', amend: () => [], hint: '请选择一个已批准的爆点 Skill。', action: '打开爆点研究' },
    { endpoint: 'products', amend: () => [], hint: '请在表单中选择自有商品。', action: '打开商品资料' },
  ];
  for (const scenario of cases) {
    const page = await browser.newPage({ viewport: { width: 1280, height: 800 } });
    try {
      await page.route(`${api}/s5/${scenario.endpoint}`, async (route) => {
        const response = await route.fetch();
        await route.fulfill({ response, json: scenario.amend(await response.json()) });
      });
      await page.goto(url);
      await page.getByRole('navigation', { name: '主要工作区' }).getByRole('button', { name: '作品项目', exact: true }).click();
      await page.getByRole('button', { name: '生成新脚本', exact: true }).click();
      const dialog = page.getByRole('dialog', { name: '建立脚本生成任务' });
      if (scenario.endpoint !== 'templates') await dialog.getByRole('radio').first().check();
      assert(await dialog.getByRole('button', { name: '生成 3 版脚本', exact: true }).isDisabled());
      // Empty lists can render before the parallel readiness request completes.
      await dialog.getByText(scenario.hint, { exact: true }).waitFor({ state: 'visible' });
      await dialog.getByRole('button', { name: scenario.action, exact: true }).click();
      await dialog.waitFor({ state: 'hidden' });
      assert.equal(await page.evaluate(() => document.body.style.overflow), '');
      results.push({ profile: `missing-${scenario.endpoint}-navigation`, passed: true });
    } finally {
      await page.close();
    }
  }
}

(async () => {
  await fs.mkdir(output, { recursive: true });
  const products = await (await fetch(`${api}/s5/products`)).json();
  assert(products.length > 0 && products.every((product) => product.sku.startsWith('QA-')), 'Only seeded QA products are allowed');
  const browser = await chromium.launch({ channel: 'chrome', headless: true });
  try {
    for (const profile of profiles) await checkLayout(browser, profile, products[0]);
    await checkSharedDialogs(browser);
    await checkPrerequisites(browser);
    if (args.includes('--generation')) await checkGeneration(browser, products[0]);
    assert.deepEqual(errors, [], 'No browser runtime exceptions are allowed');
    console.log(JSON.stringify({ passed: results.length, results }, null, 2));
  } catch (error) {
    results.push({ passed: false, error: error.stack });
    throw error;
  } finally {
    await fs.writeFile(path.join(output, 'results.json'), JSON.stringify({ timestamp: new Date().toISOString(), url, api, results, errors }, null, 2));
    await browser.close();
  }
})().catch((error) => { console.error(error); process.exitCode = 1; });

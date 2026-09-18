const fs = require('node:fs/promises');
const path = require('node:path');

function option(name, fallback = '') {
  const index = process.argv.indexOf(name);
  return index >= 0 ? process.argv[index + 1] : fallback;
}

async function main() {
  const url = option('--url', 'http://127.0.0.1:1421');
  const output = option('--output');
  const playwrightModule = option('--playwright-module');
  if (!output || !playwrightModule) throw new Error('需要 --output 与 --playwright-module');
  const { chromium } = require(playwrightModule);
  await fs.mkdir(output, { recursive: true });
  const browser = await chromium.launch({ channel: 'chrome', headless: true });
  const results = [];
  const errors = [];
  try {
    const page = await browser.newPage({ viewport: { width: 1920, height: 1080 } });
    page.on('pageerror', error => errors.push(error.message));
    await page.goto(url, { waitUntil: 'networkidle' });
    for (const label of ['素材分析', '视频剪辑', '成片素材库']) {
      if (await page.getByRole('button', { name: label, exact: true }).count() !== 1) throw new Error(`缺少一级入口：${label}`);
    }
    if (!(await page.getByRole('heading', { name: '自动剪辑项目' }).isVisible())) throw new Error('视频剪辑首页未打开');
    // The shell renders before the project-list request finishes; wait for the
    // persisted fixture instead of turning ordinary loading into a test race.
    await page.getByText('0.1.24 真实剪辑验收', { exact: true }).first().waitFor({ state: 'visible', timeout: 15000 });
    if (!(await page.getByText('0.1.24 真实剪辑验收', { exact: true }).first().isVisible())) throw new Error('持久化项目未显示');
    // Explanatory copy saying selection is automatic is not a manual selector.
    const skillChoice = /选择.*Skill|Skill.*选择/i;
    const manualSkillControls = page.getByRole('button', { name: skillChoice })
      .or(page.getByRole('link', { name: skillChoice }))
      .or(page.getByRole('combobox', { name: /Skill/i }))
      .or(page.getByRole('listbox', { name: /Skill/i }))
      .or(page.getByRole('textbox', { name: /Skill/i }))
      .or(page.getByRole('radio', { name: /Skill/i }))
      .or(page.getByRole('checkbox', { name: /Skill/i }));
    if (await manualSkillControls.count()) throw new Error('视频剪辑页不应要求用户选择 Skill');
    await page.screenshot({ path: path.join(output, 'video-edit-1920.png'), fullPage: true });
    results.push('视频剪辑项目列表和真实持久化项目可见');

    await page.getByRole('button', { name: '新建项目' }).click();
    for (const label of ['项目名称', '成片数量', '最短秒数', '最长秒数', '字幕字号', '重点词颜色', '重点词放大']) {
      if (!(await page.getByText(label, { exact: true }).first().isVisible())) throw new Error(`新建项目缺少字段：${label}`);
    }
    const controls = await page.locator('.auto-edit-create input').evaluateAll(nodes => nodes.map(node => ({ height: node.getBoundingClientRect().height, font: getComputedStyle(node).fontSize })));
    if (controls.some(item => item.height < 38 || Number.parseFloat(item.font) < 15)) throw new Error('项目输入控件尺寸低于阅读基线');
    results.push('新建项目字段完整且控件不低于 38px/15px');

    await page.getByRole('button', { name: '素材分析', exact: true }).click();
    if (!(await page.getByRole('heading', { name: '对标素材分析' }).isVisible())) throw new Error('素材分析未独立成板块');
    if (!(await page.getByRole('button', { name: /深度分析与 Skill 审核/ }).isVisible())) throw new Error('素材分析缺少 Skill 审核入口');
    results.push('素材分析与视频剪辑职责已分离');

    await page.getByRole('button', { name: '成片素材库', exact: true }).click();
    if (!(await page.getByRole('heading', { name: '成片素材库' }).isVisible())) throw new Error('成片素材库未独立成板块');
    results.push('成片素材库保持独立入口');

    await page.getByRole('button', { name: '视频剪辑', exact: true }).click();
    for (const viewport of [{ name: '1080p', width: 1920, height: 1080 }, { name: '125-equivalent', width: 1536, height: 864 }, { name: '150-equivalent', width: 1280, height: 720 }, { name: 'small', width: 820, height: 760 }]) {
      await page.setViewportSize({ width: viewport.width, height: viewport.height });
      await page.screenshot({ path: path.join(output, `video-edit-${viewport.name}.png`), fullPage: true });
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth + 1);
      if (overflow) throw new Error(`${viewport.name} 出现页面横向溢出`);
    }
    results.push('1080p、125%/150% 等效视口和小窗口无页面横向溢出');

    await page.setViewportSize({ width: 1280, height: 900 });
    await page.addStyleTag({ content: ':root { --font-body: 34px !important; --font-control: 28px !important; --font-label: 26px !important; --font-assist: 24px !important; }' });
    await page.screenshot({ path: path.join(output, 'video-edit-text-200.png'), fullPage: true });
    const keyActions = await page.locator('button:visible').count();
    if (keyActions < 5) throw new Error('文本放大后关键操作不可达');
    results.push('200% 文本放大模拟下关键操作仍可达');
    if (errors.length) throw new Error(`页面异常：${errors.join('；')}`);
    await fs.writeFile(path.join(output, 'results.json'), JSON.stringify({ passed: results, pageErrors: errors }, null, 2), 'utf8');
    console.log(JSON.stringify({ passed: results.length, results }, null, 2));
  } finally {
    await browser.close();
  }
}

main().catch(error => { console.error(error.stack || error); process.exitCode = 1; });

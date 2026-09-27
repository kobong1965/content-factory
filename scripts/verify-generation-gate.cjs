// Isolated local API only. Never starts a paid generation.
const fs = require('node:fs/promises');
const path = require('node:path');
const assert = require('node:assert/strict');
const { execFileSync } = require('node:child_process');
const arg = name => process.argv[process.argv.indexOf(name) + 1];

async function main() {
  const output = path.resolve(arg('--output')), api = arg('--api-url');
  assert.equal(new URL(api).port, '18767');
  await fs.mkdir(output, {recursive:true});
  // Includes project save/reopen, deletion recovery, real ZIP contents and the
  // workspace's scale/layout suite. All retain their original assertions.
  execFileSync(process.execPath, [path.resolve('scripts/verify-library-management.cjs'),
    '--url',arg('--url'),'--api-url',api,'--output',path.join(output,'management'),
    '--playwright-module',arg('--playwright-module')], {stdio:'inherit'});
  const {chromium} = require(arg('--playwright-module'));
  const browser = await chromium.launch({channel:'chrome',headless:true});
  const errors = [];
  try {
    const page = await browser.newPage({viewport:{width:1920,height:1080}});
    page.on('pageerror',e=>errors.push(e.message));
    await page.goto(arg('--url'),{waitUntil:'networkidle'});
    await page.getByRole('button',{name:'视频剪辑',exact:true}).click();
    const check = page.getByRole('region',{name:'生成条件检查'});
    await check.getByRole('heading',{name:'生成前检查'}).waitFor();
    assert.equal(await page.getByRole('button',{name:'开始剪辑',exact:true}).isEnabled(),true);
    assert.match(await check.innerText(), /同一成片内及不同成片之间均可重复使用片段/);
    const project = (await (await fetch(api+'/s7/auto-edit-projects')).json()).projects[0];
    assert(project.source.duration_ms < project.settings.duration_min_ms);
    await page.reload({waitUntil:'networkidle'});
    assert.equal(await page.getByRole('button',{name:'开始剪辑',exact:true}).isEnabled(),true);
    // A real missing source must still block before any paid planning. Only
    // move this fresh harness's managed test copy; always restore it.
    const managed = path.resolve(project.source.path);
    const runRoot = path.dirname(output);
    assert(managed.toLowerCase().startsWith((runRoot+path.sep).toLowerCase()));
    const unavailable = managed+'.qa-unavailable';
    await fs.rename(managed, unavailable);
    try {
      await page.reload({waitUntil:'networkidle'});
      await check.getByRole('heading',{name:'暂不支持生成，请先满足以下条件'}).waitFor();
      assert.equal(await page.getByRole('button',{name:'开始剪辑',exact:true}).isDisabled(),true);
      assert.match(await check.innerText(), /素材无法读取/);
      const blocked = await fetch(api+'/s7/auto-edit-projects/'+project.project_id+'/start',{
        method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({expected_revision:project.revision})});
      assert.equal(blocked.status,422);
      const after = await (await fetch(api+'/s7/auto-edit-projects/'+project.project_id)).json();
      assert.equal(after.revision,project.revision);
      assert.equal(after.status,'draft');
      for (const [width,height] of [[2560,1440],[1920,1080],[1536,864],[1280,720],[800,760],[390,844]]) {
        await page.setViewportSize({width,height});
        assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
        await page.screenshot({path:path.join(output,`blocked-${width}.png`),fullPage:true});
      }
    } finally {
      await fs.rename(unavailable, managed);
    }
    // Network simulation only for an invalid model response, never paid work.
    let failure={...project,status:'failed',progress:10,
      source:{...project.source,duration_ms:300000},
      sources:project.sources.map(s=>({...s,duration_ms:300000})),
      settings:{...project.settings,duration_policy:'custom',duration_min_ms:10000,duration_max_ms:40000},
      generation_check:{ready:true,blockers:[]},
      error:'第 5 条剪辑方案 clip_5 成片时长不在用户设置范围内：实际 30.036 秒，允许 10.000—30.000 秒；超过上限 0.036 秒。未进入成片渲染。'};
    await page.route('**/s7/auto-edit-projects?*',r=>r.fulfill({json:{projects:[failure],total:1}}));
    await page.setViewportSize({width:1920,height:1080});
    await page.reload({waitUntil:'networkidle'});
    await page.getByText('剪辑方案不合格，已阻止生成成片',{exact:true}).waitFor();
    assert.match(await page.locator('.project-failure > p').innerText(), /第 5 条.*30\.036.*0\.036/);
    assert.equal(await page.locator('.project-failure details').getAttribute('open'),null);
    assert.equal(await page.getByRole('button',{name:'重新尝试',exact:true}).isEnabled(),true);
    // A failed plan can be returned to editable generation conditions. The
    // isolated harness intercepts both writes, so this never calls a model.
    const editProjectId = project.project_id;
    let updateBody = null;
    let retryBody = null;
    const edited = {...failure, revision:failure.revision + 1, error:null,
      settings:{...failure.settings,duration_min_ms:15000}, plan:[], selected_skill:null,
      eligible_skill_snapshots:[], generation_check:{ready:true,blockers:[]}};
    await page.route(api+'/s7/auto-edit-projects/'+editProjectId, async route => {
      if (route.request().method() !== 'PATCH') return route.fallback();
      updateBody = JSON.parse(route.request().postData() || '{}');
      await route.fulfill({json:edited});
    });
    await page.route(api+'/s7/auto-edit-projects/'+editProjectId+'/retry', async route => {
      retryBody = JSON.parse(route.request().postData() || '{}');
      await route.fulfill({json:{...edited,revision:edited.revision+1,status:'queued',progress:0}});
    });
    await page.getByRole('button',{name:'修改生成条件',exact:true}).click();
    const editor = page.getByRole('region',{name:'修改生成条件'});
    await editor.waitFor();
    await editor.getByLabel('修改时长策略').selectOption('bounded_15_30');
    assert.equal(await editor.getByLabel('硬下限（秒）').inputValue(),'15');
    assert.equal(await editor.getByLabel('硬上限（秒）').inputValue(),'30');
    await editor.getByLabel('修改字幕显示模式').selectOption('highlight');
    await page.waitForTimeout(4200); // Ordinary polling must not overwrite the draft.
    assert.equal(await editor.getByLabel('修改字幕显示模式').inputValue(),'highlight');
    for (const width of [1920,1440,1280,800,390]) {
      await page.setViewportSize({width,height:940});
      assert(await page.locator('.auto-edit-detail').evaluate(el=>el.scrollWidth<=el.clientWidth+1), `condition panel overflow at ${width}`);
      assert(await editor.evaluate(el=>el.scrollWidth<=el.clientWidth+1), `condition editor overflow at ${width}`);
      await page.screenshot({path:path.join(output,`conditions-${width}.png`),fullPage:true});
    }
    await page.setViewportSize({width:1920,height:1080});
    await page.screenshot({path:path.join(output,'natural-conditions.png'),fullPage:true});
    await editor.getByRole('button',{name:'保存并重新生成',exact:true}).click();
    await page.getByText('新生成条件已保存，项目已重新进入后台队列。',{exact:true}).waitFor();
    assert.deepEqual(updateBody.settings.duration_min_ms,15000);
    assert.equal(updateBody.settings.duration_max_ms,30000);
    assert.equal(updateBody.settings.duration_policy,'bounded_15_30');
    assert.equal(updateBody.settings.subtitle_mode,'highlight');
    assert.equal(updateBody.expected_revision,failure.revision);
    assert.equal(retryBody.expected_revision,edited.revision);
    await page.screenshot({path:path.join(output,'invalid-plan-diagnostic.png'),fullPage:true});
    failure={...failure,error:'同一成片内的剪辑选段不能重叠'};
    await page.reload({waitUntil:'networkidle'});
    await page.getByText('上次剪辑被旧版重复选段规则拦截',{exact:true}).waitFor();
    assert.match(await page.locator('.project-failure > p').innerText(),/允许.*重复使用片段/);
    assert.equal(await page.getByRole('button',{name:'重新尝试',exact:true}).isEnabled(),true);
    await page.screenshot({path:path.join(output,'legacy-overlap.png'),fullPage:true});
    await page.setViewportSize({width:1280,height:900});
    await page.addStyleTag({content:'p,li,h3,button {font-size:200% !important}'});
    assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
    await page.screenshot({path:path.join(output,'text-200.png'),fullPage:true});
    assert.deepEqual(errors,[]);
    await fs.writeFile(path.join(output,'results.json'),JSON.stringify({
      passed:['保存保留','短素材可重复达到目标','缺失素材禁用和原因','直接请求拦截且revision不变','刷新保持','六种视口','方案错误直接可读','失败后修改条件并重新生成','历史重叠错误说明','200%文字'],errors,
      paidCalls:0,modelResponse:'simulated'},null,2));
  } finally { await browser.close(); }
}
main().catch(e=>{console.error(e);process.exit(1);});

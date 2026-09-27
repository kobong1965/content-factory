const fs = require('node:fs/promises');
const path = require('node:path');
const assert = require('node:assert/strict');
const {execFileSync} = require('node:child_process');
const arg = name => process.argv[process.argv.indexOf(name) + 1];
const viewports = [
  {name:'2k',width:2560,height:1440},
  {name:'1080p',width:1920,height:1080},
  {name:'125-equivalent',width:2048,height:1152},
  {name:'150-equivalent',width:1707,height:960},
  {name:'laptop-1440',width:1440,height:900},
  {name:'small',width:800,height:760},
  {name:'narrow-390',width:390,height:844},
];
function assertInside(parent, child, description) {
  const relative = path.relative(path.resolve(parent), path.resolve(child));
  assert(relative && relative !== '..' && !relative.startsWith(`..${path.sep}`) && !path.isAbsolute(relative), `${description} must stay inside ${parent}: ${child}`);
}
async function assertNoHorizontalOverflow(page, description) {
  const dimensions = await page.evaluate(() => ({
    viewport: innerWidth,
    document: document.documentElement.scrollWidth,
    body: document.body.scrollWidth,
    containers: [...document.querySelectorAll('.content, .auto-edit-detail, .project-failure, .project-failure pre')]
      .filter(element => element instanceof HTMLElement && element.getClientRects().length > 0)
      .map(element => ({name: element.tagName.toLowerCase() + '.' + element.className, client: element.clientWidth, scroll: element.scrollWidth})),
    overflowingElements: [...document.querySelectorAll('body *')]
      .filter(element => element instanceof HTMLElement && element.getClientRects().length > 0 && element.getBoundingClientRect().right > innerWidth + 1)
      .slice(0,30).map(element => {
        const style = getComputedStyle(element), rect = element.getBoundingClientRect();
        return {name:element.tagName.toLowerCase() + (element.id ? `#${element.id}` : '') + '.' + element.className,
          left:rect.left,right:rect.right,width:rect.width,client:element.clientWidth,scroll:element.scrollWidth,
          display:style.display,minWidth:style.minWidth,gridTemplateColumns:style.gridTemplateColumns,overflowX:style.overflowX};
      }),
  }));
  assert(dimensions.document <= dimensions.viewport + 1 && dimensions.body <= dimensions.viewport + 1, `${description} page overflow: ${JSON.stringify(dimensions)}`);
  const overflowing = dimensions.containers.filter(element => element.scroll > element.client + 1);
  assert.deepEqual(overflowing, [], `${description} content requires horizontal scrolling: ${JSON.stringify(overflowing)}`);
  return dimensions;
}
async function inspectTypography(page) {
  return page.evaluate(() => ({
    status:document.fonts.status,
    notoSansScAvailable:document.fonts.check('16px "Noto Sans SC"'),
    samples:['body','h1','.primary-button','.project-failure p','.project-failure pre'].flatMap(selector => {
      const element = document.querySelector(selector);
      if (!element) return [];
      const style = getComputedStyle(element);
      return [{selector,fontFamily:style.fontFamily,fontSize:style.fontSize,lineHeight:style.lineHeight,fontWeight:style.fontWeight}];
    }),
    notoFaces:[...document.fonts].filter(font => font.family.includes('Noto Sans SC')).map(font => ({family:font.family,status:font.status,weight:font.weight})),
  }));
}
async function inspectPlatformFonts(page) {
  const client = await page.context().newCDPSession(page);
  try {
    await client.send('DOM.enable');
    await client.send('CSS.enable');
    const {root} = await client.send('DOM.getDocument');
    const samples = [];
    for (const selector of ['h1','.primary-button','.project-failure p','.project-failure pre']) {
      const {nodeId} = await client.send('DOM.querySelector',{nodeId:root.nodeId,selector});
      if (nodeId) samples.push({selector,...await client.send('CSS.getPlatformFontsForNode',{nodeId})});
    }
    return samples;
  } finally { await client.detach(); }
}
async function inspectContrast(page, selectors = ['.project-failure p','.auto-edit-detail h2','.auto-edit-project-row strong','.primary-button','.nav-item-active']) {
  return page.evaluate(selectors => {
    const rgb = value => (value.match(/[\d.]+/g) ?? []).map(Number);
    const luminance = color => color.slice(0,3).map(value => value / 255).map(value => value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4).reduce((sum,value,index) => sum + value * [0.2126,0.7152,0.0722][index],0);
    return selectors.flatMap(selector => {
      const element = document.querySelector(selector);
      if (!element) return [];
      const style = getComputedStyle(element);
      let ancestor = element, background;
      while (ancestor) {
        const color = rgb(getComputedStyle(ancestor).backgroundColor);
        if (color.length === 3 || color[3] === 1) { background = color; break; }
        ancestor = ancestor.parentElement;
      }
      if (!background) return [{selector,unmeasured:'No solid background; manual visual review required'}];
      const ink = luminance(rgb(style.color)), paper = luminance(background);
      const large = parseFloat(style.fontSize) >= 24 || (parseFloat(style.fontSize) >= 18.66 && Number(style.fontWeight) >= 700);
      return [{selector,foreground:style.color,background,ratio:(Math.max(ink,paper)+0.05)/(Math.min(ink,paper)+0.05),required:large ? 3 : 4.5}];
    });
  }, selectors);
}
async function captureViewports(page, output, name, screenshots, verify, sizes = viewports) {
  for (const size of sizes) {
    await page.setViewportSize({width:size.width,height:size.height});
    await page.evaluate(async () => {
      await document.fonts.ready;
      await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
    });
    if (verify) await verify(size);
    await page.evaluate(() => window.scrollTo(0,0));
    const dimensions = await assertNoHorizontalOverflow(page, `${name}/${size.name}`);
    const file = `${name}-${size.name}.png`;
    await page.screenshot({path:path.join(output,file),fullPage:true,animations:'disabled'});
    const typography = await inspectTypography(page);
    const platformFonts = size.width === 1920 ? await inspectPlatformFonts(page) : undefined;
    screenshots.push({surface:name,file,viewport:{width:size.width,height:size.height},dimensions,typography,platformFonts});
  }
  await page.setViewportSize({width:1920,height:1080});
}
async function main() {
  const output = path.resolve(arg('--output')), api = arg('--api-url');
  const runRoot = path.dirname(output);
  assertInside('E:/Codex工作盘', runRoot, 'QA run directory');
  for (const name of ['CONTENT_FACTORY_RUNTIME_ROOT','CONTENT_FACTORY_MEDIA_ROOT','CONTENT_FACTORY_S7_DATA_DIR','CONTENT_FACTORY_S3_CONFIG_PATH','CONTENT_FACTORY_EXPORT_ROOT','TEMP','TMP']) {
    assert(process.env[name], `${name} is required; run through verify-s5-dialog.ps1`);
    assertInside(runRoot, process.env[name], name);
  }
  assert.equal(new URL(api).hostname, '127.0.0.1');
  assert.equal(new URL(api).port, '18767');
  assert.equal(new URL(arg('--url')).origin, 'http://127.0.0.1:1420');
  const python = 'E:/Codex项目盘/男装编剪器/.venv/Scripts/python.exe';
  await fs.mkdir(output, {recursive:true});
  const source = path.join(output, 'qa-source.mp4');
  execFileSync('ffmpeg',['-v','error','-f','lavfi','-i','color=c=0x46667c:s=180x320:r=10','-f','lavfi','-i','sine=frequency=440','-t','6','-c:v','libx264','-c:a','aac',source]);
  const {chromium} = require(arg('--playwright-module'));
  const browser = await chromium.launch({channel:'chrome',headless:true});
  const results=[], errors=[], screenshots=[], unexpectedInferenceRequests=[], accessibilityChecks=[];
  let page;
  try {
    page = await browser.newPage({viewport:{width:1920,height:1080}});
    page.on('pageerror',e=>errors.push(e.message));
    await page.route('**/s7/auto-edit-projects/**', route => {
      const request = route.request();
      if (request.method() === 'POST' && /\/(start|retry)$/.test(new URL(request.url()).pathname)) {
        unexpectedInferenceRequests.push(request.url());
        return route.abort('blockedbyclient');
      }
      return route.continue();
    });
    await page.goto(arg('--url'),{waitUntil:'networkidle'});
    await page.getByRole('heading',{name:'剪辑项目',exact:true}).waitFor();
    assert.equal(await page.getByRole('group',{name:'界面字号'}).count(),0);
    await page.getByRole('button',{name:'新建项目',exact:true}).click();
    await page.getByLabel('项目名称',{exact:true}).fill('验收演示 · J85 上午第一批');
    await page.getByLabel('款号（可选）',{exact:true}).fill('J85');
    await page.getByLabel('成片数量',{exact:true}).fill('20');
    const buffer = await fs.readFile(source);
    await page.locator('.auto-edit-create input[type=file]').setInputFiles(['甲','乙','丙'].map(n=>({name:`${n}-主播长镜头与裤脚细节.mp4`,mimeType:'video/mp4',buffer})));
    await page.getByRole('button',{name:'上移 丙-主播长镜头与裤脚细节.mp4',exact:true}).click();
    for (const size of viewports) {
      await page.setViewportSize({width:size.width,height:size.height});
      assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),`${size.name} overflow`);
      const dimensions = await assertNoHorizontalOverflow(page, `create/${size.name}`);
      await page.screenshot({path:path.join(output,`create-${size.name}.png`),fullPage:true,animations:'disabled'});
      screenshots.push({surface:'create',file:`create-${size.name}.png`,viewport:{width:size.width,height:size.height},dimensions});
    }
    await page.setViewportSize({width:1920,height:1080});
    // Text-only magnification simulation, not Windows DPI or browser zoom.
    await page.evaluate(() => {
      const values = [...document.querySelectorAll('body *')].filter(el => el instanceof HTMLElement).map(el => {
        const style = getComputedStyle(el);
        return [el, el.getAttribute('style'), parseFloat(style.fontSize), parseFloat(style.lineHeight)];
      });
      window.__qaTextStyles = values;
      for (const [el, , size, line] of values) {
        el.style.fontSize = `${size * 2}px`;
        if (Number.isFinite(line)) el.style.lineHeight = `${line * 2}px`;
      }
    });
    assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'200% text overflow');
    await page.getByRole('button',{name:'保存项目',exact:true}).click({trial:true});
    await page.evaluate(() => { window.scrollTo(0,0); });
    await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    await page.screenshot({path:path.join(output,'create-text-200-simulated.png'),fullPage:true});
    await page.evaluate(() => {
      for (const [el, original] of window.__qaTextStyles) {
        if (original === null) el.removeAttribute('style'); else el.setAttribute('style',original);
      }
      delete window.__qaTextStyles;
    });
    await page.route('**/s7/auto-edit-projects',route=>route.request().method()==='POST'?route.fulfill({status:503,json:{detail:'测试保存失败'}}):route.continue());
    await page.getByRole('button',{name:'保存项目',exact:true}).click();
    await page.getByRole('alert').filter({hasText:'测试保存失败'}).waitFor();
    assert.equal(await page.locator('.selected-sources li').count(),3);
    await page.unroute('**/s7/auto-edit-projects');
    const created = page.waitForResponse(r=>r.url().endsWith('/s7/auto-edit-projects') && r.request().method()==='POST');
    await page.getByRole('button',{name:'保存项目',exact:true}).click();
    const project=await (await created).json();
    assert.equal(project.status,'draft'); assert.equal(project.sku,'J85'); assert.equal(project.settings.target_count,20);
    assert.deepEqual(project.sources.map(s=>s.file_name),['甲-主播长镜头与裤脚细节.mp4','丙-主播长镜头与裤脚细节.mp4','乙-主播长镜头与裤脚细节.mp4']);
    await page.reload({waitUntil:'networkidle'});
    await page.getByRole('button',{name:'开始剪辑',exact:true}).waitFor();
    // Do not start paid inference; backend tests independently run model-mocked
    // plans through real FFmpeg and persistent output registration.
    await page.screenshot({path:path.join(output,'project-saved.png'),fullPage:true});
    results.push('3 个素材顺序、款号、20 条设置保存并重开；保存不会启动付费任务；失败保留输入');
    // Persist the fault on the same project that the browser really saved.
    // This synthetic path is only error text; no long-path file is created.
    for (const item of project.sources) assertInside(process.env.CONTENT_FACTORY_S7_DATA_DIR, item.path, 'Synthetic managed source');
    const diagnostic = `[Errno 2] No such file or directory: '${path.join(output, 'synthetic-cache', '主播连续长镜头裤装细节'.repeat(70), 'source-analysis', 'proxy.tmp.mp4')}'；[WinError 206] 文件名或扩展名太长。QA_SYNTHETIC_PATH_END`;
    assert(diagnostic.length > 700 && diagnostic.length < 2000, 'Use a long diagnostic that survives the store limit without truncation');
    execFileSync(python,['-B','-c',
      'import sys; from pathlib import Path; from content_factory_api.auto_edit import get_auto_edit_store; store=get_auto_edit_store(); store.database_path.resolve().relative_to(Path(sys.argv[1]).resolve()); project=store.get_project(sys.argv[2]); assert project["status"] == "draft"; store.fail(sys.argv[2], message=sys.argv[3])',
      runRoot,project.project_id,diagnostic],{env:process.env});
    const failedResponse = await fetch(api+'/s7/auto-edit-projects/'+project.project_id);
    assert(failedResponse.ok, `Read persisted synthetic failure: ${failedResponse.status}`);
    const failedProject = await failedResponse.json();
    assert.equal(failedProject.status,'failed');
    assert.equal(failedProject.error,diagnostic);
    assert.deepEqual(failedProject.sources,project.sources);
    assert.deepEqual(failedProject.settings,project.settings);
    await page.reload({waitUntil:'networkidle'});
    const failure = page.locator('.project-failure');
    const technicalDetails = failure.locator('details');
    const diagnosticText = failure.locator('pre');
    await failure.waitFor({state:'visible'});
    assert.equal(await page.locator('.project-title-line h2').innerText(),project.title);
    assert.equal(await failure.getAttribute('role'),'alert');
    assert((await failure.locator('strong').innerText()).trim().length > 0,'Failure has a readable summary');
    assert((await failure.locator('p').innerText()).trim().length > 0,'Failure explains the next step');
    assert.equal(await technicalDetails.getAttribute('open'),null,'Technical details start collapsed');
    assert.equal(await diagnosticText.isVisible(),false);
    assert.equal(await diagnosticText.textContent(),failedProject.error,'UI displays the exact error persisted by the backend');
    await captureViewports(page,output,'project-failed-collapsed',screenshots,async () => {
      assert.equal(await technicalDetails.getAttribute('open'),null);
      assert.equal(await diagnosticText.isVisible(),false);
      await page.getByRole('button',{name:'重新尝试',exact:true}).click({trial:true});
      await technicalDetails.locator('summary').filter({hasText:'查看技术详情'}).click({trial:true});
    });
    const contrasts = await inspectContrast(page);
    for (const sample of contrasts) if (sample.ratio !== undefined) assert(sample.ratio >= sample.required, `${sample.selector} contrast ${sample.ratio.toFixed(2)} < ${sample.required}`);
    const summary = technicalDetails.locator('summary').filter({hasText:'查看技术详情'});
    await page.getByRole('button',{name:'重新尝试',exact:true}).focus();
    await page.keyboard.press('Tab');
    assert(await summary.evaluate(element => document.activeElement === element),'Keyboard Tab reaches technical details from the retry action');
    const focus = await summary.evaluate(element => {
      const style = getComputedStyle(element);
      return {focusVisible:element.matches(':focus-visible'),outlineStyle:style.outlineStyle,outlineWidth:style.outlineWidth,outlineColor:style.outlineColor};
    });
    assert(focus.focusVisible && focus.outlineStyle !== 'none' && parseFloat(focus.outlineWidth) > 0,'Keyboard focus has a visible outline');
    await page.keyboard.press('Enter');
    accessibilityChecks.push({surface:'project-failure',contrasts,focus,keyboard:'Tab reaches technical details; Enter expands without retrying'});
    await diagnosticText.waitFor({state:'visible'});
    await captureViewports(page,output,'project-failed-expanded',screenshots,async () => {
      assert.notEqual(await technicalDetails.getAttribute('open'),null);
      assert.equal(await diagnosticText.textContent(),failedProject.error);
      const diagnosticScroll = await diagnosticText.evaluate(element => {
        const overflowY = getComputedStyle(element).overflowY;
        element.scrollTop = element.scrollHeight;
        const dimensions = {client:element.clientHeight,scroll:element.scrollHeight,top:element.scrollTop,overflowY};
        element.scrollTop = 0;
        return dimensions;
      });
      if (diagnosticScroll.scroll > diagnosticScroll.client + 1) {
        assert(['auto','scroll'].includes(diagnosticScroll.overflowY),'A bounded diagnostic must allow vertical scrolling');
        assert(diagnosticScroll.top > 0,'The end of the full diagnostic is reachable');
      }
      await page.getByRole('button',{name:'重新尝试',exact:true}).click({trial:true});
    });
    await page.reload({waitUntil:'networkidle'});
    await failure.waitFor({state:'visible'});
    assert.equal(await diagnosticText.textContent(),failedProject.error,'Failure remains available after reopening');
    assert.equal(await technicalDetails.getAttribute('open'),null,'Reopening does not expose raw diagnostics by default');
    const unchangedFailure = await (await fetch(api+'/s7/auto-edit-projects/'+project.project_id)).json();
    assert.equal(unchangedFailure.status,'failed');
    assert.equal(unchangedFailure.revision,failedProject.revision,'Viewing and expanding a failure must not retry or mutate its project');
    assert.equal(unchangedFailure.error,failedProject.error);
    assert.deepEqual(unexpectedInferenceRequests,[],'No implicit start or retry request is allowed');
    results.push('真实隔离项目保存长路径失败：摘要、默认折叠、完整技术详情、重开保留、手动重试可达；所有指定视口无横向滚动，未点击或自动提交重试');
    await page.getByRole('button',{name:'移入回收站',exact:true}).click();
    await page.getByRole('button',{name:'确认移入回收站',exact:true}).click();
    await page.getByText('项目已移入回收站，已生成的成片和原始录播保留。',{exact:true}).waitFor();
    await page.getByRole('button',{name:'项目回收站',exact:true}).click();
    await page.getByRole('button',{name:'恢复项目',exact:true}).click();
    await page.getByText('项目已恢复，可返回项目队列查看。',{exact:true}).waitFor();
    await page.getByRole('button',{name:'返回项目队列',exact:true}).click();
    await page.getByRole('button',{name:'移入回收站',exact:true}).click();
    await page.getByRole('button',{name:'确认移入回收站',exact:true}).click();
    await page.getByText('项目已移入回收站，已生成的成片和原始录播保留。',{exact:true}).waitFor();
    await page.getByRole('button',{name:'项目回收站',exact:true}).click();
    await page.getByRole('button',{name:'永久删除',exact:true}).click();
    await page.getByRole('button',{name:'确认永久删除原素材',exact:true}).waitFor();
    await page.screenshot({path:path.join(output,'purge-preview.png'),fullPage:true});
    await page.getByRole('button',{name:'确认永久删除原素材',exact:true}).click();
    await page.getByRole('status').filter({hasText:'项目已永久删除'}).waitFor();
    for (const s of project.sources) assert.equal(await fs.stat(s.path).then(()=>true,()=>false),false);
    assert.equal((await fetch(api+'/s7/auto-edit-projects/'+project.project_id)).status,404);
    results.push('回收站恢复、再次软删除、预览范围后二次物理删除：仅删除验收合成文件');
    await page.getByRole('button',{name:'素材分析',exact:true}).click();
    await page.getByRole('heading',{name:'已分析的剪辑 Skill',exact:false}).waitFor();
    assert.equal(await page.locator('#video-file').getAttribute('multiple'),'');
    await page.locator('#video-file').setInputFiles([{name:'对标甲.mp4',mimeType:'video/mp4',buffer},{name:'对标乙.mp4',mimeType:'video/mp4',buffer}]);
    assert.equal(await page.locator('.benchmark-files li').count(),2);
    await page.locator('.benchmark-files li').last().getByRole('button',{name:'移除'}).click();
    await page.locator('.benchmark-files li').last().getByRole('button',{name:'移除'}).click();
    const packagePath=path.join(output,'test.cfskills');
    execFileSync(python,['-B','-c','from pathlib import Path; import sys; from content_factory_api.s3 import get_viral_skill_store; from content_factory_api.skill_bundle import export_bundle; export_bundle(get_viral_skill_store().database_path,Path(sys.argv[1]))',packagePath],{env:process.env});
    await page.locator('.skill-package-panel input[type=file]').setInputFiles(packagePath);
    await page.getByRole('button',{name:'确认加入剪辑方法库',exact:true}).waitFor();
    await page.screenshot({path:path.join(output,'analysis-skill-preview.png'),fullPage:true});
    await page.getByRole('button',{name:'确认加入剪辑方法库',exact:true}).click();
    await page.getByRole('status').filter({hasText:'已加入'}).waitFor();
    await page.reload({waitUntil:'networkidle'});
    await page.getByRole('button',{name:'素材分析',exact:true}).click();
    await page.locator('.skill-method-list article').first().waitFor();
    results.push('分析页多选、移除；Skill 包预览→明确确认→重开可见且重复导入不覆盖');
    await page.screenshot({path:path.join(output,'analysis-library.png'),fullPage:true});
    await captureViewports(page,output,'analysis',screenshots);
    // Register an explicitly synthetic output fixture to test project/batch
    // routing, real media playback and archive downloads without paid inference.
    execFileSync('ffmpeg',['-v','error','-i',source,'-frames:v','1',path.join(output,'cover.jpg')]);
    await fs.writeFile(path.join(output,'subtitles.srt'),'1\n00:00:00,000 --> 00:00:01,000\n验收测试\n');
    const candidate={id:'v1',title:'验收成片',source_path:source,source_start_ms:0,source_end_ms:6000,video_path:path.basename(source),subtitle_path:'subtitles.srt',cover_path:'cover.jpg',hook:'测试',benchmark_refs:['测试方法'],review_notes:[]};
    const manifest=path.join(output,'batch.json');
    await fs.writeFile(manifest,JSON.stringify({schema_version:1,id:'target-batch',title:'J85 跳转验收批次',sku:'J85',analysis_summary:'模拟计划，用于验证真实UI与文件下载',candidates:[candidate]}));
    const imported=await fetch(api+'/s7/footage-batches/import',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({manifest_path:manifest})});
    assert(imported.ok,await imported.text());
    const form=new FormData();form.append('title','查看指定批次验收');form.append('sku','J85');form.append('settings_json',JSON.stringify(project.settings));form.append('sources',new Blob([buffer],{type:'video/mp4'}),'test.mp4');
    const saved=await (await fetch(api+'/s7/auto-edit-projects',{method:'POST',body:form})).json();
    execFileSync(python,['-B','-c','import sys; from content_factory_api.auto_edit import get_auto_edit_store; get_auto_edit_store().register_output_batch(sys.argv[1],batch_id="target-batch")',saved.project_id],{env:process.env});
    await page.getByRole('button',{name:'视频剪辑',exact:true}).click();
    await page.getByRole('button',{name:'查看本批成片',exact:true}).click();
    await page.getByRole('heading',{name:'J85 跳转验收批次',exact:true}).waitFor();
    const downloadPromise=page.waitForEvent('download');
    await page.getByRole('link',{name:'下载整批 ZIP',exact:true}).click();
    const download=await downloadPromise; const archive=path.join(output,'download.zip');await download.saveAs(archive);
    execFileSync(python,['-B','-c','import zipfile,sys; z=zipfile.ZipFile(sys.argv[1]); assert z.testzip() is None; assert len([n for n in z.namelist() if n.endswith(".mp4")])==1',archive]);
    await page.screenshot({path:path.join(output,'finished-batch.png'),fullPage:true});
    await captureViewports(page,output,'finished-batch',screenshots);
    await page.getByRole('button',{name:'检查 / 审核成片',exact:true}).click();
    assert.equal(await page.locator('.footage-batch-select select').inputValue(),'target-batch');
    await page.getByRole('button',{name:'成片素材库',exact:true}).click();
    await page.getByRole('button',{name:'J85 · 1 批',exact:true}).click();
    assert.equal(await page.getByRole('button',{name:'J85 · 1 批',exact:true}).getAttribute('aria-pressed'),'true');
    assert.equal(await page.locator('nav[aria-label="款号文件夹"] button').first().getAttribute('aria-pressed'),'false');
    // Let CSS transitions settle so screenshots represent the selected state.
    await page.evaluate(()=>Promise.all(document.getAnimations().map(a=>a.finished.catch(()=>{}))));
    await page.getByRole('button',{name:'打开批次 J85 跳转验收批次',exact:true}).waitFor();
    const selectedSku = page.getByRole('button',{name:'J85 · 1 批',exact:true});
    const assertSkuContrast = async state => {
      await page.evaluate(()=>Promise.all(document.getAnimations().map(a=>a.finished.catch(()=>{}))));
      const samples = await inspectContrast(page,['.library-sku-list [aria-pressed="true"]']);
      assert.equal(samples.length,1,'Exactly one selected SKU folder should be visible');
      const sample = samples[0];
      assert.equal(typeof sample.ratio,'number',`Selected SKU ${state} has a measurable solid background`);
      assert(sample.ratio >= sample.required, `Selected SKU ${state} contrast ${sample.ratio.toFixed(2)} < ${sample.required}`);
      accessibilityChecks.push({surface:'finished-library',state,contrasts:samples});
    };
    await selectedSku.hover();
    await assertSkuContrast('selected-hover');
    await page.mouse.move(0,0);
    await selectedSku.focus();
    await page.keyboard.press('Tab');
    await page.keyboard.press('Shift+Tab');
    assert(await selectedSku.evaluate(element => document.activeElement === element && element.matches(':focus-visible')),'Selected SKU receives visible keyboard focus');
    await assertSkuContrast('selected-keyboard-focus');
    await selectedSku.hover();
    await page.screenshot({path:path.join(output,'finished-sku.png'),fullPage:true});
    await captureViewports(page,output,'finished-library',screenshots);
    const unknownManifest = path.join(output,'unknown-batch.json');
    await fs.writeFile(unknownManifest,JSON.stringify({schema_version:1,id:'unknown-batch',title:'未填款号验收批次',analysis_summary:'测试未知款号筛选，不推断款号',candidates:[candidate]}));
    const unknownImport = await fetch(api+'/s7/footage-batches/import',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({manifest_path:unknownManifest})});
    assert(unknownImport.ok,await unknownImport.text());
    await page.getByRole('button',{name:'刷新成片',exact:true}).click();
    await page.getByRole('button',{name:'未填写款号 · 1 批',exact:true}).click();
    assert.equal(await page.getByRole('combobox',{name:'按款号筛选'}).inputValue(),'未填写款号');
    await page.getByRole('button',{name:'打开批次 未填款号验收批次',exact:true}).waitFor();
    results.push('项目跳转指定批次→审核定位一致；真实 ZIP 下载内容有效；款号文件夹筛选（模拟输出记录）');
    const gatewayBefore = await fs.readFile(process.env.CONTENT_FACTORY_S3_CONFIG_PATH);
    await page.getByRole('button',{name:'模型连接设置',exact:true}).click();
    await page.getByRole('heading',{name:'模型连接',exact:true}).waitFor();
    await captureViewports(page,output,'settings-readonly',screenshots,undefined,[viewports[1]]);
    await page.locator('.sidebar-note').getByRole('button',{name:/检查更新/}).click();
    await page.getByRole('heading',{name:'版本与更新',exact:true}).waitFor();
    await page.getByRole('button',{name:'检查更新',exact:true}).click({trial:true});
    await captureViewports(page,output,'updates-readonly',screenshots,undefined,[viewports[1]]);
    assert.deepEqual(await fs.readFile(process.env.CONTENT_FACTORY_S3_CONFIG_PATH),gatewayBefore,'Readonly settings/update inspection preserves the gateway configuration byte for byte');
    results.push('模型设置与更新入口只读截图；配置文件逐字节不变，未提交检查/下载/安装更新');
    assert.deepEqual(errors,[]);
    assert.deepEqual(unexpectedInferenceRequests,[]);
    await fs.writeFile(path.join(output,'results.json'),JSON.stringify({passed:results,pageErrors:errors,unexpectedInferenceRequests,screenshots,accessibilityChecks,boundary:'仅本机浏览器和隔离合成项目；失败写入真实隔离后端，成片由合成记录导入。系统缩放为等效 CSS 视口，未做真实系统缩放。无真实模型调用；重新尝试只检查可操作性，未实际点击。字体含计算样式和 Chromium 实际使用字体；对比度仅抽查有纯色底的文字。'},null,2));
    console.log(JSON.stringify(results));
  } catch (error) {
    let screenshotError;
    if (page) {
      try { await page.screenshot({path:path.join(output,'failure-current-page.png'),fullPage:true,animations:'disabled',timeout:10000}); }
      catch (reason) { screenshotError = String(reason); }
    }
    const typography = page ? await inspectTypography(page).catch(() => undefined) : undefined;
    await fs.writeFile(path.join(output,'failure.json'),JSON.stringify({message:String(error),stack:error.stack,pageErrors:errors,unexpectedInferenceRequests,screenshots,accessibilityChecks,screenshotError,typography},null,2));
    throw error;
  } finally {await browser.close();}
}
main().catch(e=>{console.error(e);process.exitCode=1;});

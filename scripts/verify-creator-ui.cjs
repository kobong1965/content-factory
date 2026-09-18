/* Isolated real browser + API acceptance; model responses come from local QA relay. */
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const args = process.argv.slice(2);
const arg = (key) => args[args.indexOf(key) + 1];
const url = arg('--url'), api = arg('--api-url'), output = path.resolve(arg('--output'));
assert(output.toLowerCase().startsWith(path.resolve('E:/Codex工作盘').toLowerCase() + path.sep));
for (const address of [url, api]) { const u = new URL(address); assert.equal(u.hostname, '127.0.0.1'); assert.notEqual(u.port, '8766'); }
const { chromium } = require(arg('--playwright-module'));
const results = [], errors = [];
const json = async (endpoint) => { const r = await fetch(`${api}${endpoint}`); assert(r.ok); return r.json(); };
async function nav(page, name) { await page.getByRole('navigation', { name: '主要工作区', exact: true }).getByRole('button', { name, exact: true }).click(); }
async function home(page, productId) {
  await page.goto(url);
  await page.getByRole('heading', { name: '创作工作台', exact: true }).waitFor();
  await page.getByLabel('使用已有商品', { exact: true }).selectOption(productId);
  await page.getByText('已确认卖点，可以写脚本', { exact: true }).waitFor();
  await page.evaluate(() => document.fonts.ready);
}
(async () => {
  await fs.mkdir(output, { recursive: true });
  const products = await json('/s5/products');
  assert(products.length && products.every(item => item.sku.startsWith('QA-')));
  const product = products[0];
  const browser = await chromium.launch({ channel: 'chrome', headless: true });
  try {
    for (const [name, width, height, large, text200] of [
      ['2k',2560,1440], ['1080p',1920,1080], ['125-equivalent',2048,1152], ['150-equivalent',1707,960],
      ['small',960,640], ['large',1280,800,true], ['text200',1280,800,false,true],
    ]) {
      const page = await browser.newPage({ viewport: { width,height } });
      page.on('pageerror', error => errors.push(error.message));
      await home(page, product.product_id);
      if (large) await page.getByRole('button', { name: '大字号', exact:true }).click();
      if (text200) await page.evaluate(() => {
        const root = document.documentElement, style = getComputedStyle(root);
        const tokens = ['assist','label','nav','button','table','body','subtitle','section','title'];
        const sizes = tokens.map(token => parseFloat(style.getPropertyValue(`--font-${token}`)) * 2);
        tokens.forEach((token,index) => root.style.setProperty(`--font-${token}`, `${sizes[index]}px`));
      });
      await page.locator('.creator-skill-card input').first().check();
      const geometry = await page.evaluate(() => ({ width:innerWidth, scroll:document.documentElement.scrollWidth, font:getComputedStyle(document.body).fontSize, loaded:document.fonts.check('17px "Noto Sans SC"') }));
      assert(geometry.scroll <= width + 1, `${name}: horizontal page overflow ${JSON.stringify(geometry)}`);
      assert(geometry.loaded, 'Bundled Chinese font must load');
      const generate = page.getByRole('button', { name:'生成 1 套脚本',exact:true });
      assert(await generate.isEnabled());
      await generate.scrollIntoViewIfNeeded();
      await page.evaluate(() => window.scrollTo(0,0));
      await page.screenshot({ path:path.join(output, `${name}-home.png`), fullPage:true });
      await page.locator('.creator-skill-card').first().getByRole('button', {name:'查看详情'}).click();
      const dialog = page.getByRole('dialog');
      await dialog.waitFor();
      const bounds = await dialog.boundingBox();
      assert(bounds.y >= 0 && bounds.y + bounds.height <= height + 1, `${name}: dialog outside viewport`);
      assert(await dialog.locator('.modal-dialog-body').evaluate(el => el.scrollWidth <= el.clientWidth+1));
      const select = dialog.getByRole('button', {name:'选用这个 Skill'});
      await select.focus(); await page.keyboard.press('Tab');
      assert(await dialog.getByRole('button', {name:/关闭/}).evaluate(el => el===document.activeElement));
      await page.screenshot({path:path.join(output, `${name}-skill.png`)});
      await page.keyboard.press('Escape'); await dialog.waitFor({state:'hidden'});
      assert.equal(await page.evaluate(() => document.body.style.overflow), '');
      results.push({name,geometry,passed:true});
      await page.close();
    }
    const page = await browser.newPage({viewport:{width:1707,height:960}});
    page.on('pageerror', error => errors.push(error.message));
    await home(page, product.product_id);
    await page.locator('.creator-skill-card input').first().check();
    await nav(page,'作品项目'); await nav(page,'创作工作台');
    assert(await page.locator('.creator-skill-card input').first().isChecked());
    await page.locator('.creator-skill-card input').first().uncheck();
    await nav(page,'脚本 Skill 库'); await nav(page,'创作工作台');
    assert.equal(await page.locator('.creator-skill-card input').first().isChecked(),false);
    await page.locator('.creator-skill-card input').first().check();
    results.push({name:'unsaved-selection-survives-navigation',passed:true});
    await page.getByRole('button',{name:'保存创作选择',exact:true}).click();
    await page.reload();
    await page.getByText('已确认卖点，可以写脚本',{exact:true}).waitFor();
    assert(await page.locator('.creator-skill-card input').first().isChecked());
    results.push({name:'creation-draft-reopened',passed:true});
    await page.getByRole('button',{name:'确认卖点 / 编辑资料',exact:true}).click();
    const productDialog=page.getByRole('dialog');
    const productName=productDialog.getByLabel('商品名称',{exact:true});
    const originalName=await productName.inputValue();
    assert.equal(await productDialog.getByLabel('本次操作人',{exact:true}).count(), 0, '商品保存不再要求操作人姓名');
    await productName.fill(originalName+' 暂存');
    await page.route(`${api}/s4/products/${product.product_id}`,route=>route.request().method()==='PUT'
      ? route.fulfill({status:503,json:{detail:'QA 商品保存失败'}}) : route.continue());
    await productDialog.getByRole('button',{name:'保存新版本',exact:true}).click();
    await productDialog.getByRole('status').filter({hasText:'QA 商品保存失败'}).waitFor();
    assert.equal(await productName.inputValue(),originalName+' 暂存');
    assert.equal((await json(`/s4/products/${product.product_id}`)).name,originalName);
    await page.screenshot({path:path.join(output,'product-save-failure-visible.png')});
    await page.unroute(`${api}/s4/products/${product.product_id}`);
    await productName.fill(originalName);
    await page.route(`${api}/s4/products/${product.product_id}/suggestions`,route=>route.fulfill({status:503,json:{detail:'QA 图片识别失败'}}));
    await productDialog.locator('input[type=file]').setInputFiles({name:'QA-product.png',mimeType:'image/png',buffer:Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a8N8AAAAASUVORK5CYII=','base64')});
    await productDialog.getByRole('button',{name:'复制到本机资料库',exact:true}).click();
    await productDialog.getByRole('status').filter({hasText:'QA 图片识别失败'}).waitFor();
    assert((await json(`/s4/products/${product.product_id}`)).sources.some(s=>s.managed && s.kind==='image'));
    await productDialog.getByRole('button',{name:'重新识别图片卖点',exact:true}).click();
    await productDialog.getByRole('status').filter({hasText:'QA 图片识别失败'}).waitFor();
    await page.screenshot({path:path.join(output,'product-suggestion-failure-visible.png')});
    await productDialog.getByRole('button',{name:'返回创作工作台',exact:true}).click();
    await productDialog.waitFor({state:'hidden'});
    await page.unroute(`${api}/s4/products/${product.product_id}/suggestions`);
    results.push({name:'product-modal-save-and-recognition-errors-visible-input-retained',passed:true});
    let fail = true, submissions = 0;
    await page.route(`${api}/s5/generations`, async route => {
      if(route.request().method() !== 'POST') return route.continue();
      submissions++;
      await new Promise(resolve=>setTimeout(resolve,350));
      if(fail) return route.fulfill({status:503,json:{detail:'QA 模拟提交失败'}});
      return route.continue();
    });
    await page.getByRole('button',{name:'生成 1 套脚本',exact:true}).click();
    await page.getByText(/1 套未确认成功/).waitFor();
    assert(await page.locator('.creator-skill-card input').first().isChecked());
    assert.equal(submissions,1);
    fail=false;
    const response = page.waitForResponse(r=>r.url()===`${api}/s5/generations` && r.status()===202);
    // Two synchronous clicks prove the ref lock, independent of React render timing.
    await page.getByRole('button',{name:'生成 1 套脚本',exact:true}).evaluate(el=>{el.click();el.click();});
    const task = await (await response).json();
    await page.getByText(/已建立 1 套独立任务/).waitFor();
    assert.equal(submissions,2);
    let completed;
    const deadline=Date.now()+60000;
    while(Date.now()<deadline) {
      completed=(await json('/s5/tasks')).find(item=>item.task_id===task.task_id);
      assert.notEqual(completed?.status,'failed',completed?.error);
      if(completed?.status==='completed') break;
      await new Promise(resolve=>setTimeout(resolve,500));
    }
    assert.equal(completed?.status,'completed');
    const stored=await json(`/s5/scripts/${completed.script_id}`);
    assert.equal(stored.versions.length,3);
    await nav(page,'作品项目');
    await page.locator('.script-package-list button').first().click();
    await page.locator('.script-editor').waitFor();
    await page.evaluate(()=>{document.activeElement?.blur();window.scrollTo({top:0,behavior:'instant'});});
    await page.waitForFunction(()=>window.scrollY===0);
    await page.screenshot({path:path.join(output,'shooting-script-before-edit.png'),fullPage:true});
    const voice=page.getByLabel('逐字口播',{exact:true}).first();
    const amended=(await voice.inputValue())+' QA 保存验证';
    await voice.fill(amended);
    await page.getByRole('button',{name:'后期剪辑稿',exact:true}).click();
    const subtitle=page.getByLabel('同步字幕',{exact:true}).first();
    const caption=(await subtitle.inputValue())+' QA 字幕验证';
    await subtitle.fill(caption);
    await page.getByRole('button',{name:'主播拍摄稿',exact:true}).click();
    assert.equal(await voice.inputValue(),amended);
    await page.getByRole('button',{name:'保存新版本',exact:true}).click();
    await page.getByText(/人工修改已保存为第/).waitFor();
    await page.reload(); await nav(page,'作品项目');
    await page.locator('.script-package-list button').first().click();
    await page.locator('.script-editor').waitFor();
    assert.equal(await page.getByLabel('逐字口播',{exact:true}).first().inputValue(),amended);
    await page.getByRole('button',{name:'后期剪辑稿',exact:true}).click();
    assert.equal(await page.getByLabel('同步字幕',{exact:true}).first().inputValue(),caption);
    await page.evaluate(()=>{document.activeElement?.blur();window.scrollTo({top:0,behavior:'instant'});});
    await page.waitForFunction(()=>window.scrollY===0);
    await page.screenshot({path:path.join(output,'editing-script-reopened.png'),fullPage:true});
    const saved=await json(`/s5/scripts/${completed.script_id}`);
    assert(saved.revision>stored.revision);
    assert.equal(saved.versions[0].shots[0].voiceover,amended);
    assert.equal(saved.versions[0].shots[0].subtitle,caption);
    const downloadPromise=page.waitForEvent('download');
    await page.getByRole('button',{name:'导出当前版主播拍摄稿',exact:true}).click();
    const download=await downloadPromise;
    const exportPath=path.join(output,'exported-shooting-script.txt');
    await download.saveAs(exportPath);
    const exported=await fs.readFile(exportPath,'utf8');
    assert(exported.includes(amended) && exported.includes('固定机位') && exported.includes('表情：'));
    results.push({name:'submit-failure-double-click-generate-edit-save-reopen',passed:true,task_id:task.task_id,revision:saved.revision});
    for(const name of ['脚本 Skill 库','我的素材','后期剪辑','管理设置']) {await nav(page,name); await page.screenshot({path:path.join(output,`page-${name}.png`)});}
    assert.equal(await page.locator('.legacy-navigation').count(), 0, '0.1.19 已移除旧导航');
    await nav(page,'脚本 Skill 库');
    await page.getByRole('button',{name:'导入对标素材',exact:true}).click();
    await nav(page,'脚本 Skill 库');
    await page.getByRole('button',{name:'素材分析任务',exact:true}).click();
    await nav(page,'成片素材库');
    results.push({name:'all-workspaces-accessible',passed:true});
    assert.deepEqual(errors,[]);
    await page.close();
  } catch(error) { results.push({passed:false,error:error.stack}); throw error; }
  finally {await fs.writeFile(path.join(output,'results.json'),JSON.stringify({results,errors},null,2));await browser.close();}
  console.log(JSON.stringify(results,null,2));
})().catch(error=>{console.error(error);process.exitCode=1;});

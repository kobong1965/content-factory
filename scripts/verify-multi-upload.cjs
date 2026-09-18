const fs = require('node:fs/promises');
const path = require('node:path');
const {execFileSync} = require('node:child_process');
const assert = require('node:assert/strict');
const arg = name => process.argv[process.argv.indexOf(name) + 1];
async function main() {
  const output = arg('--output');
  await fs.mkdir(output, {recursive:true});
  const video = path.join(output, 'source.mp4');
  execFileSync('ffmpeg', ['-v','error','-f','lavfi','-i','color=c=blue:s=180x320:r=25','-f','lavfi','-i','sine=frequency=440','-t','6','-c:v','libx264','-c:a','aac',video]);
  const {chromium} = require(arg('--playwright-module'));
  const browser = await chromium.launch({channel:'chrome',headless:true});
  try {
    const page = await browser.newPage({viewport:{width:1536,height:864}});
    const errors=[]; page.on('pageerror', error => errors.push(error.message));
    await page.goto(arg('--url'), {waitUntil:'networkidle'});
    await page.getByRole('button',{name:'视频剪辑',exact:true}).click();
    await page.getByRole('button',{name:'新建项目',exact:true}).click();
    await page.getByLabel('项目名称',{exact:true}).fill('多文件上传验收');
    await page.getByLabel('字幕字体',{exact:true}).selectOption('kaiti');
    await page.getByLabel('字幕特效',{exact:true}).selectOption('fade');
    const input=page.locator('.auto-edit-create input[type=file]');
    assert.equal(await input.getAttribute('multiple'), '');
    const buffer=await fs.readFile(video);
    await input.setInputFiles([{name:'第一条录播.mp4',mimeType:'video/mp4',buffer},{name:'第二条录播.mp4',mimeType:'video/mp4',buffer}]);
    assert.equal(await page.locator('.selected-sources li').count(),2);
    await input.setInputFiles([{name:'第三条录播.mp4',mimeType:'video/mp4',buffer}]);
    assert.equal(await page.locator('.selected-sources li').count(),3);
    await page.getByRole('button',{name:'移除 第二条录播.mp4',exact:true}).click();
    assert.equal(await page.locator('.selected-sources li').count(),2);
    await page.route('**/s7/auto-edit-projects', route => route.request().method()==='POST' ? route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({detail:'测试上传失败'})}) : route.continue());
    await page.getByRole('button',{name:'保存项目',exact:true}).click();
    await page.getByRole('alert').filter({hasText:'测试上传失败'}).waitFor();
    await page.waitForTimeout(4500);
    assert.equal(await page.locator('.selected-sources li').count(),2);
    assert.equal(await page.getByLabel('项目名称',{exact:true}).inputValue(),'多文件上传验收');
    assert.equal(await page.getByRole('alert').filter({hasText:'测试上传失败'}).count(),1);
    await page.unroute('**/s7/auto-edit-projects');
    await page.screenshot({path:path.join(output,'multi-selected.png'),fullPage:true});
    const responsePromise=page.waitForResponse(r=>r.url().endsWith('/s7/auto-edit-projects')&&r.request().method()==='POST');
    await page.getByRole('button',{name:'保存项目',exact:true}).click();
    const response=await responsePromise; assert.equal(response.status(),201);
    const project=await response.json();
    assert.deepEqual(project.sources.map(s=>s.file_name),['第一条录播.mp4','第三条录播.mp4']);
    assert.equal(project.settings.target_count,5);
    assert.equal(project.settings.subtitle_font,'kaiti');
    assert.equal(project.settings.subtitle_effect,'fade');
    await page.reload({waitUntil:'networkidle'});
    await page.getByRole('heading',{name:'项目素材 · 2 条',exact:true}).waitFor();
    await page.screenshot({path:path.join(output,'multi-saved.png'),fullPage:true});
    for(const width of [2560,1920,1536,1280,820]) {
      await page.setViewportSize({width,height:900});
      assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false);
    }
    assert.deepEqual(errors,[]);
    await fs.writeFile(path.join(output,'results.json'),JSON.stringify({passed:true,projectId:project.project_id,checks:['multi-select','append','remove','failed-input-retained','polling-error-retained','real-upload','persist-reload','responsive'],errors},null,2));
    console.log('Multi-upload acceptance passed');
  } finally {await browser.close();}
}
main().catch(error=>{console.error(error);process.exitCode=1;});

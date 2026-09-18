const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const args=process.argv.slice(2), arg=n=>args[args.indexOf(n)+1];
const api=arg('--api-url'), url=arg('--url'), output=arg('--output');
assert.equal(new URL(api).port,'18767');
const {chromium}=require(arg('--playwright-module'));
(async()=>{
  const browser=await chromium.launch({channel:'chrome',headless:true});
  const page=await browser.newPage({viewport:{width:1440,height:900}});
  const errors=[]; page.on('pageerror',e=>errors.push(e.message));
  await fs.mkdir(output,{recursive:true});
  try {
    // Exercise the desktop branch; real native IPC and Explorer are separately
    // verified in the installed Tauri window, not claimed by this simulation.
    await page.addInitScript(()=>{window.isTauri=true; window.__TAURI_INTERNALS__={};});
    const manifest='E:/Codex工作盘/artifacts/latest/千川对标剪辑-20260915-字幕新版/batch.json';
    const imported=await fetch(api+'/s7/footage-batches/import',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({manifest_path:manifest})});
    assert(imported.ok); const batch=await imported.json();
    const review=await fetch(`${api}/s7/footage-batches/${batch.id}/candidates/01/review`,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({revision:batch.revision,status:'approved',note:''})});assert(review.ok);
    await page.goto(url);await page.getByRole('button',{name:'成片素材库',exact:true}).click();
    const download=page.getByRole('link',{name:'下载成片',exact:true});await download.waitFor();
    let requests=0; page.on('request',r=>{if(r.url()===api+'/local-exports')requests++;});
    await download.dblclick();
    await page.getByText('文件已保存',{exact:true}).waitFor({timeout:30000});
    assert.equal(requests,1,'double-click must submit one copy');
    let saved=await page.locator('.desktop-download-path').innerText();
    assert.equal(path.dirname(saved), path.resolve(path.dirname(output),'downloads'));
    assert((await fs.readFile(saved)).equals(await fs.readFile(path.join(path.dirname(manifest),'videos/01/review.mp4'))));
    await page.screenshot({path:path.join(output,'download-success.png')});
    await page.getByRole('button',{name:'关闭提示'}).click();
    await page.getByRole('link',{name:'字幕文件',exact:true}).click();
    await page.getByText('文件已保存',{exact:true}).waitFor();
    saved=await page.locator('.desktop-download-path').innerText();
    assert((await fs.readFile(saved)).equals(await fs.readFile(path.join(path.dirname(manifest),'videos/01/subtitles.srt'))));
    await page.route(api+'/local-exports',r=>r.fulfill({status:500,json:{detail:'磁盘写入失败，请重试'}}));
    await download.click();await page.getByRole('alert').filter({hasText:'磁盘写入失败'}).waitFor();
    assert.equal(await page.locator('.desktop-download-path').count(),0,'failed export cannot show stale success');
    await page.unroute(api+'/local-exports');
    await download.click();await page.getByText('文件已保存',{exact:true}).waitFor();
    assert.deepEqual(errors,[]);
    await fs.writeFile(path.join(output,'results.json'),JSON.stringify({passed:5,checks:['desktop branch intercepts links','double click single copy','MP4 byte equality','SRT byte equality','error visible and retry succeeds'],native_ipc_simulated:true,errors},null,2));
    console.log('5 native download bridge checks passed; native window still required');
  }catch(e){await page.screenshot({path:path.join(output,'failure.png')});throw e;}finally{await browser.close();}
})();

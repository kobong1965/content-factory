const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const path=require('node:path');
const args=process.argv.slice(2),arg=n=>args[args.indexOf(n)+1];
const {chromium}=require(arg('--playwright-module'));
(async()=>{
 const api=arg('--api-url'),output=arg('--output');assert.equal(new URL(api).port,'18767');
 const response=await fetch(api+'/s7/footage-batches/import',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({manifest_path:'E:/Codex工作盘/artifacts/latest/千川对标剪辑-20260915-字幕新版/batch.json'})});assert(response.ok);const batch=await response.json();
 const browser=await chromium.launch({channel:'chrome',headless:true});const page=await browser.newPage({viewport:{width:1920,height:1080},acceptDownloads:true});const errors=[];page.on('pageerror',e=>errors.push(e.message));await fs.mkdir(output,{recursive:true});
 try{
 await page.goto(arg('--url'));await page.getByRole('heading',{name:'素材剪辑',exact:true}).waitFor();
 assert.deepEqual(await page.getByRole('navigation',{name:'主要工作区',exact:true}).getByRole('button').allTextContents(),['AI 剪辑','成片素材库']);
 assert.equal(await page.getByRole('heading',{name:'工程能力与真实验收'}).count(),0);
 await page.screenshot({path:path.join(output,'upload.png')});
 await page.getByRole('button',{name:'成片素材库',exact:true}).click();
 await page.getByRole('button',{name:'打开批次 '+batch.title,exact:true}).waitFor();assert.equal(await page.locator('video').count(),0);
 await page.screenshot({path:path.join(output,'folders.png')});
 await page.getByRole('button',{name:'打开批次 '+batch.title,exact:true}).click();assert.equal(await page.locator('video').count(),6);
 assert.equal(await page.locator('.finished-item').filter({hasText:'待审核'}).count(),6);
 const [download]=await Promise.all([page.waitForEvent('download'),page.getByRole('link',{name:'下载成片',exact:true}).first().click()]);
 const downloaded=await download.path();assert((await fs.readFile(downloaded)).equals(await fs.readFile('E:/Codex工作盘/artifacts/latest/千川对标剪辑-20260915-字幕新版/videos/01/review.mp4')));
 await page.getByRole('button',{name:'返回全部批次'}).click();assert.equal(await page.locator('video').count(),0);
 for(const width of [2560,1920,2048,1707,960]){await page.setViewportSize({width,height:900});await page.screenshot({path:path.join(output,`folders-${width}.png`)});assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));}
 await page.getByRole('button',{name:'大字号',exact:true}).click();assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
 await page.reload();await page.getByRole('button',{name:'成片素材库',exact:true}).click();await page.getByRole('button',{name:'打开批次 '+batch.title,exact:true}).waitFor();
 const saved=await (await fetch(api+'/s7/footage-batches')).json();assert.equal(saved.batches[0].revision,batch.revision);assert(saved.batches[0].candidates.every(c=>c.review_status==='pending'));assert.deepEqual(errors,[]);
 await fs.writeFile(path.join(output,'results.json'),JSON.stringify({checks:10,errors,download_byte_equal:true,review_unchanged:true,system_scaling:'CSS equivalent only'},null,2));console.log('10 simplified workflow checks passed');
 }finally{await browser.close();}
})();

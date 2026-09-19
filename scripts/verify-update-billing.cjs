const fs = require('node:fs/promises');
const path = require('node:path');
function option(name) { return process.argv[process.argv.indexOf(name)+1]; }
async function main() {
  const {chromium} = require(option('--playwright-module'));
  const output = option('--output'); await fs.mkdir(output,{recursive:true});
  const browser = await chromium.launch({headless:true,channel:'msedge'});
  const page = await browser.newPage({viewport:{width:1536,height:864}});
  const errors=[];page.on('pageerror',e=>errors.push(e.message));
  try {
    await page.goto(option('--url'));
    await page.getByRole('button',{name:'模型连接设置',exact:true}).click();
    const billing=page.getByRole('link',{name:'https://apikey.fun/dashboard'});
    if(await billing.getAttribute('href')!=='https://apikey.fun/dashboard') throw new Error('Wrong billing URL');
    await page.getByRole('button',{name:'查看余额与用量',exact:true}).waitFor();
    await page.screenshot({path:path.join(output,'billing.png'),fullPage:true});
    await page.getByRole('button',{name:/版本 .*检查更新/}).click();
    await page.getByRole('heading',{name:'版本与更新'}).waitFor();
    if(await page.getByRole('checkbox').isChecked()) throw new Error('Preview must be opt-in');
    // Controlled provider response: exercise UI state transitions without
    // downloading GB of release files or installing on the test workstation.
    let state={phase:'idle',message:'',downloaded:0,total:100,current_version:'0.1.31'};
    await page.route('**/software-updates**',async route=>{
      const url=route.request().url();
      if(url.includes('/check?')) state={...state,phase:'available',version:'0.1.32',message:'发现新版本',notes:'测试更新说明'};
      if(url.endsWith('/download')) state={...state,phase:'ready',downloaded:100,message:'测试下载完成'};
      if(url.endsWith('/install')) {await route.fulfill({status:409,json:{detail:'仍有排队、分析、转写或剪辑任务；请完成后再安装。'}});return;}
      await route.fulfill({json:state});
    });
    await page.getByRole('button',{name:'检查更新',exact:true}).click();
    await page.getByRole('button',{name:'下载 0.1.32',exact:true}).click();
    await page.getByRole('button',{name:'安装更新',exact:true}).click();
    await page.getByRole('alert').filter({hasText:'仍有排队'}).waitFor();
    for(const width of [2560,1920,1536,1280,960]) {
      await page.setViewportSize({width,height:Math.round(width*9/16)});
      if(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1)) throw new Error('Horizontal overflow at '+width);
    }
    await page.screenshot({path:path.join(output,'updates.png'),fullPage:true});
    if(errors.length) throw new Error(errors.join('\n'));
    await fs.writeFile(path.join(output,'results.json'),JSON.stringify({passed:['approved billing link','update navigation','preview opt-in','mock check/download','busy install feedback','responsive widths'],boundary:'Update network and installation mocked; no account login accessed.'},null,2));
  } finally {await browser.close();}
}
main().catch(e=>{console.error(e);process.exitCode=1;});

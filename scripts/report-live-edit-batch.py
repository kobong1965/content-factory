"""Evidence-linked analysis and review index for this real footage batch."""
import html
import importlib.util
import json
from pathlib import Path

spec=importlib.util.spec_from_file_location('render_batch',Path(__file__).with_name('render-live-edit-batch.py'))
module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
ROOT, CASES = module.ROOT, module.CASES
WHY = {
 '4.20 J85': ('细节先于介绍，减少观众认款成本', '前 7.8 秒的腰头/袋口近景先抓商品识别，25.5–29.5 秒拉伸让“有弹力”有可见动作。细节—整体—细节的变化能缓解固定机位的单调。', '本批 01 保留腰头到整裤；04 保留拉伸释放。没有移用夏季超薄、温度和材质数值。'),
 '4.23': ('异常形态制造未完成感', '0–6.6 秒先出现拧成绳的裤腿，6.6–9.6 秒立即展开，观众可以等待并看见结果；后续内腰与背袋又帮助识别商品。', '新实拍未发现等价完整拧转测试，不伪造同一挑战；只借用展示结果不能切走的规则。'),
 '5.9 J72': ('舒适顾虑与动作挑战接力', '0–5.8 秒腰头展示，8–10.5 秒横拉，13–27 秒拧转并释放，动作逐步加深；27–31 秒手指停顿强化重点。', '02 用身材顾虑开场接完整裤型，未采用对标关于面料性能的原话。'),
 '5.9 J82': ('把抽象质感变成可比较的细节', '5–11.5 秒捏双层边缘，16–25.5 秒完整背面，30–36.8 秒拉伸；近远尺度交替让人能判断细节和轮廓。', '03 用裤脚和腰头在同画面比较，不写固定尺寸；排除另一条灰裤的比较画面。'),
 '5.10 J82': ('多个短动作持续交付信息', '3.4–7.7 秒拧细、13.7–17 秒横拉、25–31 秒前臂触感，连续阶段让口播不是单纯形容词堆叠。', '04 保留实拍中确实存在的拉伸和裤口，不能把未拍到的凉感动作剪出来。'),
 '5.12 J85': ('短时间内完成展示闭环', '3.7–6.7 秒抖动、6.7–11 秒搭臂、11–20 秒拧转释放，27–30 秒拉伸；动作完成再进入下一项。', '本批压缩报尺码和库存插话，04 保留连贯动作，不为卡点打断证明。'),
 '5.13 J83': ('从整体认识到背袋记忆点', '4.2–8.5 秒拧细、15.7–20.5 秒横拉、26–30 秒搭臂，最后 40.7–46.95 秒背袋皮牌收口。', '01、05 以可见整裤和腰头认款；没有把对标绿色皮牌或款号放进新商品。'),
 '6.1 J96': ('字幕帮助观众跟上展示重点', '开头可见口播字幕，13–19 秒拧转释放，22–27.8 秒后袋近景；文字随阶段改变，不是装饰字幕。', '本批增加与口播对应字幕。对标的温度、速干等宣传不作为新产品事实。'),
 '6.3 J96': ('短链路完成商品说明', '0–7.3 秒整裤与搭臂，12.5–20 秒拧转释放，26.8 秒起背牌收尾；画面依次回答商品、演示、识别。', '05 采用搭配—裤型—裤口收尾。未因两条相似对标而推断同一投放结果。'),
 '6.13 J96': ('连续证据降低理解成本', '9–13.5 秒捏边、13.5–20 秒拧转释放、20–24.7 秒横拉，证据相邻，观众容易把表达和动作对应起来。', '03 疑问紧接裤口演示；04 拉伸之后立即回到裤型。'),
 '6.16 J99': ('需求入口后用转折维持注意', '黄白字幕辅助入口，6.5–9.5 秒捏边，9.5–16 秒拧转释放，20–23 秒横拉；“薄”与“不皱”形成宣传转折，但性能仍要证实。', '06 采用具体裤长选择问题，不借用速干、抗皱和天气功效承诺。'),
 '7.29 J85': ('近远切换来自人物和商品动作', '0–4.3 秒近到中景，4.3–14 秒拧转释放并留屏，21.3–28.8 秒垂落触感，35.2 秒后背牌结束。', '02、06 保留固定机位的真实动作变化；无需外拍、运镜或新增拍摄人员。'),
}

def main():
 inventory=json.loads((CASES/'inventory.json').read_text(encoding='utf-8'))
 rows=[]
 for item in inventory:
  name=item['name']; annotation=json.loads((CASES/name/'references/annotations.json').read_text(encoding='utf-8'))
  assert module.sha(Path(item['source'])) == item['sha256']
  title,why,transfer=WHY[name]
  phases=annotation['phases']; first=phases[0]
  folder=ROOT/'benchmarks'/name; folder.mkdir(parents=True,exist_ok=True)
  depth=f'''# {name} · 千川对标复核 v2

来源：{item['source']}；时长 {item['duration']:.2f} 秒；SHA256：{item['sha256']}。

效果依据：用户说明这条属于已经千川验证、烧出数据的素材。本次未提供花费、ROI、点击率、成交和留存曲线，以下是内容机制假设，不是平台算法因果结论。

本次复用与原片哈希一致的既有密集取样、阶段注释和本地转写。原有取样记录 {item['samples']} 张，不等于逐原始帧全看。既有完整视觉报告：[内容解析]({(CASES/name/'references/01_内容深度解析.md').as_posix()})。

## 主要可迁移点：{title}

{why}

开头可见动作：{first['action']}。这一入口让商品或异常动作先进入视线，用户有具体对象可观察。是否提高 2 秒留存，需要真实投放分时数据验证。

口播入口辅助识别（机器转写，未逐句听审）：{first.get('machine_text','未提供')}。

## 多维观察与边界

- 结构：按下方阶段表记录真实商品/人物动作，阶段不是切镜数；主要证据来自连续固定机位展示。
- 商品展示：{'; '.join(p['label'] for p in phases)}。这是一条实物证明链，不能把对标商品的参数带到别款。
- 表情和动作：明确记录能看见的指示、提腰、拉伸、拧转或释放。未凭静态帧判断真诚、可信、情绪强度或观众心理反应。
- 机位与光线：主体相对固定背景靠近/后退，不能称为镜头推拉；以既有关键帧中的明暗、面料细节可见性为依据。无灯具型号、色温和布光测量，不能反推具体灯位参数。
- 剪辑节奏：让演示的起点、过程和结果完整留屏，省掉无关停顿比盲目加快切镜更符合本次固定直播间素材。
- 字幕：{'有可见字幕，辅助说明阶段。' if name in ['6.1 J96','6.16 J99'] else '不将机器转写当作画面已有字幕；是否烧字幕见既有视觉证据。'}
- 音乐与声音：已确认文件音轨及辅助转写；未人工完整听审音乐，不宣称曲名、BPM、爆点音效或没有 BGM。本批保留实拍原音轨，不额外引入音乐。
- 转化：商品特征与动作对应、最后保持商品可见可能帮助理解；点击和成交机制属于待验证假设，不能凭画面证明成交归因。

## 本次剪辑如何用

{transfer}

以下性能话术不凭对标移用：面料成分、抗皱等级、降温数值、速干时间、品牌获奖、售价和库存。爆款点不等于允许复制商品事实。
'''
  (folder/'01_内容深度解析.md').write_text(depth,encoding='utf-8')
  breakdown=f'# {name} · 连续镜头内动作阶段\n\n以下是阶段表，不能据此断言发生多次切镜。声音列来自待听审机器转写。\n\n|时间（秒）|阶段|画面动作|功能|辅助口播|\n|---|---|---|---|---|\n'
  for p in phases:
   quote=p.get('machine_text','未提供').replace('|','／').replace('\n',' ')
   breakdown+=f"|{p['start_ms']/1000:.2f}–{p['end_ms']/1000:.2f}|{p['label']}|{p['action']}|{p['function']}|{quote}|\n"
  breakdown+=f"\n关键帧与原时间索引：[取样证据]({(CASES/name/'evidence/frame_index.json').as_posix()})。\n"
  (folder/'02_分镜脚本拆解.md').write_text(breakdown,encoding='utf-8')
  rows.append(f'|{name}|{title}|{why}|{transfer}|')
 batch=json.loads((ROOT/'batch.json').read_text(encoding='utf-8'))
 report='''# 千川对标 → 实拍剪辑 · 第一轮 v1

12 条对标原片已核对哈希，与软件已保存的案例来源一致。3 条新素材各约 300 秒，合计约 15 分钟。本轮提取 6 个角度，均为待审核候选，不自动批准或投放。

## 已确认与未确认

用户确认对标已经千川验证；没有量化投放数据和逐秒留存曲线，因此不能判定“某句话被算法抓取导致跑量”。本次确认的是可见动作和内容表达，解释留存/点击的部分是待投放检验的假设。

新素材抽样 039/040 各 151 帧、041 共 150 帧，约每 2 秒一帧，覆盖到结尾；已查看全部抽样帧。041 的候选变化点约 172 秒，需要与原片时间轴一起看，不能据此说发生切镜。语音使用已安装 large-v3-turbo 在本地转写，未上传外部服务、未下载模型；字幕未逐句人工听审。

## 逐条对标差异

|对标|主要内容机制|时间与证据|本批迁移或不迁移|
|---|---|---|---|
'''+ '\n'.join(rows)+'''

## 共同机制

1. 首屏给商品/细节/不寻常动作，让人先知道看什么。
2. 把“有弹力、裤型、触感”等抽象表达放到真实动作旁边。
3. 证明动作保留到结果出现，不能刚拉开就切走。
4. 固定机位仍可以用手部、商品、人物靠近/后退改变信息尺度。
5. 一个成片围绕一个主要购买顾虑；不要把直播间所有报尺码和促单都塞进去。

新素材的主要差距：大段静止持裤与实时观众问答；缺少对标中反复出现的完整拧转—释放动作、男模上身效果和独立细节镜头。本轮依靠已拍到的裤口、腰头、拉伸和口播，未拿对标片补画面，也未混入 040 中另一条浅灰裤。

## 成片清单

|版本|时长|源片及选段|取舍|
|---|---|---|---|
'''
 cards=[]
 for c in batch['candidates']:
  qa=json.loads((ROOT/'videos'/c['id']/'technical-qa.json').read_text(encoding='utf-8'))
  spans='、'.join(f"{p['start_ms']/1000:.2f}–{p['end_ms']/1000:.2f}秒" for p in c['clips'])
  report+=f"|{c['id']} {c['title']}|{qa['duration']:.2f}秒|{Path(c['source_path']).stem[-3:]}：{spans}|{c['hook']}|\n"
  cards.append(f'<article><h2>{html.escape(c["id"]+" · "+c["title"])}</h2><video controls preload="metadata" poster="{c["cover_path"]}" src="{c["video_path"]}"></video><p>{html.escape(c["hook"])}</p><p>原片 {Path(c["source_path"]).stem[-3:]}：{spans}</p><p>对标：{html.escape("、".join(c["benchmark_refs"]))}</p><ul>'+''.join(f'<li>{html.escape(n)}</li>' for n in c['review_notes'])+f'</ul><a download href="{c["video_path"]}">保存视频</a> · <a href="{c["subtitle_path"]}">查看字幕</a></article>')
 report+='''

## 声音、字幕、画面

每段画面与该段原声一起裁切，没有配音替换；多段版用完整口播段拼接，不声称整条连续一镜到底。03、04 为连续选段。所有候选仅来自各自同一个原片文件。字幕保留 ASR 原始记录，常见同音词规范化映射在可复现脚本中，未把机翻称为听审通过。因本机 libass 对 Noto 可变字体选择成 Thin，本次成片使用已安装微软雅黑粗体，软件 UI 字体不变。

1080×1920，30fps，H.264/AAC；字幕字号 52 视频像素、标题 60，描边保证对比度。标题只在开头出现，结尾提示查看商品和按尺码表选择；没有臆造商品链接编号、价格、销量、库存和优惠。本轮未新加 BGM、音效和磨皮滤镜，保留商品原始颜色和原片音轨。

## 审核顺序

建议先看 03（裤脚连续证明）、04（弹力与裤型），再看 02（版型共鸣）。其他版本用来比较颜色搭配、衣橱场景和裤长选择。查看字幕是否听写准确、剪口是否顺、表情是否合适；尺码/弹力等商品事实按你实际款核对。通过软件保存“待审核 / 需要修改 / 审核通过”，通过不会自动投放。

这些候选不是投放保证：首轮内容与技术检查完成后，仍要由你确认原话、商品信息和最终观感。未提供的音乐授权、投放数据不作为已完成项目。
'''
 (ROOT/'01_内容深度解析.md').write_text(report,encoding='utf-8')
 cuts='# 新素材 → 成片剪辑决策\n\n保留原声、同原片、完整语义段；按输出顺序排列，非原片自然时间顺序。\n\n'
 for c in batch['candidates']:
  cuts+=f"## {c['id']} {c['title']}\n\n{c['hook']}\n\n|成片时间|原片时间|处理|\n|---|---|---|\n"
  total=0
  for p in c['clips']:
   duration=p['end_ms']-p['start_ms']; cuts+=f"|{total/1000:.2f}–{(total+duration)/1000:.2f}秒|{p['start_ms']/1000:.2f}–{p['end_ms']/1000:.2f}秒|画面原声同步截取，字幕随该句原时间平移|\n"; total+=duration
  cuts+=f"\n[字幕逐句稿](videos/{c['id']}/subtitles.srt) · [原剪辑决策](videos/{c['id']}/edit-decision.json)\n\n"
 (ROOT/'02_分镜脚本拆解.md').write_text(cuts,encoding='utf-8')
 page='''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>千川对标 · 六版成片待审核</title><style>body{margin:0;background:#f5f6f7;color:#202124;font:17px/1.65 "Microsoft YaHei",sans-serif}main{max-width:1500px;margin:auto;padding:24px}h1{font-size:28px}h2{font-size:21px}section{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,380px),1fr));gap:24px}article{background:white;padding:24px;border:1px solid #dfe1e5;border-radius:8px;min-width:0}video{display:block;height:520px;max-width:100%;aspect-ratio:9/16;margin:auto;background:#111}a{color:#b51e40}li{margin:8px 0}</style><main><h1>千川对标 · 六版成片待审核</h1><p>真实实拍剪辑 · 2026-09-15 · 建议先看 03 和 04。此离线页面只看片，审核请在软件“后期剪辑 → 实拍素材剪辑”中保存。</p><p><a href="01_内容深度解析.md">12 条对标分析与迁移依据</a> · <a href="02_分镜脚本拆解.md">每版原片时间码</a></p><section>'''+''.join(cards)+'</section></main></html>'
 (ROOT/'成片审核.html').write_text(page,encoding='utf-8')
 print('Reports and offline review index ready.')

if __name__=='__main__': main()

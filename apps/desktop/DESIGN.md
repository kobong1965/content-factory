---
name: 爆款内容工厂
description: 面向内部服装内容团队的渐变演播工作台
colors:
  primary: "#315fc5"
  primary-dark: "#244b9f"
  primary-soft: "#eaf0ff"
  navy: "#172d4f"
  nav-text: "#d8e4f7"
  nav-active: "#e5edff"
  surface: "#fff"
  surface-soft: "#f1f5fd"
  ink: "#182944"
  text: "#283a54"
  muted: "#53657f"
  line: "#dbe3ef"
  line-strong: "#a2b2c9"
  focus: "#2351b7"
  danger: "#aa3029"
  danger-soft: "#fff2ef"
  success: "#14715c"
  success-soft: "#e8f5ef"
  warning: "#99530f"
  warning-soft: "#fff2dc"
typography:
  headline:
    fontFamily: '"Noto Sans SC", "Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI", sans-serif'
    fontSize: "28px"
    fontWeight: 700
    lineHeight: 1.3
    letterSpacing: "-0.02em"
  title:
    fontFamily: '"Noto Sans SC", "Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI", sans-serif'
    fontSize: "20px"
    fontWeight: 700
    lineHeight: 1.3
  body:
    fontFamily: '"Noto Sans SC", "Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI", sans-serif'
    fontSize: "17px"
    fontWeight: 400
    lineHeight: 1.62
  button:
    fontFamily: '"Noto Sans SC", "Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI", sans-serif'
    fontSize: "16px"
    fontWeight: 700
    lineHeight: 1.45
  label:
    fontFamily: '"Noto Sans SC", "Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI", sans-serif'
    fontSize: "15px"
    fontWeight: 500
    lineHeight: 1.45
  assist:
    fontFamily: '"Noto Sans SC", "Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI", sans-serif'
    fontSize: "14px"
    lineHeight: 1.45
rounded:
  sm: "8px"
  md: "14px"
  interaction: "10px"
  status: "6px"
spacing:
  space-1: "0.25rem"
  space-2: "0.5rem"
  space-3: "0.75rem"
  space-4: "1rem"
  space-6: "1.5rem"
  space-8: "2rem"
components:
  button-primary:
    backgroundColor: "{colors.primary}"
    textColor: "{colors.surface}"
    typography: "{typography.button}"
    rounded: "{rounded.sm}"
    padding: "10px 16px"
  button-primary-hover:
    backgroundColor: "{colors.primary-dark}"
    textColor: "{colors.surface}"
  button-secondary:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    typography: "{typography.button}"
    rounded: "{rounded.sm}"
    padding: "10px 16px"
  project-input:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    rounded: "{rounded.sm}"
    padding: "8px 12px"
  nav-item:
    textColor: "{colors.nav-text}"
    rounded: "{rounded.interaction}"
    padding: "14px 16px"
  nav-item-active:
    backgroundColor: "{colors.nav-active}"
    textColor: "{colors.navy}"
    rounded: "{rounded.interaction}"
  sku-filter-selected:
    backgroundColor: "{colors.primary}"
    textColor: "{colors.surface}"
    rounded: "{rounded.sm}"
    padding: "10px 16px"
  work-panel:
    backgroundColor: "{colors.surface}"
    rounded: "{rounded.md}"
    padding: "24px"
  status-failed:
    backgroundColor: "{colors.danger-soft}"
    textColor: "{colors.danger}"
    rounded: "{rounded.status}"
    padding: "4px 8px"
  status-completed:
    backgroundColor: "{colors.success-soft}"
    textColor: "{colors.success}"
    rounded: "{rounded.status}"
    padding: "4px 8px"
---

# Design System: 爆款内容工厂

## Overview

**Creative North Star: "渐变演播工作台"**

工作台以雾蓝到浅桃的连续背景承接多个内容工作区，海军蓝导航稳定定位，白色工作面承载编辑、证据与审核。钴蓝集中标记操作与当前选择，深色中文和明确的间距服务长时间批量工作。

页面密度来自并列工作区、可换行的元信息和清晰的主次操作。背景提供气氛，内容区保留实体表面；真实素材、处理状态和失败原因始终能读懂。品牌沿用现有标记，操作图标使用统一 SVG 线条，本轮没有新增栅格装饰资源。

**Key Characteristics:**

- 雾蓝至浅桃的全工作区渐变，配海军蓝导航与白色内容表面。
- Noto Sans SC 中文为主，正文与按钮分别使用独立字号角色。
- 项目选择、当前阶段、状态和操作分别表达，选中态在悬停与键盘聚焦时继续清晰可辨。
- 深度主要由背景色、间距和细线形成，普通工作面保持平整。

本文记录 0.1.33 当前实现，以最后加载的 `src/studio-theme.css` 及其实际继承样式为依据。方向采用 code-led 实施；不存在已批准效果图的声明。surface brief 记录 seed `c197a70d`，原始抽取输出未保留，未独立验证。实现与验证范围见 `../../docs/qa/2026-09-22-gradient-and-media-path-0.1.33.md`；验收证据及原生运行、真实系统缩放与付费任务的边界以该报告为准。

## Colors

钴蓝、海军蓝和冷白组成操作骨架，雾蓝与浅桃只负责背景氛围；状态色保留独立语义。

### Primary

- **钴蓝操作色**（`primary`）：主按钮、当前处理阶段和选中的款号筛选。
- **深钴蓝**（`primary-dark`）：主操作悬停、文字操作及选中筛选的悬停与聚焦底色。
- **浅蓝选择面**（`primary-soft`）：继承工作区的轻量选择与提示表面。

### Secondary

- **海军蓝**（`navy`）：侧栏实体底色、页面标题和浅底选中导航的文字。
- **浅蓝导航文字**（`nav-text`）：深侧栏上的未选中导航。
- **冰蓝导航选择面**（`nav-active`）：当前主工作区，配海军蓝文字。
- **背景渐变**：`linear-gradient(118deg, #cfdef6 0%, #d8e4fb 34%, #e6dff1 64%, #f3ddd3 100%)`。这是整体工作区材料，不是按钮或表单底色。

### Neutral

- **实体白工作面**（`surface`）：项目详情、新建项目、导入与 Skill 面板。
- **浅蓝辅助面**（`surface-soft`）：嵌套信息和次级区域的公共底色；项目队列另有接近的浅蓝表面。
- **深墨标题与正文**（`ink`、`text`）：标题、字段和正文。
- **次级文字**（`muted`）：说明、元数据和非主要信息。
- **分隔线与较强边界**（`line`、`line-strong`）：内容分区、继承表单和弹窗边界。

状态色沿用 `danger` / `danger-soft`、`success` / `success-soft`、`warning` / `warning-soft` 三组，分别表达失败、审核或完成、处理中；状态文字始终同时出现。

**The Readable Surface Rule.** 渐变属于工作区背景，编辑与审核内容使用实体表面，文字不能直接依赖渐变中的某个位置获得对比。

**The Persistent Selection Rule.** 款号筛选选中后保持白字深蓝底；悬停和键盘聚焦只加深底色，不能退回浅色次按钮背景。

## Typography

**Display Font:** 无独立展示字体；页面标题沿用 Noto Sans SC。
**Body Font:** 内置 Noto Sans SC，后备顺序见前置 token。
**Label/Mono Font:** 标签沿用正文字体；技术诊断沿用代码区域的等宽呈现，不另立品牌字体。

中文字符保持端正、均匀、可连续阅读。字体通过本地 `/fonts/NotoSansSC-VF.ttf` 加载；没有网络字体依赖，也没有以系统字体替代展示字体的设计决定。

### Hierarchy

- **Headline**：工作区标题，使用 `headline`。
- **Title**：公共分区标题，使用 `title`；项目详情标题按实际组件采用更强一级的尺寸（22px），队列及部分次标题为18px。
- **Body**：正文使用 `body`，普通段落最大行长沿用75ch；错误解释单独收窄至72ch。
- **Button**：主要与次要按钮使用 `button`；导航常规字重500、当前导航700。
- **Label**：表单标签使用 `label`，新建项目表单局部字重600。
- **Assist**：时间、款号、说明与状态使用 `assist`；说明段落可以增加行高。

以上为默认字号。已有大字号偏好继续通过根元素属性继承；本轮没有恢复字号切换入口。CSS 视口与文字放大检查不能替代真实 Windows DPI 验证。

**The Readable Density Rule.** 通过布局重排、换行和独立滚动容纳内容，不能用缩小正文或截掉失败信息消除溢出。

## Layout

桌面采用外层导航加工作区，内容最大宽度2048px。默认侧栏宽224px，外层留16px边距和24px间隔；侧栏为悬浮式实体面，顶部和底部都保留背景。主工作区内部主要间距沿用4px基础节奏及8 / 12 / 16 / 24 / 32px尺度。

剪辑页在宽屏将项目队列与详情并列，队列宽度在256–320px之间，详情占据剩余宽度，区间距20px。宽度至少1100px时，两区分别滚动并保留稳定滚动条空间；这是一种长列表工作模式，不是所有页面都必须复制的固定三栏模板。新建项目将素材上传和参数并列，较窄时顺序排列。

- 1280px及以下：侧栏变窄，项目轨缩至230–280px，间距和详情内边距收紧；阶段条可以分行。
- 1000px及以下：导航移至上方横向排列，项目详情改为单列，项目列表可为双列；移除两区最大高度限制。
- 560px及以下：项目列表单列，主要面板内边距16px，导航图标与文字上下排列，阶段条两列。

长项目名、原片名、技术路径和提示允许换行。控件默认最小高度44px；项目管理工具栏有经过单独定义的紧凑按钮，不应把该局部高度推广为主操作标准。

## Elevation & Depth

渐变背景、深侧栏、浅蓝队列和白色工作面形成层级，普通卡片和项目选择行不使用悬浮阴影。选中项目通过白底与较强边界确定位置。弹窗仍使用已存在的柔和投影，遮罩区分当前操作与背景；这是功能性浮层。

### Shadow Vocabulary

- **Modal**（`0 20px 50px rgb(16 23 21 / 24%)`）：继承的共享弹窗投影，不用于每张内容卡片。
- **Flat panel**（`none`）：普通项目面板与项目选择行。

**The Tonal Depth Rule.** 主工作区先用底色和间距区分层级，投影只沿用已有浮层用途。

## Shapes

按钮和项目表单使用小圆角，主要工作面使用中圆角，项目行及文件选择区域使用交互圆角。状态标签使用更紧凑圆角且允许文字换行。全局导航外壳圆角16px，属于大容器轮廓，不推广为所有按钮形状。阶段编号为小圆形标记，表达顺序；不把普通数字指标都包成圆徽章。

图标为20px SVG、24×24 viewBox、1.6px圆端线条，与文字并排使用。图标本身不代替按钮的可读名称。

## Components

### Buttons

主按钮使用钴蓝实体底与白字，次按钮使用白底、深字和细蓝灰边界；默认内边距与圆角见前置 token。主按钮悬停加深，次按钮悬停使用浅蓝底和更明显的边界。键盘焦点保留可见蓝色外框；默认外框为3px、偏移3px，导航沿用2px的局部外框。禁用控件保留既有不可点击和降低透明度行为。

状态变化只使用颜色和边框的短过渡，常用控件为180ms ease-out；减少动画偏好下不启用该过渡。按钮文字描述当前动作，保存与启动在流程中有各自独立入口。

### Chips

状态标签为文字加柔和语义底色，内边距见前置 token。失败、完成/待审核、处理中分别绑定现有状态，不把标签颜色当作唯一依据。款号筛选是可按下的按钮组；选中及 hover / focus 都保留白字深蓝底。

### Cards / Containers

主要工作面为白色、无常规投影、中圆角和24px内边距。队列采用浅蓝区域，未选项目透明，悬停略加深，已选项目白底加细边。项目行的标题、来源数量、款号和状态纵向组织，文字可换行。

成片批次是可进入的白色文件夹卡片，悬停出现浅蓝底和细外框。普通信息区使用细分隔线，无需再增加一层独立卡片。

### Inputs / Fields

项目输入框为白底、深字、小圆角和可辨边界，文字16px，最小高度44px。标签位于控件上方，辅助说明紧随所属输入区。文件上传区域使用浅蓝底及虚线边界；原生文件按钮保留可操作表面。焦点可见，禁用状态跟随正在提交的表单状态。

### Navigation

主导航固定为三个工作区入口，设置与版本操作置于侧栏尾部。常规项使用浅字透明底，悬停加亮，当前项使用冰蓝底和海军蓝文字。每个入口都有 SVG 图标与中文名称，当前项同时暴露 `aria-current`。窄屏迁移为顶部导航，不缩成不可读图标列。

### Processing Stage & Failure Details

处理阶段为带编号的顺序列表，只给当前步骤实体钴蓝标记，同时设置 `aria-current="step"`。状态标签和具体失败信息负责表达结果，阶段条不制造已完成的承诺。

错误区域使用浅红背景、细红边界、可读原因和可展开技术详情。原始错误保留在允许换行、限制高度并可滚动的代码区；解释与原文并存。重点词颜色等低对比业务参数用色块加深色数值展示，避免白底黄字。

## Do's and Don'ts

### Do:

- **Do** 保留渐变背景、海军蓝导航、白色工作面与钴蓝操作的角色关系。
- **Do** 使用本地 Noto Sans SC 和已实现的默认字号层级。
- **Do** 同时使用状态文字、语义颜色和可见焦点表达交互。
- **Do** 检查选中态的 hover / focus，以及长名称、路径和错误详情的换行。
- **Do** 复用现有品牌标记与线性 SVG 图标，素材预览使用真实关联内容。

### Don't:

- **Don't** 把编辑工作面替换为半透明渐变或重新铺回整页灰白背景。
- **Don't** 通过缩小正文、裁切提示或隐藏主操作解决窗口变窄。
- **Don't** 将绿色旧主题、演示统计或未经核实的处理结果继承为新视觉规范。
- **Don't** 把验证演示图、seed 记录或浏览器视口模拟描述为用户批准效果图或真实系统缩放验收。

未规范化：旧样式文件中被覆盖的历史配色和字号、修正前款号筛选的白字浅底悬停、一次性演示数据及不可独立验证的 seed 推导；它们不是可供后续页面继承的视觉规则。


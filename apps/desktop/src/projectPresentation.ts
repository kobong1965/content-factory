export function projectFailureSummary(error: string): { title: string; message: string } {
  if (/剪辑选段不能重叠/.test(error)) {
    return { title: '上次剪辑被旧版重复选段规则拦截', message: '当前版本允许同一成片内及不同成片之间重复使用片段，也允许源选段区间重叠。原项目和素材已保留，可重新尝试；重新规划可能产生模型费用，数量、时长和素材边界仍须通过检查。' };
  }
  if (/成片时长不在用户设置范围内|剪辑方案数量与用户设置不一致|剪辑选段超出源素材范围/.test(error)) {
    return { title: '剪辑方案不合格，已阻止生成成片', message: `${error} 项目和素材仍保留，系统不会放宽时长或强行截断口播。重新尝试会重新规划，可能产生模型费用，也不保证新方案一定合格。` };
  }
  if (error.startsWith('暂不支持生成：')) {
    return { title: '生成条件未满足', message: `${error} 请先处理上述问题；本次检查未调用模型。` };
  }
  if (/本地语音识别失败.*API 语音识别也不可用/i.test(error)) {
    return {
      title: '本地与 API 语音识别均不可用',
      message: `${error} 请配置真正支持 /audio/transcriptions 的语音模型和有效 API Key；仅支持文字/图片的模型不能替代语音转写。项目参数和素材已保留。`,
    };
  }
  if (/本地语音|原音频|语音识别|字幕未覆盖|ASR|对齐/i.test(error)) {
    return {
      title: '本地语音识别失败',
      message: `${error} 软件会优先使用本地模型；本地模型不可用时才尝试已配置的 API 语音识别。请检查本地模型/运行时，或在模型连接中配置支持 /audio/transcriptions 的语音模型。项目参数和素材已保留。`,
    };
  }
  if (/no such file|errno 2|winerror 206|path.{0,12}long|路径.{0,8}过长/i.test(error)) {
    return { title: '视频处理文件无法访问', message: '处理缓存或素材路径无法访问，不一定是原素材丢失。更新后的程序会使用较短的处理路径；确认素材仍可访问后，可手动重新尝试。' };
  }
  if (/api|timeout|timed out|connection|模型|接口|连接/i.test(error)) {
    return { title: '模型服务连接未完成', message: '项目和已保存的参数仍保留。请检查模型连接或余额，恢复后再重新尝试。' };
  }
  return { title: '这次处理未完成', message: '项目参数已保留。请展开技术详情检查原因；重新尝试可能重新调用模型并产生费用。' };
}

export function projectStage(status: string, progress: number): number {
  if (['review', 'completed'].includes(status)) return 4;
  if (['rendering', 'render_pending'].includes(status)) return 3;
  if (status === 'planning') return 2;
  if (status === 'analyzing') return 1;
  // The old persisted progress is a checkpoint, not proof of completion.
  if (['failed', 'cancelled'].includes(status)) return progress >= 60 ? 3 : progress >= 30 ? 2 : progress > 0 ? 1 : 0;
  return 0;
}

import { describe, expect, it } from "vitest";
import { appendAutoEditFiles, autoEditStatusLabel, canRetryRegistration, draftFromProject, draftSettings, selectDurationPolicy, validateAutoEditDraft } from "./autoEditing";

describe('registration retry presentation', () => {
  const checkpoint = { manifest_path: 'outputs/auto_example/manifest.json', manifest_sha256: 'a'.repeat(64) };

  it('identifies only failed projects with a saved registration checkpoint', () => {
    expect(canRetryRegistration({ status: 'failed', registration_checkpoint: checkpoint })).toBe(true);
    expect(canRetryRegistration({ status: 'rendering', registration_checkpoint: checkpoint })).toBe(false);
    expect(canRetryRegistration({ status: 'review', registration_checkpoint: checkpoint })).toBe(false);
  });

  it('does not promise registration-only recovery for older failures', () => {
    expect(canRetryRegistration({ status: 'failed' })).toBe(false);
    expect(canRetryRegistration({ status: 'failed', registration_checkpoint: null })).toBe(false);
  });
});

describe("automatic editing project draft", () => {
  it('converts a failed project into editable generation conditions without changing its persisted settings', () => {
    const project = {
      title: '失败批次',
      settings: {
        target_count: 5,
        duration_min_ms: 20000,
        duration_max_ms: 40000,
        subtitle_font_size: 68,
        keyword_color: '#FFD400',
        keyword_scale: 1.3,
        top_title_enabled: false,
        subtitle_font: 'yahei' as const,
        subtitle_effect: 'none' as const,
        subtitle_mode: 'sentence' as const,
      },
    };
    expect(draftFromProject(project)).toEqual({
      title: '失败批次', sku: '', targetCount: 5, durationMinSeconds: 20,
      durationMaxSeconds: 40, durationPolicy: 'custom', subtitleFontSize: 68, keywordColor: '#FFD400',
      keywordScale: 1.3, subtitleFont: 'yahei', subtitleEffect: 'none', subtitleMode: 'sentence',
    });
    expect(project.settings.duration_min_ms).toBe(20000);
  });

  it('appends multiple files without replacing previous selection and prevents duplicates', () => {
    const first = { name: '第一条.mp4', size: 10, lastModified: 1 } as File;
    const second = { name: '第二条.mp4', size: 20, lastModified: 2 } as File;
    const original = [first];
    expect(appendAutoEditFiles(original, [first, second])).toEqual([first, second]);
    expect(original).toEqual([first]);
    expect(() => appendAutoEditFiles([], Array.from({length:21}, (_, i) => ({name:`${i}.mp4`,size:1,lastModified:1} as File)))).toThrow('20');
  });
  const valid = {
    title: "J85 直播录播第一批",
    targetCount: 5,
    durationMinSeconds: 20,
    durationMaxSeconds: 40,
    subtitleFontSize: 68,
    keywordColor: "#FFD400",
    keywordScale: 1.3,
  };

  it("accepts the product defaults", () => {
    expect(validateAutoEditDraft(valid)).toBeNull();
  });

  it('switches a legacy failure to 15–30 seconds and persists the selected caption mode', () => {
    const edited = {...selectDurationPolicy(valid, 'bounded_15_30'), subtitleMode: 'auto' as const};
    expect(validateAutoEditDraft(edited)).toBeNull();
    expect(draftSettings(edited)).toMatchObject({duration_min_ms:15000, duration_max_ms:30000, duration_policy:'bounded_15_30', subtitle_mode:'auto'});
    expect(valid.durationMaxSeconds).toBe(40);
    expect(validateAutoEditDraft({...edited,durationMaxSeconds:40})).toContain('15—30');
    expect(validateAutoEditDraft({...edited,durationMinSeconds:NaN})).not.toBeNull();
  });

  it('persists the explicit original-audio-only mode without requiring ASR settings', () => {
    const edited = {...valid, subtitleMode: 'none' as const};
    expect(validateAutoEditDraft(edited)).toBeNull();
    expect(draftSettings(edited).subtitle_mode).toBe('none');
  });

  it("rejects invalid count and duration without clearing the draft", () => {
    expect(validateAutoEditDraft({ ...valid, targetCount: 21 })).toContain("1—20");
    expect(validateAutoEditDraft({ ...valid, durationMinSeconds: 50, durationMaxSeconds: 20 })).toContain("时长");
    expect(valid.title).toBe("J85 直播录播第一批");
  });

  it("has explicit labels for every persisted queue state", () => {
    for (const status of ["draft", "queued", "analyzing", "planning", "render_pending", "rendering", "review", "failed", "cancelled"] as const) {
      expect(autoEditStatusLabel(status)).not.toContain(status);
    }
  });
});

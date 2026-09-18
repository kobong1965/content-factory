import { describe, expect, expectTypeOf, it } from "vitest";

import {
  evidenceSourceTypes,
  jobStatuses,
  PROJECT_NAME,
  s0Modules,
  s1EngineeringChecks,
  s1SchemaCatalog,
  mediaTaskStatuses,
  mediaTaskSteps,
  analysisTaskStatuses,
  analysisTaskSteps,
  gatewayInputModalities,
  gatewayProviders,
  gatewayPurposeRequirements,
  gatewayPurposes,
  ANALYSIS_REPORT_VERSION,
  SCRIPT_PACKAGE_VERSION,
  MATERIAL_ASSET_VERSION,
  EDIT_PROJECT_VERSION,
  PUBLICATION_VERSION,
  type GatewaySettings,
  type GatewaySettingsV21,
  type GatewayProvider,
  type AnalysisReport,
  type AnalysisTask,
  type ViralSkill,
  VIRAL_SKILL_VERSION,
} from "./index";

describe("S1 contract catalog", () => {
  it("publishes the five frozen schemas", () => {
    expect(s1SchemaCatalog.map((schema) => schema.kind)).toEqual([
      "analysis",
      "product",
      "script",
      "gold_case",
      "gold_manifest",
    ]);
  });

  it("keeps evidence and engineering readiness explicit", () => {
    expect(evidenceSourceTypes).toContain("manual_annotation");
    expect(s1EngineeringChecks).toHaveLength(5);
  });

  it("retains the S0 job lifecycle", () => {
    expect(jobStatuses[0]).toBe("pending_analysis");
    expect(jobStatuses.at(-1)).toBe("published");
  });
});

describe("shared project contracts", () => {
  it("keeps the confirmed project identity", () => {
    expect(PROJECT_NAME).toBe("爆款内容工厂");
  });

  it("keeps the complete confirmed workflow in order", () => {
    expect(jobStatuses[0]).toBe("pending_analysis");
    expect(jobStatuses.at(-1)).toBe("published");
    expect(new Set(jobStatuses).size).toBe(jobStatuses.length);
  });

  it("exposes every S0 engineering boundary", () => {
    expect(s0Modules.map((module) => module.id)).toEqual([
      "desktop",
      "api",
      "media",
      "contracts",
      "archive",
    ]);
  });
});

describe("S2 media contracts", () => {
  it("freezes the recoverable queue states", () => {
    expect(mediaTaskStatuses).toEqual(["pending", "running", "retry_wait", "completed", "failed"]);
    expect(mediaTaskSteps).toContain("recovered");
    expect(mediaTaskSteps.at(-1)).toBe("failed");
  });
});

describe("S3 deep analysis contracts", () => {
  it("publishes the recoverable analysis lifecycle", () => {
    expect(analysisTaskStatuses).toEqual(["pending", "running", "retry_wait", "succeeded", "completed", "failed", "cancelled"]);
    expect(analysisTaskSteps).toContain("segment");
    expect(analysisTaskSteps).toContain("summarizing");
    expect(analysisTaskSteps).toContain("gateway");
    expect(analysisTaskSteps).toContain("recovered");
  });

  it("bumps the analysis report contract for review metadata", () => {
    expect(ANALYSIS_REPORT_VERSION).toBe("1.1.0");
  });

  it("exposes the analysis versus video-review purpose on new task results", () => {
    expectTypeOf<AnalysisTask["model_purpose"]>()
      .toEqualTypeOf<"analysis" | "video_review" | undefined>();
    expectTypeOf<AnalysisReport["processing"]["purpose"]>()
      .toEqualTypeOf<"analysis" | "video_review" | undefined>();
  });

  it("publishes the formal viral Skill lifecycle separately from report candidates", () => {
    expect(VIRAL_SKILL_VERSION).toBe("1.0.0");
    expectTypeOf<ViralSkill["status"]>().toEqualTypeOf<"approved" | "disabled">();
    expectTypeOf<ViralSkill["reuse_mode"]>().toEqualTypeOf<"reuse" | "avoid">();
  });

  it("publishes the multimodal model registry and explicit task routes", () => {
    const settings: GatewaySettings = {
      schema_version: "2.1.0",
      base_url: "https://relay.example.com/v1",
      model: "gpt-vision-model",
      api_mode: "responses",
      api_key_configured: true,
      default_model_id: "model_gpt",
      models: [
        {
          model_id: "model_gpt",
          display_name: "GPT 多模态模型",
          provider: "openai",
          base_url: "https://api.openai.com/v1",
          model: "gpt-vision-model",
          api_mode: "responses",
          modalities: ["text", "image"],
          purposes: ["analysis", "script", "material", "video_review"],
          enabled: true,
          api_key_configured: true,
        },
        {
          model_id: "model_qwen",
          display_name: "Qwen 多模态模型",
          provider: "qwen",
          base_url: "https://qwen.example.com/v1",
          model: "qwen-vision-model",
          api_mode: "chat_completions",
          modalities: ["text", "image"],
          purposes: ["analysis", "script", "material", "video_review"],
          enabled: true,
          api_key_configured: true,
        },
      ],
      routing: {
        analysis: "model_gpt",
        script: "model_qwen",
        material: "model_qwen",
        video_review: "model_gpt",
      },
      updated_at: "2026-08-31T00:00:00Z",
    };

    expect(gatewayProviders).toEqual(["openai", "qwen", "openai_compatible", "custom"]);
    expect(gatewayInputModalities).toEqual(["text", "image"]);
    expect(gatewayPurposes).toEqual(["analysis", "script", "material", "video_review"]);
    expect(gatewayPurposeRequirements.video_review).toEqual(["text", "image"]);
    expect(settings.routing.analysis).toBe(settings.default_model_id);
    expect(settings.routing.video_review).toBe("model_gpt");
    expect(settings.models.map((model) => model.provider)).toEqual(["openai", "qwen"]);
    expect(settings.models.every((model) => model.modalities.includes("image"))).toBe(true);
    expectTypeOf<GatewaySettingsV21["models"][number]>()
      .toMatchTypeOf<{ provider: GatewayProvider }>();
    expectTypeOf<GatewaySettingsV21["routing"]>()
      .toMatchTypeOf<{ video_review: string | null }>();
  });

  it("keeps persisted schema 2.0.0 settings source-compatible", () => {
    const legacySettings: GatewaySettings = {
      schema_version: "2.0.0",
      base_url: "https://relay.example.com/v1",
      model: "legacy-model",
      api_mode: "chat_completions",
      api_key_configured: true,
      default_model_id: "model_legacy",
      models: [{
        model_id: "model_legacy",
        display_name: "旧版模型",
        base_url: "https://relay.example.com/v1",
        model: "legacy-model",
        api_mode: "chat_completions",
        modalities: ["text", "image"],
        purposes: ["analysis", "script", "material"],
        enabled: true,
        api_key_configured: true,
      }],
      routing: {
        analysis: "model_legacy",
        script: "model_legacy",
        material: "model_legacy",
      },
      updated_at: null,
    };

    expect(legacySettings.models[0]?.provider).toBeUndefined();
    expect(legacySettings.routing.video_review).toBeUndefined();
  });
});

describe("S5 script contracts", () => {
  it("publishes the auditable script package version", () => {
    expect(SCRIPT_PACKAGE_VERSION).toBe("1.1.0");
  });
});

describe("S6 material contracts", () => {
  it("publishes the versioned material asset boundary", () => {
    expect(MATERIAL_ASSET_VERSION).toBe("1.0.0");
  });
});

describe("S7 editing contracts", () => {
  it("publishes the versioned edit project boundary", () => {
    expect(EDIT_PROJECT_VERSION).toBe("1.0.0");
  });
});

describe("S8 publishing feedback contracts", () => {
  it("publishes the versioned publication boundary", () => {
    expect(PUBLICATION_VERSION).toBe("1.0.0");
  });
});

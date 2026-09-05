import { clientLog } from "./consoleLog.svelte";

export interface LlmProvider {
  id: string;
  label: string;
  notes?: string;
  docs_url?: string;
  api_key?: "required" | "optional" | "none";
  key_hint?: string;
  cloud?: boolean;
  base_url?: string;
  supports_pull?: boolean;
}

export interface DetectedServer {
  label: string;
  base_url: string;
  provider: string;
  model_count?: number;
  needs_api_key?: boolean;
  models?: { id: string }[];
}

export const llmCatalog = $state({
  providers: [] as LlmProvider[],
  inContainer: false,
  containerHost: "",
});

export const llmSettings = $state({
  baseUrl: "",
  modelName: "",
  apiKey: "",
  apiKeyMasked: false,
  contextTokens: "",
  embeddingModel: "",
  provider: "",
  allowCloud: false,
  urlNote: "",
  testStatus: "",
  testTone: "info" as "info" | "ok" | "error",
});

export const modelsBrowser = $state({
  open: false,
  installed: [] as { id: string }[],
  pulling: {} as Record<string, "pulling" | "ok" | "error">,
});

const POPULAR_MODELS = [
  { id: "llama3.2", label: "Llama 3.2 (3B)" },
  { id: "llama3.1", label: "Llama 3.1 (8B)" },
  { id: "mistral", label: "Mistral (7B)" },
  { id: "gemma2", label: "Gemma 2 (9B)" },
  { id: "qwen2.5", label: "Qwen 2.5 (7B)" },
  { id: "phi4", label: "Phi-4 (14B)" },
  { id: "deepseek-r1", label: "DeepSeek R1 (7B)" },
];

export function popularModels() {
  return POPULAR_MODELS;
}

export function getProvider(id: string): LlmProvider | null {
  return llmCatalog.providers.find((p) => p.id === id) || null;
}

export async function loadProviderCatalog(): Promise<void> {
  try {
    const res = await fetch("/api/llm/providers");
    if (!res.ok) return;
    const body = await res.json();
    llmCatalog.providers = body.providers || [];
    llmCatalog.inContainer = !!body.in_container;
    llmCatalog.containerHost = body.container_host || "";
  } catch {
    /* server not reachable; the plain URL field still works */
  }
}

export function providerNotes(provider: LlmProvider): string {
  let notes = provider.notes || "";
  if (llmCatalog.inContainer && !provider.cloud) {
    notes +=
      ` AmicoScript is running in a container, so a server on your machine is ` +
      `reached at ${llmCatalog.containerHost} — addresses you type as localhost are ` +
      `converted automatically.`;
  }
  return notes.trim();
}

export function applyProvider(providerId: string, { fillUrl = false }: { fillUrl?: boolean } = {}): void {
  const provider = getProvider(providerId);
  if (!provider) return;
  llmSettings.provider = provider.id;
  if (fillUrl && provider.base_url) llmSettings.baseUrl = provider.base_url;
}

export async function detectServers(): Promise<{ servers: DetectedServer[]; status: string; tone: "ok" | "error" }> {
  try {
    const res = await fetch("/api/llm/detect");
    const body = await res.json();
    const servers: DetectedServer[] = body.servers || [];
    if (!servers.length) {
      return {
        servers: [],
        tone: "error",
        status: `Nothing answered on ${(body.scanned || []).length} addresses. Start Ollama, LM Studio or Unsloth Studio and scan again, or enter an address by hand.`,
      };
    }
    clientLog(`LLM scan found: ${servers.map((s) => s.label).join(", ")}`);
    return { servers, tone: "ok", status: `Found ${servers.length} server${servers.length > 1 ? "s" : ""}.` };
  } catch (err) {
    return { servers: [], tone: "error", status: `Scan failed: ${(err as Error).message}` };
  }
}

export function useServer(server: DetectedServer): void {
  applyProvider(server.provider);
  llmSettings.baseUrl = server.base_url;
  if (!llmSettings.modelName && server.models?.length) {
    llmSettings.modelName = server.models[0].id;
  }
  window.dispatchEvent(new CustomEvent("amicoscript:llm-config-changed"));
}

export async function saveLlmSettings(): Promise<void> {
  const fd = new FormData();
  const baseUrl = llmSettings.baseUrl.trim();
  const model = llmSettings.modelName.trim();
  fd.append("llm_base_url", baseUrl);
  fd.append("llm_model_name", model);
  fd.append("llm_api_key", llmSettings.apiKeyMasked ? "__unchanged__" : llmSettings.apiKey);
  if (llmSettings.contextTokens.trim()) fd.append("llm_context_tokens", llmSettings.contextTokens.trim());
  fd.append("llm_embedding_model", llmSettings.embeddingModel.trim());
  fd.append("llm_provider", llmSettings.provider);
  fd.append("llm_allow_cloud", String(llmSettings.allowCloud));

  clientLog(`LLM settings saved: url=${baseUrl || "(provider default)"}, model=${model || "(none)"}`);
  try {
    const res = await fetch("/api/llm/settings", { method: "POST", body: fd });
    const body = await res.json().catch(() => ({}));
    if (!res.ok) {
      llmSettings.urlNote = body.detail || "Could not save these settings.";
      return;
    }
    if (body.llm_base_url) llmSettings.baseUrl = body.llm_base_url;
    llmSettings.urlNote = body.note ? `Adjusted: ${body.note}.` : "";
  } catch {
    /* offline; the value stays in the form */
  }
}

export async function loadLlmSettingsFromServer(): Promise<boolean> {
  try {
    const res = await fetch("/api/llm/settings");
    if (!res.ok) return false;
    const cfg = await res.json();
    if (cfg.llm_base_url) llmSettings.baseUrl = cfg.llm_base_url;
    if (cfg.llm_model_name) llmSettings.modelName = cfg.llm_model_name;
    if (cfg.llm_api_key_set) {
      llmSettings.apiKey = "••••••••";
      llmSettings.apiKeyMasked = true;
    }
    if (cfg.llm_context_tokens) llmSettings.contextTokens = String(cfg.llm_context_tokens);
    if (cfg.llm_embedding_model) llmSettings.embeddingModel = cfg.llm_embedding_model;

    await loadProviderCatalog();
    applyProvider(cfg.llm_provider);
    llmSettings.allowCloud = !!cfg.llm_allow_cloud;
    return !!cfg.llm_model_name;
  } catch {
    /* server not available */
  }
  return false;
}

export async function refreshModelsBrowser(): Promise<void> {
  try {
    await saveLlmSettings();
    const res = await fetch("/api/llm/models");
    modelsBrowser.installed = res.ok ? await res.json() : [];
  } catch {
    modelsBrowser.installed = [];
  }
}

export async function openModelsBrowser(): Promise<void> {
  modelsBrowser.open = true;
  await refreshModelsBrowser();
}

export function selectLlmModel(modelId: string): void {
  llmSettings.modelName = modelId;
  modelsBrowser.open = false;
  clientLog(`LLM model selected: ${modelId}`);
  saveLlmSettings();
}

export async function pullLlmModel(modelId: string): Promise<void> {
  modelsBrowser.pulling[modelId] = "pulling";
  clientLog(`Pulling LLM model: ${modelId}`);
  try {
    const res = await fetch("/api/llm/models/pull", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ model_name: modelId }),
    });
    if (!res.ok) throw new Error(await res.text());
    clientLog(`Model pull complete: ${modelId}`);
    modelsBrowser.pulling[modelId] = "ok";
    selectLlmModel(modelId);
    await refreshModelsBrowser();
  } catch (err) {
    clientLog(`Model pull failed: ${(err as Error).message}`, "ERROR");
    modelsBrowser.pulling[modelId] = "error";
    setTimeout(() => {
      delete modelsBrowser.pulling[modelId];
    }, 3000);
  }
}

export async function autoTestLlmConnection(): Promise<void> {
  try {
    const res = await fetch("/api/llm/test-connection", { method: "POST" });
    const data = await res.json();
    if (data.ok) {
      llmSettings.testTone = "ok";
      llmSettings.testStatus = "● LLM connected";
    } else {
      llmSettings.testTone = "info";
      llmSettings.testStatus = "● " + (data.error || "LLM not reachable");
    }
  } catch {
    llmSettings.testTone = "info";
    llmSettings.testStatus = "● LLM status unknown";
  }
}

export async function testLlmConnection(): Promise<void> {
  llmSettings.testStatus = "";
  try {
    await saveLlmSettings();
    const res = await fetch("/api/llm/test-connection", { method: "POST" });
    const data = await res.json();
    if (data.ok) {
      llmSettings.testTone = "ok";
      llmSettings.testStatus = data.model_info || "Connected ✓";
    } else {
      llmSettings.testTone = "error";
      llmSettings.testStatus = data.error || "Connection failed";
    }
  } catch (err) {
    llmSettings.testTone = "error";
    llmSettings.testStatus = (err as Error).message;
  }
}

// --- benchmark ---------------------------------------------------------------

export interface BenchmarkResult {
  model: string;
  error?: string;
  load_time_s?: number;
  transcribe_time_s?: number;
  elapsed_s?: number;
  rtf?: number;
}

export interface BenchmarkData {
  date: string;
  system: {
    cpu?: string;
    cpu_cores?: number;
    ram_gb?: number;
    cuda?: boolean;
    gpu?: string;
    os?: string;
    arch?: string;
  };
  results: BenchmarkResult[];
  total_elapsed_s?: number;
  reference_audio?: string;
}

export const benchmarkState = $state({
  running: false,
  status: "",
  data: null as BenchmarkData | null,
});

export async function runBenchmark(): Promise<void> {
  benchmarkState.running = true;
  benchmarkState.status = "Downloading reference audio and running tiny → small → medium…";
  benchmarkState.data = null;
  clientLog("Benchmark started");
  try {
    const resp = await fetch("/api/benchmark/run", {
      method: "POST",
      signal: AbortSignal.timeout ? AbortSignal.timeout(300000) : undefined,
    });
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({ detail: resp.statusText }));
      throw new Error(err.detail || resp.statusText);
    }
    benchmarkState.data = await resp.json();
    clientLog("Benchmark complete");
  } catch (e) {
    benchmarkState.status = "Error: " + (e as Error).message;
    clientLog("Benchmark error: " + (e as Error).message, "ERROR");
  } finally {
    benchmarkState.running = false;
  }
}

export function shareBenchmarkUrl(): string | null {
  const d = benchmarkState.data;
  if (!d) return null;
  const s = d.system || {};
  const gpuLine = s.cuda ? s.gpu || "GPU (CUDA)" : "CPU only";
  const machineLabel = `${s.cpu || "unknown"} / ${gpuLine}`;

  const tableRows = (d.results || [])
    .map((r) => (r.error ? `| ${r.model} | — | — | — | error |` : `| ${r.model} | ${r.load_time_s}s | ${r.transcribe_time_s}s | ${r.elapsed_s}s | ${r.rtf}x |`))
    .join("\n");

  const body = [
    `## Benchmark Results`,
    ``,
    `**Date:** ${d.date}`,
    `**OS:** ${s.os || "—"} (${s.arch || "—"})`,
    `**CPU:** ${s.cpu || "—"}`,
    `**Cores:** ${s.cpu_cores || "—"}`,
    `**RAM:** ${s.ram_gb != null ? s.ram_gb + " GB" : "—"}`,
    `**GPU:** ${gpuLine}`,
    `**Reference audio:** ${d.reference_audio || "jfk.flac"}`,
    ``,
    `| Model | Load | Inference | Elapsed | RTF |`,
    `|-------|------|-----------|---------|-----|`,
    tableRows,
    ``,
    d.total_elapsed_s != null ? `**Total benchmark time:** ${d.total_elapsed_s}s` : "",
    `> RTF < 1.0 = faster than real-time`,
    ``,
    `<!-- benchmark-data`,
    JSON.stringify(d),
    `-->`,
  ].join("\n");

  const title = encodeURIComponent(`[Benchmark] ${machineLabel} — ${d.date}`);
  const bodyEnc = encodeURIComponent(body);
  return `https://github.com/sim186/AmicoScript/issues/new?labels=benchmark-result&title=${title}&body=${bodyEnc}`;
}

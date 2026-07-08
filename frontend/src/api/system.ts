export interface SystemInfo {
  backend: "cpu" | "gpu";
  gpu_available: boolean;
  provider: string | null;      // active ONNX Runtime execution provider
  device_name: string | null;
  vram_total_mb: number | null;
}

export async function getSystem(): Promise<SystemInfo> {
  const r = await fetch("/api/system");
  if (!r.ok) throw new Error(`system fetch failed: ${r.status}`);
  return r.json();
}

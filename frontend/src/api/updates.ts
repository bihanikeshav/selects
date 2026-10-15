export interface UpdateStatus {
  current_version: string;
  platform: string;
  installation: "desktop" | "python";
  status: "unchecked" | "available" | "current" | "unavailable";
  latest_version: string | null;
  release_url: string;
  download_url: string | null;
  checked_at: string | null;
  message: string | null;
}

export async function getUpdates(check = false): Promise<UpdateStatus> {
  const response = await fetch(check ? "/api/updates/check" : "/api/updates", {
    method: check ? "POST" : "GET",
    signal: AbortSignal.timeout(15000),
  });
  if (!response.ok) throw new Error("The update service is unavailable. Try again.");
  return response.json();
}

export type RecordRow = {
  id: string;
  created: number;
  video_id: string;
  data: Record<string, any>;
};
export type Employee = {
  role: string;
  status: string;
  job_id: string | null;
  stage: string | null;
  updated: number;
};
export type Office = {
  id: string;
  name: string;
  mode: string;
  settings: Record<string, any>;
};
export type Snapshot = {
  office: Office;
  employees: Employee[];
  jobs: any[];
  production_queue: any[];
  videos: RecordRow[];
  topics: RecordRow[];
  reports: RecordRow[];
  system_events: RecordRow[];
  cost_events: RecordRow[];
  usage_events: RecordRow[];
  analytics_snapshots: RecordRow[];
  daily_production_plans: RecordRow[];
  performance_profiles: RecordRow[];
  errors: RecordRow[];
  youtube: { channel_id: string; channel_title: string } | null;
};
export const roles = [
  "EDITOR",
  "SCOUT",
  "RESEARCHER",
  "WRITER",
  "DIRECTOR",
  "ARTIST",
  "VOICE",
  "CUTTER",
  "RIGHTS / QC",
  "ANALYST / UPLOADER",
];
export function config() {
  const deployedApi = process.env.NEXT_PUBLIC_API_URL || "";
  const saved = localStorage.getItem("psf-api") || "";
  const savedIsLocal = /^http:\/\/(localhost|127\.0\.0\.1)(:|\/|$)/.test(
    saved,
  );
  const useDeployedApi =
    location.protocol === "https:" &&
    deployedApi.startsWith("https://") &&
    (!saved || savedIsLocal);
  return {
    url: useDeployedApi
      ? deployedApi
      : saved || deployedApi || "http://localhost:8000",
    token: sessionStorage.getItem("psf-token") || "",
  };
}
export async function api(path: string, method = "GET", data?: unknown) {
  const c = config();
  const r = await fetch(c.url + path, {
    method,
    headers: {
      Authorization: `Bearer ${c.token}`,
      ...(data ? { "Content-Type": "application/json" } : {}),
    },
    body: data ? JSON.stringify(data) : undefined,
  });
  if (!r.ok) {
    let message = "Server returned " + r.status;
    try {
      const e = await r.json();
      message =
        typeof e.detail === "string" ? e.detail : JSON.stringify(e.detail);
    } catch {}
    throw new Error(message);
  }
  return r.json();
}
export async function fileUrl(office: string, id: string) {
  const c = config();
  const r = await fetch(`${c.url}/api/offices/${office}/videos/${id}/file`, {
    headers: { Authorization: `Bearer ${c.token}` },
  });
  if (!r.ok) throw new Error("Video download unavailable");
  return URL.createObjectURL(await r.blob());
}
export async function previewUrl(office: string, id: string) {
  const c = config();
  const r = await fetch(`${c.url}/api/offices/${office}/videos/${id}/preview`, {
    headers: { Authorization: `Bearer ${c.token}` },
  });
  if (!r.ok) throw new Error("Video preview unavailable");
  return URL.createObjectURL(await r.blob());
}

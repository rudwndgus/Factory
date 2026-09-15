"use client";
import { useEffect, useState, useRef } from "react";
import SceneReview from "../components/SceneReview";
import ReadableReport from "../components/ReadableReport";
import dynamic from "next/dynamic";
import {
  Play,
  Pause,
  Square,
  Activity,
  FlaskConical,
  Settings,
  ArrowUpRight,
  Plus,
  Building2,
  FileText,
  Film,
  Layers,
  Radio,
  ShieldCheck,
  Plug,
  Zap,
  ChevronDown,
  ChevronRight,
  X,
  LogOut,
  RefreshCw,
  Download,
  Check,
  Search,
  AlertTriangle,
  BookOpen,
  Coins,
  Globe,
  Power,
  Menu,
} from "lucide-react";
import {
  api,
  config,
  fileUrl,
  roles,
  type Snapshot,
  type Office,
  type RecordRow,
} from "../lib/api";
const OfficeScene = dynamic(() => import("../components/OfficeScene"), {
  ssr: false,
  loading: () => <div className="office-loading">Opening the office…</div>,
});
const tabs = [
  "Office",
  "Production",
  "Topics",
  "Video library",
  "Reports",
  "Analytics",
  "Settings",
];
const icons = [Building2, Layers, Search, Film, FileText, Activity, Settings];
const titles = [
  "The office is yours.",
  "From idea to final cut.",
  "A little curiosity goes a long way.",
  "Made by your tiny team.",
  "A note from your team.",
  "Learn from every upload.",
  "Make this factory your own.",
];
const descriptions = [
  "One small team. A whole world of stories to tell.",
  "A persistent production line. Every step, accounted for.",
  "Discover, research, and turn surprising ideas into stories.",
  "Review your scripts, source records, and finished Shorts.",
  "Real production reports, delivered straight to your desk.",
  "Your channel’s own performance. No invented numbers.",
  "Connect providers and set the rules of your office.",
];
const roleLabels = [
  "Editor",
  "Scout",
  "Researcher",
  "Writer",
  "Director",
  "Artist",
  "Voice",
  "Cutter",
  "Rights / QC",
  "Analyst",
];
const roleDescriptions = [
  "Coordinates the production line",
  "Finds fresh stories from official feeds",
  "Collects source evidence and claims",
  "Writes original, short narration",
  "Plans each scene and its motion",
  "Creates rights-cleared visuals",
  "Synthesizes speech and subtitle timing",
  "Assembles and renders the final MP4",
  "Checks technical quality and publication gates",
  "Connects YouTube and collects performance",
];
const emptyRows: RecordRow[] = [];
function date(ts: number) {
  return new Date(ts * 1000).toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
  });
}
function dollars(n: number) {
  return "$" + n.toFixed(2);
}

export default function Page() {
  const [tab, setTab] = useState("Office"),
    [offices, setOffices] = useState<Office[]>([]),
    [id, setId] = useState(""),
    [snap, setSnap] = useState<Snapshot | null>(null),
    [connected, setConnected] = useState(false),
    [modal, setModal] = useState<string | null>(null),
    [selected, setSelected] = useState(""),
    [error, setError] = useState(""),
    [notice, setNotice] = useState(""),
    [busy, setBusy] = useState(false),
    [integrations, setIntegrations] = useState<any[]>([]),
    [detail, setDetail] = useState<any>(null),
    [playback, setPlayback] = useState(""),
    [hq, setHq] = useState(false),
    [reportShelf, setReportShelf] = useState<"inbox" | "archive">("inbox");
  const [server, setServer] = useState("http://localhost:8000"),
    [password, setPassword] = useState("");
  const controller = useRef<AbortController | null>(null);
  const root = `/api/offices/${id}`;
  const office = snap?.office;
  const mode = connected ? office?.mode || "STOPPED" : "OFFLINE";
  const videos = snap?.videos || emptyRows,
    jobs = snap?.jobs || [],
    reports = snap?.reports || emptyRows;
  const productionReports = reports.filter((r) => r.data.kind === "production");
  const inboxReports = productionReports.filter((r) => !r.data.archived);
  const archivedReports = productionReports.filter((r) => r.data.archived);
  const visibleReports =
    reportShelf === "inbox" ? inboxReports : archivedReports;
  const teamWorking =
    mode === "RUNNING" &&
    jobs.some((j) => ["QUEUED", "RUNNING", "RETRYING"].includes(j.status));
  const today = new Date().toISOString().slice(0, 10);
  const costs = snap?.cost_events || [];
  const dailyCost = costs
    .filter((c) => c.data.day === today)
    .reduce((s, c) => s + c.data.estimated_usd, 0);
  const produced = videos.filter(
    (v) =>
      v.data.file && new Date(v.created * 1000).toISOString().startsWith(today),
  ).length;
  async function refresh() {
    if (id) {
      const s = await api(root + "/snapshot");
      setSnap(s);
      setConnected(true);
    }
  }
  async function act(
    fn: () => Promise<any>,
    message = "Saved",
    refreshAfter = true,
  ) {
    setBusy(true);
    setError("");
    try {
      await fn();
      if (id && refreshAfter) await refresh();
      setNotice(message);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function boot() {
    try {
      const list = await api("/api/offices");
      setOffices(list);
      setId((old) =>
        list.some((o: Office) => o.id === old) ? old : list[0]?.id || "",
      );
      setConnected(true);
    } catch {
      setConnected(false);
    }
  }
  useEffect(() => {
    setServer(config().url);
    if (config().token) void boot();
    if ("serviceWorker" in navigator)
      navigator.serviceWorker
        .register((process.env.NEXT_PUBLIC_BASE_PATH || "") + "/sw.js")
        .catch(() => {});
  }, []);
  useEffect(() => {
    if (connected && tab === "Settings") {
      api("/api/integrations")
        .then(setIntegrations)
        .catch(() => {});
    }
  }, [connected, tab]);
  useEffect(() => {
    if (!id) return;
    setSnap(null);
    controller.current?.abort();
    const ac = new AbortController();
    controller.current = ac;
    void (async () => {
      while (!ac.signal.aborted) {
        try {
          const c = config();
          const response = await fetch(`${c.url}/api/offices/${id}/events`, {
            headers: { Authorization: `Bearer ${c.token}` },
            signal: ac.signal,
          });
          if (!response.ok) throw new Error("Live connection unavailable");
          setConnected(true);
          const reader = response.body!.getReader();
          const decoder = new TextDecoder();
          let buffer = "";
          while (!ac.signal.aborted) {
            const { done, value } = await reader.read();
            if (done) break;
            buffer += decoder.decode(value, { stream: true });
            const parts = buffer.split("\n\n");
            buffer = parts.pop() || "";
            for (const part of parts)
              if (part.startsWith("data: ")) setSnap(JSON.parse(part.slice(6)));
          }
        } catch {
          if (!ac.signal.aborted) setConnected(false);
        }
        if (!ac.signal.aborted) await new Promise((r) => setTimeout(r, 8000));
      }
    })();
    return () => ac.abort();
  }, [id]);
  useEffect(() => {
    if (!notice) return;
    const t = setTimeout(() => setNotice(""), 4000);
    return () => clearTimeout(t);
  }, [notice]);
  useEffect(
    () => () => {
      if (playback) URL.revokeObjectURL(playback);
    },
    [playback],
  );
  function need() {
    if (!connected) {
      setModal("connect");
      return false;
    }
    return true;
  }
  const control = (value: string) => {
    if (!need()) return;
    if (value === "EMERGENCY_STOP") {
      setModal("emergency");
      return;
    }
    void act(
      () => api(root + "/mode", "POST", { mode: value }),
      "Office " + value.toLowerCase(),
    );
  };
  async function openVideo(v: RecordRow) {
    setSelected(v.id);
    setDetail(null);
    setPlayback("");
    setModal("video");
    try {
      setDetail(await api(root + "/videos/" + v.id));
      if (v.data.file) setPlayback(await fileUrl(id, v.id));
    } catch (e) {
      setError((e as Error).message);
    }
  }
  function openEmployee(role: string) {
    if (role === "CEO") {
      setTab("Reports");
      return;
    }
    setSelected(role);
    setModal("employee");
  }
  const liveEvents = (snap?.system_events || []).slice(0, 5);
  return (
    <div className="shell">
      <aside className="sidebar">
        <a
          className="brand"
          href="#"
          onClick={(e) => {
            e.preventDefault();
            setTab("Office");
            setHq(false);
          }}
        >
          <span className="brand-pixel">
            <i />
            <i />
            <i />
            <i />
          </span>
          <span>
            pixel shorts<span>FACTORY</span>
          </span>
        </a>
        <div className="workspace-label">YOUR WORKSPACE</div>
        <button
          className={"hq-link " + (hq ? "active" : "")}
          onClick={() => setHq(!hq)}
        >
          <Globe size={17} /> Headquarters <ArrowUpRight size={14} />
        </button>
        <div className="office-selector">
          <div className="office-icon">
            A<span>01</span>
          </div>
          <div>
            <small>CURRENT OFFICE</small>
            <select
              aria-label="Select office"
              value={id}
              onChange={(e) => {
                setId(e.target.value);
                setHq(false);
              }}
            >
              <option value="">Amazing Things</option>
              {offices.map((o) => (
                <option key={o.id} value={o.id}>
                  {o.name}
                </option>
              ))}
            </select>
          </div>
        </div>
        <nav>
          {tabs.map((t, i) => {
            const Icon = icons[i];
            return (
              <button
                key={t}
                className={!hq && tab === t ? "active" : ""}
                onClick={() => {
                  setTab(t);
                  setHq(false);
                  if (t === "Settings" && connected)
                    api("/api/integrations")
                      .then(setIntegrations)
                      .catch(() => {});
                }}
              >
                <Icon size={18} />
                {t}
                {t === "Reports" && inboxReports.length > 0 ? (
                  <b>{inboxReports.length}</b>
                ) : t === "Office" ? (
                  <span className="nav-dot" />
                ) : null}
              </button>
            );
          })}
        </nav>
        <button
          className="new-office"
          onClick={() => {
            if (need()) setModal("new");
          }}
        >
          <Plus size={15} /> Create an office
        </button>
        <div className="sidebar-bottom">
          <div className="budget-card">
            <div>
              <Coins size={14} />
              <span>DAILY BUDGET</span>
              <span>{connected ? dollars(dailyCost) : "—"}</span>
            </div>
            <div className="budget-track">
              <i
                style={{
                  width: `${Math.min(100, (dailyCost / (office?.settings.daily_budget || 3)) * 100)}%`,
                }}
              />
            </div>
            <p>
              {connected
                ? `${dollars(office?.settings.daily_budget || 0)} limit · estimated reservations`
                : "Connect your server to track costs"}
            </p>
          </div>
          <button
            className="connection-button"
            onClick={() => setModal("connect")}
          >
            <span className={"connection-dot " + (connected ? "on" : "")} />
            <span>
              {connected ? "Server connected" : "Connect your server"}
              <small>
                {connected
                  ? "Private workspace"
                  : "Production runs on your backend"}
              </small>
            </span>
            <Plug size={16} />
          </button>
          <div className="owner">
            <span>K</span>
            <div>
              Owner workspace<small>CEO · Single owner</small>
            </div>
            <button
              aria-label="Sign out"
              onClick={() =>
                void act(async () => {
                  await api("/api/auth/logout", "POST");
                  sessionStorage.removeItem("psf-token");
                  controller.current?.abort();
                  setConnected(false);
                  setSnap(null);
                  setId("");
                }, "Signed out")
              }
            >
              <LogOut size={15} />
            </button>
          </div>
        </div>
      </aside>
      <main>
        <header className="topbar">
          <div className="breadcrumb">
            <Building2 size={15} /> Workspace <ChevronRight size={13} />
            <span>
              {hq ? "Headquarters" : office?.name || "Amazing Things"}
            </span>
          </div>
          <div className="top-right">
            <span className="time-label">YOUR TINY PRODUCTION TEAM</span>
            <span className={"mode " + mode.toLowerCase()}>
              <i />
              {mode.replace("_", " ")}
            </span>
            <button
              aria-label="Connection settings"
              onClick={() => setModal("connect")}
            >
              <Settings size={17} />
            </button>
          </div>
        </header>
        <div className="content">
          <div className="page-heading">
            <div>
              <div className="eyebrow">
                <span />{" "}
                {hq
                  ? "GLOBAL HQ"
                  : `OFFICE ${Math.max(
                      1,
                      offices.findIndex((o) => o.id === id) + 1,
                    )
                      .toString()
                      .padStart(2, "0")} / ${tab.toUpperCase()}`}
              </div>
              <h1>
                {hq ? "A home for every idea." : titles[tabs.indexOf(tab)]}
              </h1>
              <p>
                {hq
                  ? "Your independent channel offices, in one place."
                  : descriptions[tabs.indexOf(tab)]}
              </p>
            </div>
            <button
              className="btn primary"
              disabled={busy || (connected && mode !== "RUNNING")}
              title={connected && mode !== "RUNNING" ? "먼저 Office에서 Start를 눌러주세요." : undefined}
              onClick={() => {
                if (need())
                  void act(() => {
                    if (
                      !window.confirm(
                        "AI 이미지 5장을 생성하는 테스트입니다. API 비용이 발생하며 업로드는 하지 않습니다. 진행할까요?",
                      )
                    )
                      return Promise.resolve();
                    return api(root + "/jobs", "POST", {
                      test_mode: true,
                      ai_visuals: true,
                    });
                  }, "TEST RUN queued. No YouTube publication.");
              }}
            >
              <FlaskConical size={16} /> AI Test run <ArrowUpRight size={14} />
            </button>
          </div>
          {error && (
            <div role="alert" className="alert">
              <AlertTriangle size={16} />
              {error}
              <button onClick={() => setError("")} aria-label="Dismiss error">
                <X size={15} />
              </button>
            </div>
          )}
          {hq ? (
            <>
              <div className="stats-grid">
                <Stat
                  label="TOTAL OFFICES"
                  value={String(offices.length)}
                  note="Isolated workspaces"
                />
                <Stat
                  label="ACTIVE OFFICES"
                  value={String(
                    offices.filter((o) => o.mode === "RUNNING").length,
                  )}
                  note="Scheduled production"
                />
                <Stat
                  label="SYSTEM"
                  value={connected ? "Online" : "Offline"}
                  note="Real backend connection"
                />
              </div>
              <div className="toolbar">
                {["RUNNING", "PAUSED", "EMERGENCY_STOP"].map((m) => (
                  <button
                    key={m}
                    className="btn"
                    onClick={() => {
                      if (!need()) return;
                      if (m === "EMERGENCY_STOP") setModal("emergency-all");
                      else
                        void act(
                          () => api("/api/system/mode", "POST", { mode: m }),
                          m,
                        );
                    }}
                  >
                    {m === "RUNNING"
                      ? "Start all"
                      : m === "PAUSED"
                        ? "Pause all"
                        : "Emergency stop all"}
                  </button>
                ))}
              </div>
              <div className="cards">
                {offices.map((o) => (
                  <button
                    className="hq-card"
                    key={o.id}
                    onClick={() => {
                      setId(o.id);
                      setHq(false);
                      setTab("Office");
                    }}
                  >
                    <Building2 />
                    <h3>{o.name}</h3>
                    <span className="pill">{o.mode}</span>
                  </button>
                ))}
              </div>
            </>
          ) : tab === "Office" ? (
            <>
              <div className="stats-grid">
                <Stat
                  label="SHORTS PRODUCED TODAY"
                  value={connected ? `${produced}` : "—"}
                  suffix={
                    connected ? `/ ${office?.settings.videos_per_day || 3}` : ""
                  }
                  note={
                    connected
                      ? "Includes clearly labeled test outputs"
                      : "Waiting for your production server"
                  }
                  icon={<Film size={17} />}
                />
                <Stat
                  label="TEAM STATUS"
                  value={
                    connected
                      ? `${snap?.employees.filter((e) => e.status === "WORKING").length || 0} working`
                      : "Not connected"
                  }
                  note="10 specialists. One shared mission."
                  icon={<Layers size={17} />}
                />
                <Stat
                  label="ESTIMATED COST TODAY"
                  value={connected ? dollars(dailyCost) : "—"}
                  note={
                    connected
                      ? `${dollars(office?.settings.daily_budget || 3)} daily limit`
                      : "No provider calls made by this page"
                  }
                  icon={<Coins size={17} />}
                />
                <Stat
                  label="READY FOR REVIEW"
                  value={
                    connected
                      ? String(
                          videos.filter(
                            (v) => v.data.status === "WAITING_FOR_REVIEW",
                          ).length,
                        )
                      : "—"
                  }
                  note="Your final say before publishing"
                  icon={<ShieldCheck size={17} />}
                />
              </div>
              <section className="office-panel">
                <div className="panel-heading">
                  <div>
                    <span className="mini-icon">
                      <Building2 size={17} />
                    </span>
                    <div>
                      <h2>{office?.name || "Amazing Things"}</h2>
                      <p>Curiosity, made into stories.</p>
                    </div>
                    <span className="pill">OFFICE 01</span>
                  </div>
                  <div className="legend">
                    <i />{" "}
                    {connected
                      ? "Live employee states"
                      : "Server not connected"}
                    <span />
                    <small>Scroll to zoom · drag to pan</small>
                  </div>
                </div>
                <div className="map-wrap">
                  <OfficeScene
                    employees={snap?.employees || []}
                    reports={inboxReports.length}
                    connected={connected}
                    mode={office?.mode}
                    teamWorking={teamWorking}
                    onSelect={openEmployee}
                    blocked={!!modal}
                  />
                  <div className="map-caption">
                    <span>
                      <span
                        className={"connection-dot " + (connected ? "on" : "")}
                      />
                      {connected
                        ? "Connected to your real production queue"
                        : "Connect a server to bring your office to life"}
                    </span>
                    <span>ORIGINAL PIXEL OFFICE</span>
                  </div>
                </div>
                <div className="factory-controls">
                  <div className="controls-label">
                    <Power size={14} /> FACTORY CONTROLS
                  </div>
                  <button
                    className="btn start"
                    disabled={busy}
                    onClick={() => control("RUNNING")}
                  >
                    <Play size={14} /> Start
                  </button>
                  <button className="btn" onClick={() => control("PAUSED")}>
                    <Pause size={14} /> Pause
                  </button>
                  <button className="btn" onClick={() => control("STOPPED")}>
                    <Square size={12} /> Stop
                  </button>
                  <button
                    className="btn"
                    disabled={busy}
                    onClick={() => {
                      if (need())
                        void act(
                          () => api(root + "/inspect", "POST"),
                          "Inspection delivered to the CEO desk",
                        );
                    }}
                  >
                    <Activity size={14} /> Inspect
                  </button>
                  <button
                    className="btn maintenance"
                    onClick={() => control("MAINTENANCE")}
                  >
                    <Settings size={14} /> Maintenance
                  </button>
                  <button
                    className="emergency"
                    onClick={() => control("EMERGENCY_STOP")}
                  >
                    Emergency stop
                  </button>
                </div>
              </section>
              <div className="lower-grid">
                <section className="panel">
                  <div className="section-head">
                    <h2>
                      <Radio size={16} /> Office activity
                    </h2>
                    <span className="live-label">
                      {connected ? "LIVE FEED" : "OFFLINE"}
                    </span>
                  </div>
                  {liveEvents.length ? (
                    liveEvents.map((e) => (
                      <div className="event" key={e.id}>
                        <span className={"event-dot " + e.data.severity} />
                        <p>{e.data.message}</p>
                        <time>{date(e.created)}</time>
                      </div>
                    ))
                  ) : (
                    <div className="empty-activity">
                      <span className="sleepy">
                        z<span>z</span>
                      </span>
                      <div>
                        <h3>A quiet start.</h3>
                        <p>
                          {connected
                            ? "Your team is ready. Start a test run to see real activity."
                            : "Once connected, every task and milestone appears here."}
                        </p>
                      </div>
                    </div>
                  )}
                </section>
                <section className="panel desk-panel">
                  <div className="section-head">
                    <h2>
                      <FileText size={16} /> On your desk
                    </h2>
                    <button onClick={() => setTab("Reports")}>
                      View all <ArrowUpRight size={13} />
                    </button>
                  </div>
                  <div className="paper-preview">
                    <div className="paper-symbol">▤</div>
                    <div>
                      <span className="eyebrow">CEO INBOX</span>
                      <h3>
                        {inboxReports.length
                          ? `${inboxReports.length} reports waiting for confirmation`
                          : "Nothing to review. Yet."}
                      </h3>
                      <p>
                        Daily reports, inspections, and important decisions.
                        Delivered here.
                      </p>
                    </div>
                  </div>
                </section>
              </div>
              <section className="team-section">
                <div className="section-head">
                  <h2>Meet your little team</h2>
                  <span>Click a teammate to see their work</span>
                </div>
                <div className="team-grid">
                  {roles.map((r, i) => (
                    <button key={r} onClick={() => openEmployee(r)}>
                      <span className={"person person-" + i}>
                        <i />
                      </span>
                      <b>{roleLabels[i]}</b>
                      <small>
                        {connected
                          ? snap?.employees.find((e) => e.role === r)?.status ||
                            "IDLE"
                          : "OFFLINE"}
                      </small>
                    </button>
                  ))}
                </div>
              </section>
            </>
          ) : tab === "Production" ? (
            <section className="panel list-panel">
              <div className="section-head">
                <h2>Production queue</h2>
                <span>{jobs.length} jobs</span>
              </div>
              {jobs.length ? (
                jobs.map((j) => (
                  <div className="job-row" key={j.id}>
                    <span className="job-icon">
                      <Layers size={18} />
                    </span>
                    <div>
                      <h3>
                        {videos.find((v) => v.id === j.id)?.data.title ||
                          j.id.slice(0, 8)}
                      </h3>
                      <p>
                        Stage {Math.min(j.stage + 1, 9)} / 9 · Attempts{" "}
                        {j.attempts} {j.error ? "· " + j.error : ""}
                      </p>
                    </div>
                    <span className="pill">{j.status}</span>
                    {["FAILED", "BLOCKED", "CANCELLED", "WAITING"].includes(
                      j.status,
                    ) ? (
                      <button
                        className="btn"
                        onClick={() =>
                          void act(
                            () => api(root + `/jobs/${j.id}/retry`, "POST"),
                            "Retry queued",
                          )
                        }
                      >
                        Retry stage
                      </button>
                    ) : j.status !== "COMPLETE" ? (
                      <button
                        className="btn"
                        onClick={() =>
                          void act(
                            () => api(root + `/jobs/${j.id}/cancel`, "POST"),
                            "Job cancelled",
                          )
                        }
                      >
                        Cancel
                      </button>
                    ) : null}
                  </div>
                ))
              ) : (
                <Empty
                  title="The production line is clear."
                  text="Test run creates a real MP4 without publishing. Live production uses your configured providers."
                />
              )}
            </section>
          ) : tab === "Topics" ? (
            <section className="panel list-panel">
              <div className="section-head">
                <h2>Discovery board</h2>
                <div className="toolbar">
                  <button
                    className="btn"
                    disabled={busy}
                    onClick={() => {
                      if (need())
                        void act(
                          () => api(root + "/discover", "POST"),
                          "Discovery complete",
                        );
                    }}
                  >
                    <Search size={14} /> Discover sources
                  </button>
                  <button
                    className="btn primary"
                    onClick={() => {
                      if (need()) setModal("topic");
                    }}
                  >
                    <Plus size={14} /> Add topic
                  </button>
                </div>
              </div>
              {snap?.topics.length ? (
                snap.topics.map((t) => (
                  <div className="topic-row" key={t.id}>
                    <div>
                      <span className="eyebrow">
                        {t.data.provider || "OWNER SOURCE"} · {t.data.status}
                      </span>
                      <h3>{t.data.title}</h3>
                      <p>{t.data.summary?.slice(0, 200)}</p>
                      {/^https?:\/\//.test(t.data.source_url || "") && (
                        <a
                          target="_blank"
                          rel="noreferrer"
                          href={t.data.source_url}
                        >
                          Read source <ArrowUpRight size={12} />
                        </a>
                      )}
                    </div>
                    <div className="toolbar">
                      <button
                        className="btn"
                        onClick={() =>
                          void act(
                            () => api(root + `/topics/${t.id}/pin`, "POST"),
                            "Topic pinned",
                          )
                        }
                      >
                        Pin
                      </button>
                      <button
                        className="btn"
                        onClick={() =>
                          void act(
                            () => api(root + `/topics/${t.id}/reject`, "POST"),
                            "Topic rejected",
                          )
                        }
                      >
                        Reject
                      </button>
                      <button
                        className="btn primary"
                        onClick={() =>
                          void act(
                            () =>
                              api(root + "/jobs", "POST", { topic_id: t.id }),
                            "Production queued",
                          )
                        }
                      >
                        Produce
                      </button>
                    </div>
                  </div>
                ))
              ) : (
                <Empty
                  title="Start with a surprising idea."
                  text="Scout can collect fresh RSS entries, or you can add a topic and its source yourself."
                />
              )}
            </section>
          ) : tab === "Video library" ? (
            <section className="panel list-panel">
              <div className="section-head">
                <h2>Video library</h2>
                <span>{videos.length} productions</span>
              </div>
              {videos.length ? (
                <div className="video-cards">
                  {videos.map((v) => (
                    <button
                      key={v.id}
                      className="video-card"
                      onClick={() => void openVideo(v)}
                    >
                      <div className="video-cover">
                        <Film size={38} />
                        <span>
                          {v.data.test_mode ? "TEST RUN" : "PRODUCTION"}
                        </span>
                      </div>
                      <h3>{v.data.title}</h3>
                      <span className="pill">{v.data.status}</span>
                      <p>
                        {v.data.actual_duration
                          ? `${v.data.actual_duration.toFixed(1)} sec · `
                          : ""}
                        {date(v.created)}
                      </p>
                    </button>
                  ))}
                </div>
              ) : (
                <Empty
                  title="Your first Short starts here."
                  text="Completed videos include narration, subtitles, original graphics, and a real downloadable MP4."
                />
              )}
            </section>
          ) : tab === "Reports" ? (
            <section className="panel list-panel">
              <div className="section-head">
                <h2>영상 제작 보고서</h2>
                <div className="toolbar">
                  <button
                    className={
                      "btn " + (reportShelf === "inbox" ? "primary" : "")
                    }
                    onClick={() => setReportShelf("inbox")}
                  >
                    확인 대기 {inboxReports.length}
                  </button>
                  <button
                    className={
                      "btn " + (reportShelf === "archive" ? "primary" : "")
                    }
                    onClick={() => setReportShelf("archive")}
                  >
                    정리함 {archivedReports.length}
                  </button>
                </div>
              </div>
              {visibleReports.length ? (
                <div className="report-grid">
                  {visibleReports.map((r) => (
                    <button
                      className="report-paper"
                      key={r.id}
                      onClick={() => {
                        setSelected(r.id);
                        setModal("report");
                      }}
                    >
                      <span className="eyebrow">PIXEL SHORTS FACTORY</span>
                      <FileText size={30} />
                      <h3>
                        {r.data.title || `${r.data.kind.toUpperCase()} REPORT`}
                      </h3>
                      <p>{r.data.topic || r.data.summary}</p>
                      <p>
                        {r.data.upload_date || "업로드 대기"} · {r.data.status}
                      </p>
                      <div />
                      <div />
                      <div />
                      <span>
                        Open report <ArrowUpRight size={14} />
                      </span>
                    </button>
                  ))}
                </div>
              ) : (
                <Empty
                  title={
                    reportShelf === "archive"
                      ? "정리함이 비어 있습니다."
                      : "확인할 영상 보고서가 없습니다."
                  }
                  text="영상 한 편마다 10개 부서의 작업과 특이사항이 한 장에 정리됩니다."
                />
              )}
            </section>
          ) : tab === "Analytics" ? (
            <section className="panel list-panel">
              <div className="section-head">
                <h2>Channel performance</h2>
                <button
                  className="btn"
                  disabled={busy}
                  onClick={() => {
                    if (need())
                      void act(
                        () => api(root + "/analytics/refresh", "POST"),
                        "Analytics refreshed",
                      );
                  }}
                >
                  <RefreshCw size={14} /> Refresh YouTube
                </button>
              </div>
              {snap?.analytics_snapshots.length ? (
                snap.analytics_snapshots.map((a) => (
                  <div className="job-row" key={a.id}>
                    <div>
                      <h3>
                        {videos.find((v) => v.id === a.video_id)?.data.title ||
                          a.video_id}
                      </h3>
                      <p>{date(a.created)}</p>
                    </div>
                    <pre>{JSON.stringify(a.data, null, 2)}</pre>
                  </div>
                ))
              ) : (
                <Empty
                  title="Let real performance lead."
                  text="No analytics yet. Connect YouTube and publish approved videos to begin collecting actual views, watch time, and engagement."
                />
              )}
            </section>
          ) : (
            <div className="settings-grid">
              <section className="panel list-panel">
                <div className="section-head">
                  <h2>Office settings</h2>
                  <span>PRIVATE WORKSPACE</span>
                </div>
                {office ? (
                  <OfficeForm
                    key={office.id}
                    office={office}
                    onSave={(body) =>
                      act(async () => {
                        await api(root, "PUT", body);
                        await boot();
                      }, "Settings saved")
                    }
                  />
                ) : (
                  <Empty
                    title="Connect your production server."
                    text="Office settings are stored on the backend, isolated per channel."
                    action={() => setModal("connect")}
                  />
                )}
              </section>
              <div>
                <section className="panel list-panel">
                  <div className="section-head">
                    <h2>Integrations</h2>
                    <Plug size={16} />
                  </div>
                  {[
                    "OPENAI_API_KEY",
                    "GOOGLE_CLIENT_ID",
                    "GOOGLE_CLIENT_SECRET",
                    "PEXELS_API_KEY",
                  ].map((name) => (
                    <div className="integration" key={name}>
                      <div>
                        <b>{name.replaceAll("_", " ")}</b>
                        <small>
                          {integrations.find((i) => i.name === name)?.configured
                            ? "Configured · masked on server"
                            : "Not configured"}
                        </small>
                      </div>
                      <button
                        className="btn"
                        onClick={() => {
                          if (need()) {
                            setSelected(name);
                            setModal("secret");
                          }
                        }}
                      >
                        Configure
                      </button>
                      {["OPENAI_API_KEY", "PEXELS_API_KEY"].includes(name) &&
                        integrations.find((i) => i.name === name)
                          ?.configured && (
                          <button
                            className="btn"
                            onClick={() =>
                              void act(async () => {
                                const r = await api(
                                  `/api/integrations/${name}/validate`,
                                  "POST",
                                );
                                if (!r.valid)
                                  throw new Error(
                                    "Provider validation failed: " +
                                      r.http_status,
                                  );
                              }, "Provider validated")
                            }
                          >
                            Test
                          </button>
                        )}
                    </div>
                  ))}
                </section>
                <section className="panel list-panel youtube-panel">
                  <div className="section-head">
                    <h2>YouTube channel</h2>
                    <Play size={18} />
                  </div>
                  <h3>
                    {snap?.youtube?.channel_title || "No channel connected"}
                  </h3>
                  <p>
                    {snap?.youtube?.channel_id ||
                      "Secure Google OAuth. Your YouTube password is never requested."}
                  </p>
                  <button
                    className="btn primary"
                    onClick={() => {
                      if (need())
                        void act(async () => {
                          const r = await api(
                            root + "/youtube/connect",
                            "POST",
                          );
                          window.location.assign(r.url);
                        }, "Opening Google");
                    }}
                  >
                    Connect YouTube <ArrowUpRight size={14} />
                  </button>
                  {snap?.youtube && (
                    <button
                      className="btn"
                      onClick={() =>
                        void act(
                          () => api(root + "/youtube", "DELETE"),
                          "Disconnected",
                        )
                      }
                    >
                      Disconnect
                    </button>
                  )}
                </section>
              </div>
            </div>
          )}
          <footer>
            <span>
              <span className="tiny-logo">▦</span> PIXEL SHORTS FACTORY
            </span>
            <p>Small team. Curious minds. Real stories.</p>
            <a
              href="https://github.com/rudwndgus/Factory"
              target="_blank"
              rel="noreferrer"
            >
              Source & setup <ArrowUpRight size={12} />
            </a>
          </footer>
        </div>
      </main>
      {notice && (
        <div className="toast" role="status">
          <Check size={16} />
          {notice}
        </div>
      )}
      {modal && (
        <div
          className="modal-backdrop"
          onMouseDown={(e) => {
            if (e.target === e.currentTarget) setModal(null);
          }}
        >
          <section
            className={"modal " + (modal === "video" ? "video-modal" : "")}
            role="dialog"
            aria-modal="true"
            aria-label={modal}
          >
            <button
              className="modal-close"
              onClick={() => setModal(null)}
              aria-label="Close dialog"
            >
              <X size={19} />
            </button>
            {modal === "connect" ? (
              <>
                <span className="eyebrow">YOUR PRIVATE PRODUCTION SERVER</span>
                <h2>Bring your office to life.</h2>
                <p>
                  The web app connects to your own backend. Enter its address
                  and owner password. Your projects live on that server.
                </p>
                <form
                  onSubmit={(e) => {
                    e.preventDefault();
                    void act(async () => {
                      let url = server.trim().replace(/\/$/, "");
                      const parsed = new URL(url);
                      if (!["http:", "https:"].includes(parsed.protocol))
                        throw new Error("Use an HTTP(S) server address");
                      if (
                        parsed.protocol === "http:" &&
                        !["localhost", "127.0.0.1"].includes(parsed.hostname)
                      )
                        throw new Error("Use HTTPS for a remote server");
                      localStorage.setItem("psf-api", url);
                      const r = await api("/api/auth/login", "POST", {
                        password,
                      });
                      sessionStorage.setItem("psf-token", r.token);
                      await boot();
                      setPassword("");
                      setModal(null);
                    }, "Connected to your office");
                  }}
                >
                  <label>
                    Backend URL
                    <input
                      value={server}
                      onChange={(e) => setServer(e.target.value)}
                      required
                      type="url"
                      placeholder="https://factory-api.example.com"
                    />
                  </label>
                  <label>
                    Owner password
                    <input
                      type="password"
                      autoComplete="current-password"
                      value={password}
                      onChange={(e) => setPassword(e.target.value)}
                      required
                    />
                  </label>
                  <button className="btn primary full" disabled={busy}>
                    {busy ? "Connecting…" : "Open my office"}{" "}
                    <ArrowUpRight size={15} />
                  </button>
                </form>
                <div className="setup-note">
                  First time? Run <code>scripts/setup.ps1</code>, then{" "}
                  <code>scripts/dev.ps1</code>. See README for Docker hosting.
                </div>
              </>
            ) : modal === "employee" ? (
              <>
                <span className="eyebrow">
                  TEAM MEMBER /{" "}
                  {String(roles.indexOf(selected) + 1).padStart(2, "0")}
                </span>
                <h2>{selected}</h2>
                <p>{roleDescriptions[roles.indexOf(selected)]}</p>
                <dl>
                  <dt>Status</dt>
                  <dd>
                    {connected
                      ? snap?.employees.find((e) => e.role === selected)
                          ?.status || "IDLE"
                      : "SERVER NOT CONNECTED"}
                  </dd>
                  <dt>Current stage</dt>
                  <dd>
                    {snap?.employees.find((e) => e.role === selected)?.stage ||
                      "No active task"}
                  </dd>
                  <dt>Current video</dt>
                  <dd>
                    {videos.find(
                      (v) =>
                        v.id ===
                        snap?.employees.find((e) => e.role === selected)
                          ?.job_id,
                    )?.data.title || "—"}
                  </dd>
                </dl>
                <div className="setup-note">
                  Employee activity reflects persisted backend jobs. When there
                  is no job, the employee is idle.
                </div>
              </>
            ) : modal === "new" ? (
              <>
                <span className="eyebrow">A NEW CHANNEL, A NEW TEAM</span>
                <h2>Create an office.</h2>
                <OfficeForm
                  onSave={(body) =>
                    act(async () => {
                      const o = await api("/api/offices", "POST", body);
                      setOffices(await api("/api/offices"));
                      setId(o.id);
                      setSnap(await api(`/api/offices/${o.id}/snapshot`));
                      setModal(null);
                    }, "Office created", false)
                  }
                />
              </>
            ) : modal === "topic" ? (
              <>
                <h2>Start with an idea.</h2>
                <form
                  onSubmit={(e) => {
                    e.preventDefault();
                    const f = new FormData(e.currentTarget);
                    void act(async () => {
                      await api(
                        root + "/topics",
                        "POST",
                        Object.fromEntries(f),
                      );
                      setModal(null);
                    }, "Topic added");
                  }}
                >
                  <label>
                    Topic title
                    <input name="title" required minLength={3} />
                  </label>
                  <label>
                    Source URL
                    <input name="source_url" type="url" required />
                  </label>
                  <label>
                    Research / evidence
                    <textarea name="summary" required rows={5} />
                  </label>
                  <button className="btn primary full">
                    Add to discovery board
                  </button>
                </form>
              </>
            ) : modal === "secret" ? (
              <>
                <span className="eyebrow">ENCRYPTED ON YOUR SERVER</span>
                <h2>{selected.replaceAll("_", " ")}</h2>
                <form
                  onSubmit={(e) => {
                    e.preventDefault();
                    const f = new FormData(e.currentTarget);
                    void act(async () => {
                      await api("/api/integrations", "PUT", {
                        name: selected,
                        value: f.get("value"),
                      });
                      setIntegrations(await api("/api/integrations"));
                      setModal(null);
                    }, "Credential encrypted and saved");
                  }}
                >
                  <label>
                    Credential
                    <input
                      type="password"
                      name="value"
                      autoComplete="off"
                      required
                    />
                  </label>
                  <button className="btn primary full">Save securely</button>
                </form>
              </>
            ) : modal.startsWith("emergency") ? (
              <>
                <span className="eyebrow">EMERGENCY CONTROL</span>
                <h2>
                  Stop{" "}
                  {modal === "emergency-all" ? "every office" : "this office"}{" "}
                  now?
                </h2>
                <p>
                  Block new generation and uploads, cancel queued work, and
                  interrupt rendering where possible. Published videos remain on
                  YouTube.
                </p>
                <button
                  className="btn danger full"
                  onClick={() =>
                    void act(async () => {
                      await api(
                        modal === "emergency-all"
                          ? "/api/system/mode"
                          : root + "/mode",
                        "POST",
                        { mode: "EMERGENCY_STOP", confirmed: true },
                      );
                      setModal(null);
                    }, "Emergency stop active")
                  }
                >
                  Confirm emergency stop
                </button>
              </>
            ) : modal === "report" ? (
              <>
                <span className="eyebrow">DELIVERED TO THE CEO DESK</span>
                <ReadableReport
                  report={reports.find((r) => r.id === selected)?.data}
                />
                {!reports.find((r) => r.id === selected)?.data.archived && (
                  <button
                    className="btn primary full"
                    onClick={() =>
                      void act(async () => {
                        await api(
                          root + `/reports/${selected}/archive`,
                          "POST",
                        );
                        setModal(null);
                        setReportShelf("archive");
                      }, "보고서를 확인하고 정리함으로 옮겼습니다.")
                    }
                  >
                    <Check size={15} /> 확인 완료 · 정리함으로 이동
                  </button>
                )}
              </>
            ) : modal === "video" ? (
              <>
                <span className="eyebrow">REVIEW ROOM</span>
                <h2>{detail?.title || "Loading video…"}</h2>
                {detail && (
                  <>
                    <div className="review-grid">
                      {playback ? (
                        <video controls src={playback} />
                      ) : (
                        <div className="no-video">
                          Rendering has not completed.
                        </div>
                      )}
                      <div>
                        <span className="pill">
                          {detail.test_mode
                            ? "TEST RUN · NEVER PUBLISHED"
                            : detail.status}
                        </span>
                        <h3>Quality control</h3>
                        {Object.entries(detail.qc || {}).map(([k, v]) => (
                          <div className="qc-line" key={k}>
                            <span>{k.replaceAll("_", " ")}</span>
                            <b className={v ? "pass" : "fail"}>
                              {v ? "PASS" : "BLOCK"}
                            </b>
                          </div>
                        ))}
                        <h3>Script</h3>
                        <p>
                          {detail.script?.sentences?.join(" ") ||
                            "Not written yet."}
                        </p>
                        <button
                          className="btn"
                          onClick={() => setModal("edit-script")}
                        >
                          Edit script
                        </button>
                      </div>
                    </div>
                    <SceneReview
                      scenes={detail.scenes || []}
                      root={root + `/videos/${selected}`}
                      regenerate={(scene) => {
                        void act(async () => {
                          await api(
                            root + `/videos/${selected}/regenerate`,
                            "POST",
                            { stage: 4, scene },
                          );
                          setModal(null);
                        }, "장면 재생성 접수 완료");
                      }}
                    />
                    <details>
                      <summary>Sources, rights, and research</summary>
                      <pre>
                        {JSON.stringify(
                          {
                            sources: detail.sources,
                            rights: detail.rights_records,
                            claims: detail.research_claims,
                          },
                          null,
                          2,
                        )}
                      </pre>
                    </details>
                    <div className="toolbar wrap">
                      {playback && (
                        <a
                          className="btn primary"
                          href={playback}
                          download={`${selected}.mp4`}
                        >
                          <Download size={15} /> Download MP4
                        </a>
                      )}
                      {!detail.test_mode &&
                        ["verify_facts", "approve", "reject"].map((action) => (
                          <button
                            key={action}
                            className="btn"
                            onClick={() =>
                              void act(async () => {
                                setDetail(
                                  await api(
                                    root + `/videos/${selected}/review`,
                                    "POST",
                                    { action },
                                  ),
                                );
                              }, "Review updated")
                            }
                          >
                            {action === "verify_facts"
                              ? "I verified the facts"
                              : action === "approve"
                                ? "Approve"
                                : "Reject"}
                          </button>
                        ))}
                      {[
                        ["Rewrite script", 2],
                        ["Regenerate images", 4],
                        ["Regenerate voice", 5],
                        ["Render again", 7],
                      ].map(([label, stage]) => (
                        <button
                          key={label}
                          className="btn"
                          onClick={() =>
                            void act(async () => {
                              await api(
                                root + `/videos/${selected}/regenerate`,
                                "POST",
                                { stage },
                              );
                              setModal(null);
                            }, "Regeneration queued")
                          }
                        >
                          {label}
                        </button>
                      ))}
                      {!detail.test_mode && detail.status === "APPROVED" && (
                        <button
                          className="btn primary"
                          onClick={() => setModal("publish")}
                        >
                          Publish to YouTube
                        </button>
                      )}
                    </div>
                  </>
                )}
              </>
            ) : modal === "edit-script" ? (
              <>
                <h2>Edit narration</h2>
                <form
                  onSubmit={(e) => {
                    e.preventDefault();
                    const f = new FormData(e.currentTarget);
                    void act(async () => {
                      await api(
                        root + `/videos/${selected}/regenerate`,
                        "POST",
                        {
                          stage: 3,
                          sentences: String(f.get("sentences"))
                            .split("\n")
                            .filter((s) => s.trim()),
                        },
                      );
                      setModal(null);
                    }, "Script saved; dependent stages queued");
                  }}
                >
                  <label>
                    One narration segment per line (3–12)
                    <textarea
                      name="sentences"
                      rows={12}
                      defaultValue={detail?.script?.sentences?.join("\n")}
                      required
                    />
                  </label>
                  <button className="btn primary full">
                    Save and regenerate
                  </button>
                </form>
              </>
            ) : modal === "publish" ? (
              <>
                <h2>Publish approved video</h2>
                <form
                  onSubmit={(e) => {
                    e.preventDefault();
                    const f = new FormData(e.currentTarget);
                    void act(async () => {
                      await api(root + `/videos/${selected}/publish`, "POST", {
                        privacy: f.get("privacy"),
                        confirmed: true,
                        publish_at: f.get("publish_at") || null,
                      });
                      setModal(null);
                    }, "YouTube upload complete");
                  }}
                >
                  <label>
                    Visibility
                    <select name="privacy">
                      <option value="private">Private</option>
                      <option value="unlisted">Unlisted</option>
                      <option value="public">Public</option>
                    </select>
                  </label>
                  <label>
                    Schedule (optional, RFC3339)
                    <input
                      name="publish_at"
                      placeholder="2026-10-01T12:00:00Z"
                    />
                  </label>
                  <button className="btn primary full">
                    Confirm YouTube upload
                  </button>
                </form>
              </>
            ) : null}
            {error && (
              <p className="modal-error" role="alert">
                {error}
              </p>
            )}
          </section>
        </div>
      )}
    </div>
  );
}

function Stat({
  label,
  value,
  suffix,
  note,
  icon,
}: {
  label: string;
  value: string;
  suffix?: string;
  note: string;
  icon?: React.ReactNode;
}) {
  return (
    <div className="stat">
      <div className="stat-label">
        {label}
        {icon}
      </div>
      <strong>
        {value}
        <small>{suffix}</small>
      </strong>
      <p>{note}</p>
    </div>
  );
}
function Empty({
  title,
  text,
  action,
}: {
  title: string;
  text: string;
  action?: () => void;
}) {
  return (
    <div className="empty">
      <span>✧</span>
      <h3>{title}</h3>
      <p>{text}</p>
      {action && (
        <button className="btn primary" onClick={action}>
          Connect server
        </button>
      )}
    </div>
  );
}
function OfficeForm({
  office,
  onSave,
}: {
  office?: Office;
  onSave: (body: any) => Promise<void>;
}) {
  const s = office?.settings;
  return (
    <form
      className="office-form"
      onSubmit={(e) => {
        e.preventDefault();
        const f = new FormData(e.currentTarget);
        let weights;
        try {
          weights = JSON.parse(String(f.get("weights")));
        } catch {
          alert("Category weights must be valid JSON");
          return;
        }
        void onSave({
          name: f.get("name"),
          settings: {
            direction: f.get("direction"),
            visual_source_mode: f.get("visual_source_mode"),
            visual_style_preset: f.get("visual_style_preset"),
            language: f.get("language"),
            audience: f.get("audience"),
            duration: Number(f.get("duration")),
            videos_per_day: Number(f.get("videos_per_day")),
            timezone: f.get("timezone"),
            upload_times: String(f.get("upload_times"))
              .split(",")
              .map((x) => x.trim()),
            daily_budget: Number(f.get("daily_budget")),
            monthly_budget: Number(f.get("monthly_budget")),
            review_mode: f.get("review") === "on",
            auto_upload: f.get("auto_upload") === "on",
            category_weights: weights,
            freeze_weights: true,
          },
        });
      }}
    >
      <label>
        Office name
        <input
          name="name"
          defaultValue={office?.name || ""}
          placeholder="Amazing Things"
          required
          maxLength={80}
        />
      </label>
      <label>
        Content direction
        <textarea
          name="direction"
          defaultValue={
            s?.direction || "Discover something surprising in under one minute."
          }
          rows={3}
          required
        />
      </label>
      <label>
        Visual Source Mode
        <select
          name="visual_source_mode"
          defaultValue={s?.visual_source_mode || "AI First"}
        >
          <option>AI First</option>
          <option>Mixed</option>
          <option>Real First</option>
        </select>
      </label>
      <label>
        Visual Style Preset
        <textarea
          name="visual_style_preset"
          required
          maxLength={500}
          rows={2}
          defaultValue={
            s?.visual_style_preset ||
            "cinematic, mysterious, educational, high-contrast, clean, visually striking"
          }
        />
      </label>
      <p>
        AI First는 장면별 AI 이미지를 우선 생성합니다. 실제 기록이 필요한 장면은
        외부 자료를 사용하며, 생성 실패를 임의 도형으로 대체하지 않습니다.
      </p>
      <div className="form-grid">
        <label>
          Primary language
          <input
            name="language"
            defaultValue={s?.language || "English"}
            required
          />
        </label>
        <label>
          Target audience
          <input
            name="audience"
            defaultValue={s?.audience || "Global curious viewers"}
            required
          />
        </label>
        <label>
          Target seconds
          <input
            name="duration"
            type="number"
            min={25}
            max={60}
            defaultValue={s?.duration || 35}
          />
        </label>
        <label>
          Videos per day
          <input
            name="videos_per_day"
            type="number"
            min={1}
            max={24}
            defaultValue={s?.videos_per_day || 3}
          />
        </label>
        <label>
          Daily budget (USD)
          <input
            name="daily_budget"
            type="number"
            step="0.01"
            min={0}
            defaultValue={s?.daily_budget ?? 3}
          />
        </label>
        <label>
          Monthly budget (USD)
          <input
            name="monthly_budget"
            type="number"
            step="0.01"
            min={0}
            defaultValue={s?.monthly_budget ?? 20}
          />
        </label>
        <label>
          Timezone
          <input name="timezone" defaultValue={s?.timezone || "UTC"} />
        </label>
        <label>
          Production times
          <input
            name="upload_times"
            defaultValue={s?.upload_times?.join(",") || "09:00,15:00,21:00"}
          />
        </label>
      </div>
      <label>
        Category weights (total 100)
        <textarea
          name="weights"
          defaultValue={JSON.stringify(
            s?.category_weights || {
              Fresh: 35,
              "Science / Space": 20,
              Mystery: 15,
              "Strange World": 15,
              Evergreen: 10,
              Experimental: 5,
            },
            null,
            2,
          )}
          rows={5}
        />
      </label>
      <label className="checkbox">
        <input
          type="checkbox"
          name="review"
          defaultChecked={s?.review_mode ?? true}
        />{" "}
        CEO review before publishing
      </label>
      <label className="checkbox">
        <input
          type="checkbox"
          name="auto_upload"
          defaultChecked={s?.auto_upload ?? false}
        />{" "}
        Automatically upload when all QC / review gates pass
      </label>
      <button className="btn primary full">
        {office ? "Save office settings" : "Create office"}
      </button>
    </form>
  );
}

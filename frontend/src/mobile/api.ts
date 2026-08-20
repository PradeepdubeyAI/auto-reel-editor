// Mobile orchestration — talks to the EXISTING backend (upload/plan/fetch/cache/zoom/render).
// Self-contained (no desktop store) so the mobile page stays independent. Auto-accepts overlays
// (top B-roll pick per scene, all cards, all zooms) for the one-tap fast path; the Result screen
// re-bakes with tweaks over the SAME ingested base.

// The 8 looks vex/color_grading.py's engine already implements (SUPPORTED_COLOR_GRADE_LOOKS) —
// this was previously hardcoded server-side to "natural" with zero user choice anywhere.
export const COLOR_GRADE_LOOKS = ["auto", "natural", "vibrant", "cinematic", "warm", "cool", "documentary", "punchy"] as const;
export type ColorGradeLook = (typeof COLOR_GRADE_LOOKS)[number];

export type Recipe = {
  removeSilences: boolean;
  removeRetakes: boolean; // separate from removeSilences -- cuts real spoken content, not dead air
  cleanAudio: boolean;
  colorGrade: boolean;
  colorGradeLook: ColorGradeLook;
  colorGradeIntensity: number; // 0.0-1.5, engine-side range; UI exposes it as a 0-100% slider
};
export type Toggles = Recipe & {
  captions: boolean;
  broll: boolean;
  cards: boolean;
  zoom: boolean;
  // Render-tier (goes through /api/broll/render, not /api/upload) — unlike the Recipe fields
  // above, this is read fresh on every render/re-render, so it CAN be changed in Fine-tune even
  // though there's no dedicated UI for it there yet (v1 only exposes it in Setup's CATALOG).
  smoothTransitions: boolean;
  // Render-tier. Places a few sound effects automatically off signals the pipeline already
  // computes (B-roll cutaways, zooms, cards) -- manual placement in the SFX sheet still overrides.
  sfxAuto: boolean;
  // Also render-tier. Unlike every other toggle here, defaults OFF — silence/original audio is
  // a valid default many users want, unlike e.g. captions or trim-silence.
  music: boolean;
};

export type ProgressEvt = { event?: string; name?: string; status?: string; text?: string };
export type Moment = {
  momentId: string;
  type: "scene" | "text_or_stat" | "abstract";
  spanStartMs: number;
  spanEndMs: number;
  primaryQuery: string;
  fallbackQueries: string[];
  transcriptPhrase: string;
  card?: BrollCard | null;
  // Set when the planner matched this scene to one of the user's own uploaded clips instead of
  // a stock search (see broll_plan.py's USER_CLIPS_ADDENDUM) — null/absent means "search stock".
  assignedUserClipKey?: string | null;
};
// A clip the user uploaded via uploadBrollClip(), with their one-line description of its
// content — fed to planBroll() so the LLM can match it to a moment instead of stock search.
export type UserBrollClip = {
  key: string;
  url: string;
  kind: "video" | "image";
  description: string;
  durationMs: number;
};
export type BrollCard = {
  cardType: "stat" | "phrase" | "list";
  headline: string;
  value?: string | null;
  items?: string[] | null;
  style?: "ink" | "amber" | "night";
};
export type Candidate = {
  id: string;
  source: string;
  kind: "video" | "image";
  thumbUrl: string;
  mediaUrl: string;
  width: number;
  height: number;
};
export type Zoom = {
  startMs: number; endMs: number; style: "slow_push" | "quick_punch"; targetScale: number;
  transcriptPhrase?: string;
  anchor?: "face" | "center";   // where the punch-in centres; face is the default
  off?: boolean;                // switched off in the Zoom sheet -- kept so it can be switched back
};

// A chosen overlay ready for the render endpoint.
export type Accepted =
  | { momentId: string; startMs: number; endMs: number; kind: "card"; card: BrollCard }
  | {
      momentId: string; startMs: number; endMs: number; mediaKind: "video" | "image"; url: string;
      // Retime (Fine-tune trim slider) — ms into the SOURCE clip to start playing from.
      // Video only; omitted/0 = original untouched behavior (play from the clip's own start).
      sourceStartMs?: number;
    };

// Caption-style registry (served by /api/caption-styles — the UI never re-declares style data).
export type CaptionStyleInfo = {
  id: string;
  label: string;
  available: boolean;
  defaultSizePx: number;
  defaultBottomPercent: number;
  uppercase: string;
  boxStyle: string;
  activeColor: string;
  inactiveColor: string;
  outlineWidth: number;
  outlineColor: string;
  boxColor: string;
  boxOpacity: number;
  // motion fields (default off) — used by the animated in-picker previews
  glowColor: string;
  glowBlur: number;
  fadeMs: number;
  fillColor: string;
  karaokeFill: boolean;
  typewriter: boolean;
};
export type CaptionRegistry = {
  styles: CaptionStyleInfo[];
  sizePresets: Record<string, number>; // S/M/L -> multiplier on the style's default size
  position: { min: number; max: number };
  defaultStyleId: string;
};
// Caption selection carried into render(); undefined => the default (amber) path, untouched.
export type CaptionSel = { styleId: string; sizePx?: number; bottomPercent?: number };

const j = async (r: Response) => {
  const d = await r.json().catch(() => ({}));
  if (!r.ok || d?.error) throw new Error(d?.error || `${r.status}`);
  return d;
};

// Run an SSE job to completion; forward step/log events; resolve on done, reject on error.
export function streamJob(jobId: string, onEvt?: (e: ProgressEvt) => void): Promise<{ output?: string; qc?: unknown[] }> {
  return new Promise((resolve, reject) => {
    const es = new EventSource(`/api/progress/${jobId}`);
    let settled = false;
    es.onmessage = (ev) => {
      let evt: ProgressEvt & { output?: string; qc?: unknown[]; message?: string };
      try {
        evt = JSON.parse(ev.data);
      } catch {
        return;
      }
      if (evt.event === "done") {
        settled = true;
        es.close();
        resolve(evt);
      } else if (evt.event === "error") {
        settled = true;
        es.close();
        reject(new Error(evt.message || "render error"));
      } else {
        onEvt?.(evt);
      }
    };
    es.onerror = () => {
      if (settled) return;
      settled = true;
      es.close();
      reject(new Error("progress stream closed"));
    };
  });
}

// 1) upload + ingest (front half of the pipeline with the chosen recipe). Switches ACTIVE server-side.
// Returns the project_id — the caller threads it back on every later call so this upload's
// project stays isolated from other concurrent users' projects.
export async function ingest(file: File, r: Recipe, onEvt?: (e: ProgressEvt) => void): Promise<string> {
  const qs = new URLSearchParams({
    name: file.name,
    clean_audio: String(r.cleanAudio),
    remove_silences: String(r.removeSilences),
    remove_retakes: String(r.removeRetakes),
    color_grade: String(r.colorGrade),
    color_grade_look: r.colorGradeLook,
    color_grade_intensity: String(r.colorGradeIntensity),
  });
  const { job_id, project_id } = await j(await fetch(`/api/upload?${qs.toString()}`, { method: "POST", body: file }));
  await streamJob(job_id, onEvt);
  return (project_id as string) || (job_id as string);
}

// 2) plan B-roll moments (scenes + graphic cards) over the ACTIVE transcript. Pass the user's
// own uploaded clips (key + one-line description) so the planner can match one to a moment
// instead of searching stock; unusedUserClipKeys lists any clip the planner didn't place.
export async function planBroll(
  projectId?: string,
  userClips?: UserBrollClip[],
): Promise<{ moments: Moment[]; unusedUserClipKeys: string[] }> {
  const d = await j(
    await fetch("/api/broll/plan", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        project: projectId,
        edits: { editedText: {} },
        userClips: (userClips ?? []).map((c) => ({ key: c.key, description: c.description })),
      }),
    }),
  );
  // prefer a server-assigned momentId (stable); fall back to index if the API doesn't send one
  const moments = (d.moments ?? []).map((m: Omit<Moment, "momentId"> & { momentId?: string }, i: number) => ({ ...m, momentId: m.momentId ?? `m${i}` }));
  return { moments, unusedUserClipKeys: (d.meta?.unusedUserClipKeys as string[]) ?? [] };
}

// Upload the user's own B-roll clip (Setup screen, when B-roll is on) — same cache the stock
// fetch/cache flow writes into, so it's usable by /api/broll/render exactly like a stock pick.
export async function uploadBrollClip(file: File, kind: "video" | "image"): Promise<UserBrollClip> {
  const qs = new URLSearchParams({ name: file.name, kind });
  const d = await j(await fetch(`/api/broll/upload?${qs.toString()}`, { method: "POST", body: file }));
  return { key: d.key as string, url: d.url as string, kind: d.kind as "video" | "image", description: "", durationMs: (d.durationMs as number) ?? 0 };
}

// 3) plan auto-zoom (dual-signal) over the ACTIVE base.
export async function planZooms(projectId?: string): Promise<Zoom[]> {
  const d = await j(
    await fetch("/api/zoom/plan", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ project: projectId, edits: { editedText: {} } }),
    }),
  );
  // zoom_plan emits spanStartMs/spanEndMs; normalize to the {startMs,endMs} shape render() maps.
  // (Fixes a latent key-mismatch where mobile auto-zoom silently no-op'd — zooms were dropped
  // at the render sanitizer because startMs/endMs were undefined.)
  return (d.zooms ?? []).map((z: Zoom & { spanStartMs?: number; spanEndMs?: number }) => ({
    ...z,
    startMs: z.spanStartMs ?? z.startMs,
    endMs: z.spanEndMs ?? z.endMs,
  }));
}

// fetch ranked candidates for a query, and cache the chosen one locally (Range-served for render).
export async function fetchCandidates(query: string, spanMs: number): Promise<Candidate[]> {
  const d = await j(
    await fetch("/api/broll/fetch", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query, page: 1, spanMs, orientation: "portrait" }),
    }),
  );
  return d.candidates ?? [];
}
export async function cacheMedia(mediaUrl: string, kind: "video" | "image"): Promise<string> {
  const d = await j(
    await fetch("/api/broll/cache", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url: mediaUrl, kind }),
    }),
  );
  return d.url as string;
}

// fetch the caption-style registry (styles + size presets + position bounds). One source of truth.
export async function fetchCaptionStyles(): Promise<CaptionRegistry> {
  return j(await fetch("/api/caption-styles"));
}

// SFX catalog (17 bundled sounds, 5 categories) for Fine-tune's sound-effects picker.
export type SfxSound = { id: string; label: string; category: string; file: string; durationMs: number; url: string };
export type SfxCategory = { id: string; label: string };
export async function fetchSfxCatalog(): Promise<{ sounds: SfxSound[]; categories: SfxCategory[] }> {
  return j(await fetch("/api/sfx-catalog"));
}
// A sound placed at a specific moment, ready for the render endpoint.
export type PlacedSfx = {
  id: string; soundId: string; atMs: number; gainDb: number;
  // "auto" hits come from planSfx (see MobileApp); "manual" ones the user placed themselves. Kept so
  // the SFX sheet can show which is which and let either be removed -- same pattern as retakes.
  source?: "auto" | "manual";
  reason?: string;
};

// Music catalog (40 bundled CC0 tracks, 8 categories) + named ducking presets for Fine-tune's
// Music sheet. User-uploaded tracks are NOT listed here — see uploadMusicTrack().
export const DUCKING_LABELS: Record<string, string> = { off: "Off", light: "Light", medium: "Medium", strong: "Strong" };
export type MusicTrack = { id: string; label: string; category: string; file: string; durationMs: number; url: string };
export type MusicCategory = { id: string; label: string };
export async function fetchMusicCatalog(): Promise<{ tracks: MusicTrack[]; categories: MusicCategory[]; duckingPresets: string[] }> {
  return j(await fetch("/api/music-catalog"));
}
// The user's own uploaded track (Setup/Fine-tune Music sheet's Upload tab).
export type UserMusicTrack = { key: string; url: string; durationMs: number; label: string };
export async function uploadMusicTrack(file: File): Promise<UserMusicTrack> {
  const qs = new URLSearchParams({ name: file.name });
  const d = await j(await fetch(`/api/music/upload?${qs.toString()}`, { method: "POST", body: file }));
  return { key: d.key as string, url: d.url as string, durationMs: (d.durationMs as number) ?? 0, label: file.name };
}
// The currently-selected music for this project's render — trackId is either a catalog id
// (e.g. "montage") or an uploaded key (e.g. "up_xyz.mp3"); the server resolves either the same way.
export type MusicSelection = { trackId: string; gainDb: number; ducking: "off" | "light" | "medium" | "strong" };

// (COLOR_GRADE_LOOKS / ColorGradeLook already exported above, next to the Recipe type.)

// Real preview thumbnails (one small frame per look, actually graded from THIS project's own
// footage) for the Fine-tune "Look" picker — a plain-color swatch can't show what a look will
// actually do to this specific video, so this asks the server to render one. A null value for
// a given look means that ONE look's preview render failed; the picker falls back to a plain
// swatch for just that tile rather than failing the whole picker.
export async function fetchLookPreviews(
  projectId: string | undefined,
  looks: readonly ColorGradeLook[],
  intensity: number,
): Promise<Record<string, string | null>> {
  const d = await j(
    await fetch("/api/color-grade/preview", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ project: projectId, looks, intensity }),
    }),
  );
  return (d.previews ?? {}) as Record<string, string | null>;
}

// Fine-tune "Look" picker's Apply action: re-grade the cached pre-grade checkpoint with a new
// look/intensity WITHOUT re-transcribing, swap it in as the project's base, stream progress the
// same way every other job does. The caller still needs to re-render afterward (this only
// updates the base; overlays/captions/etc are baked back on top by the existing render() call).
export async function applyColorGrade(opts: {
  project?: string;
  look: ColorGradeLook;
  intensity: number;
  onEvt?: (e: ProgressEvt) => void;
}): Promise<{ regradedBase?: string }> {
  const { job_id } = await j(
    await fetch("/api/color-grade/apply", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ project: opts.project, look: opts.look, intensity: opts.intensity }),
    }),
  );
  // Return the exact regraded file the job produced so the caller can render directly
  // against it, instead of relying on the server having swapped the project's base pointer
  // in time (that swap-then-render ordering was racy — see /api/broll/render base_override).
  const done = await streamJob(job_id, opts.onEvt);
  return { regradedBase: done.output };
}

// Fine-tune "Trimmed" sheet: every retake cut so far (AI-detected or user-marked --
// text/reason/confidence), which are currently restored, the FULL pre-retake-cut transcript
// (for the tap-to-mark timeline), and how much runtime came from silence-trim vs. retake-cut.
// Empty/zeroed (not an error) when nothing was auto-cut for this project.
export type RetakeCut = {
  id: string; text: string; reason: string; confidence: "high" | "medium" | "manual";
  startMs?: number; endMs?: number;
  cutStartMs?: number; cutEndMs?: number; // the actual removed span, in checkpoint time
  outAtMs?: number | null;                // where this cut's seam falls on the RENDERED timeline
  restored: boolean;
};
export type RetakeWord = { startMs: number; endMs: number; text: string };
export type RetakeSegment = {
  segIndex: number; text: string; startMs: number; endMs: number;
  words: RetakeWord[]; // per-line words — the tappable precision targets (see markRetake)
};
export type RetakesInfo = {
  retakes: RetakeCut[]; segments: RetakeSegment[]; words: RetakeWord[];
  preRetakeDurationMs: number | null;
  silenceTrimmedMs: number; retakesTrimmedMs: number;
  originalDurationMs: number | null; currentDurationMs: number | null;
};
export async function fetchRetakes(projectId?: string): Promise<RetakesInfo> {
  const qs = projectId ? `?${new URLSearchParams({ project: projectId }).toString()}` : "";
  return j(await fetch(`/api/retakes${qs}`));
}

// Trimmed sheet's preview sources. `retakePreviewUrl` = the pre-cut checkpoint (everything
// still present, for a fast first pass); `retakeFinalUrl` = the project's CURRENT base, i.e.
// the already-cut video, for reviewing cuts against the real result. Both Range-served.
export function retakePreviewUrl(projectId?: string): string {
  const qs = projectId ? `?${new URLSearchParams({ project: projectId }).toString()}` : "";
  return `/api/retakes/preview${qs}`;
}
export function retakeFinalUrl(projectId?: string, version?: number): string {
  const p = new URLSearchParams();
  if (projectId) p.set("project", projectId);
  if (version != null) p.set("v", String(version)); // cache-bust after each re-apply
  const qs = p.toString();
  return `/api/retakes/final${qs ? `?${qs}` : ""}`;
}

// Trimmed sheet's manual trim: mark an explicit range for removal. Pass EITHER checkpoint-time
// (startMs/endMs, when marking on the pre-cut preview) OR rendered-time (outStartMs/outEndMs,
// when marking while watching the already-cut result) -- the server converts and snaps both
// edges outward to word boundaries. Only updates the ledger; call restoreRetakes to apply.
export async function markRetake(
  projectId: string | undefined,
  range: { startMs: number; endMs: number } | { outStartMs: number; outEndMs: number },
): Promise<{ id: string; startMs: number; endMs: number; text: string; snapped: boolean }> {
  return j(
    await fetch("/api/retakes/mark", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ project: projectId, ...range }),
    }),
  );
}

// Delete a USER-marked cut outright (tapping a red region off). AI-detected cuts aren't
// deletable this way -- those get "restored" via keepIds so their reasoning stays visible.
export async function unmarkRetake(projectId: string | undefined, id: string): Promise<{ removed: string }> {
  return j(
    await fetch("/api/retakes/unmark", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ project: projectId, id }),
    }),
  );
}

// Trimmed sheet's "restore" action: re-apply the retake-cut ledger against its checkpoint with
// `keepIds` (the FULL current set of restored ids, not a delta) excluded from the cut, swap in
// as the project's base. Caller re-renders on top of the returned base, same as applyColorGrade.
export async function restoreRetakes(opts: {
  project?: string;
  keepIds: string[];
  onEvt?: (e: ProgressEvt) => void;
}): Promise<{ restoredBase?: string }> {
  const { job_id } = await j(
    await fetch("/api/retakes/restore", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ project: opts.project, keepIds: opts.keepIds }),
    }),
  );
  const done = await streamJob(job_id, opts.onEvt);
  return { restoredBase: done.output };
}

// Publish kit: LLM-drafted title/description/hashtags from THIS project's transcript
// (server resolves `project` the same way every other route does — omit it only for the
// legacy single-project fallback). Used on the Saved screen to back real share/copy actions.
export type PublishKit = { title: string; description: string; hashtags: string[] };
export async function fetchPublishKit(projectId?: string): Promise<PublishKit> {
  const qs = projectId ? `?${new URLSearchParams({ project: projectId }).toString()}` : "";
  return j(await fetch(`/api/publish-kit${qs}`, { method: "POST" }));
}

// 4) bake: composite base + accepted overlays + zooms (+ captions unless off) -> final reel + QC.
export async function render(opts: {
  accepted: Accepted[];
  zooms: Zoom[];
  captionsOff: boolean;
  caption?: CaptionSel; // undefined => default amber path (no styleId/size/position overrides sent)
  editedText?: Record<string, string>;
  project?: string; // this user's project id — keeps the bake on their own base, not another user's
  smoothTransitions?: boolean; // alpha-fade B-roll/Ken-Burns overlay entry+exit — default true server-side
  sfxHits?: PlacedSfx[]; // Fine-tune's sound-effects sheet placements — empty/absent = no audio change
  music?: MusicSelection | null; // Fine-tune's Music sheet selection — null/absent = no background track
  baseOverride?: string; // exact regraded base to bake on (from applyColorGrade) — avoids the base-swap race
  onEvt?: (e: ProgressEvt) => void;
}): Promise<{ outputName: string; qc: { label: string; pass: boolean; detail: string }[] }> {
  // Only send overrides the user actually chose — absent fields => the style's own default server-side.
  const c = opts.caption;
  const captionSettings: Record<string, unknown> = { style: "word-focus" };
  if (c?.styleId) captionSettings.styleId = c.styleId;
  if (c?.sizePx != null) captionSettings.sizePx = c.sizePx;
  if (c?.bottomPercent != null) captionSettings.bottomPercent = c.bottomPercent;
  const { job_id, output_name } = await j(
    await fetch("/api/broll/render", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        project: opts.project,
        accepted: opts.accepted,
        zooms: opts.zooms.filter((z) => !z.off).map((z) => ({
          startMs: z.startMs, endMs: z.endMs, style: z.style, targetScale: z.targetScale,
          anchor: z.anchor ?? "face",
        })),
        captionSettings,
        edits: { editedText: opts.editedText ?? {} },
        captionsOff: opts.captionsOff,
        smoothTransitions: opts.smoothTransitions ?? true,
        sfxHits: (opts.sfxHits ?? []).map((h) => ({ soundId: h.soundId, atMs: h.atMs, gainDb: h.gainDb })),
        music: opts.music ? { trackId: opts.music.trackId, gainDb: opts.music.gainDb, ducking: opts.music.ducking } : null,
        base_override: opts.baseOverride, // bake on the exact regraded file (avoids base-swap race)
        engine: "ffmpeg", // mobile bakes with the Remotion-free pure-ffmpeg path
      }),
    }),
  );
  const done = await streamJob(job_id, opts.onEvt);
  return { outputName: output_name as string, qc: (done.qc as { label: string; pass: boolean; detail: string }[]) ?? [] };
}

// Fine-tune's chat editor — structured-intent extraction, not an agentic execution loop (see
// chat_agent.py's docstring). One LLM call per turn; the client applies "actions" itself via its
// own already-tested functions (swapClip, retimeClip, setToggles, etc) after the user confirms.
export type ChatMessage = { role: "user" | "assistant"; content: string };
export type ToggleKey = "captions" | "broll" | "cards" | "zoom" | "smoothTransitions" | "music";
export type ChatAction =
  | { tool: "swap_broll_clip"; momentId: string }
  | { tool: "set_broll_removed"; momentId: string; removed: boolean }
  | { tool: "retime_broll_clip"; momentId: string; sourceStartMs: number }
  | { tool: "set_caption_style"; styleId: string }
  | { tool: "set_toggle"; key: ToggleKey; on: boolean }
  | { tool: "set_color_grade_look"; look: ColorGradeLook; intensity: number }
  | { tool: "add_sfx"; soundId: string; atMs: number }
  | { tool: "remove_sfx"; id: string }
  | { tool: "set_music"; trackId: string; gainDb: number; ducking: "off" | "light" | "medium" | "strong" };

export async function chatEdit(message: string, history: ChatMessage[], state: unknown): Promise<{ reply: string; actions: ChatAction[] }> {
  return j(
    await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message, history, state }),
    }),
  );
}

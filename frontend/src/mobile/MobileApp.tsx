import { forwardRef, useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ArrowLeft,
  Blend,
  Captions,
  Check,
  ChevronRight,
  Clapperboard,
  Copy,
  Download,
  Camera,
  LayoutTemplate,
  Loader2,
  MessageCircle,
  Mic,
  MoreHorizontal,
  Music2,
  Palette,
  Pause,
  Pencil,
  Play,
  Plus,
  Redo2,
  RotateCcw,
  Scissors,
  Share2,
  Shuffle,
  Trash2,
  UploadCloud,
  Volume2,
  X,
  Zap,
  ZoomIn,
  type LucideIcon,
} from "lucide-react";
import {
  applyColorGrade,
  cacheMedia,
  chatEdit,
  COLOR_GRADE_LOOKS,
  DUCKING_LABELS,
  fetchCandidates,
  fetchCaptionStyles,
  fetchMusicCatalog,
  fetchPublishKit,
  fetchRetakes,
  fetchSfxCatalog,
  ingest,
  markRetake,
  planBroll,
  planZooms,
  render,
  retakeFinalUrl,
  retakePreviewUrl,
  restoreRetakes,
  unmarkRetake,
  uploadBrollClip,
  uploadMusicTrack,
  type Accepted,
  type BrollCard,
  type Candidate,
  type CaptionRegistry,
  type CaptionSel,
  type CaptionStyleInfo,
  type ChatAction,
  type ChatMessage,
  type ColorGradeLook,
  type Moment,
  type MusicCategory,
  type MusicSelection,
  type MusicTrack,
  type PlacedSfx,
  type ProgressEvt,
  type PublishKit,
  type RetakeCut,
  type RetakesInfo,
  type SfxCategory,
  type SfxSound,
  type Toggles,
  type UserBrollClip,
  type UserMusicTrack,
  type Zoom,
} from "./api";
import { POPPINS_DATA_URL } from "./poppinsData";
import logoSuper from "./assets/logo-super-ink.svg";
import logoUp from "./assets/logo-up-red.svg";

// Bundle the SAME Poppins ExtraBold the render uses, so the caption preview matches the output.
// Also register the keyframes the animated previews use (fade / karaoke fill / typewriter / glow).
const CAP_FONT_ID = "poppins-caption-preview";
if (typeof document !== "undefined" && !document.getElementById(CAP_FONT_ID)) {
  const st = document.createElement("style");
  st.id = CAP_FONT_ID;
  st.textContent =
    `@font-face{font-family:"PoppinsCap";font-weight:800;font-style:normal;font-display:block;src:url(${POPPINS_DATA_URL}) format("truetype");}` +
    `@keyframes capFade{0%,8%{opacity:.10}30%,70%{opacity:1}92%,100%{opacity:.10}}` +
    `@keyframes capWipe{0%{clip-path:inset(0 100% 0 0)}75%,100%{clip-path:inset(0 0 0 0)}}` +
    `@keyframes capType{from{clip-path:inset(0 100% 0 0)}to{clip-path:inset(0 -0.12em 0 0)}}` +
    `@keyframes capGlow{0%,100%{filter:brightness(1)}50%{filter:brightness(1.35)}}`;
  document.head.appendChild(st);
}

const PREVIEW_PHRASE = ["seekho", "AI", "kaise", "kaam", "karta", "hai"];
const CARD_PHRASE = PREVIEW_PHRASE.slice(0, 4);
const CARD_ACTIVE = 1;

type CaptionUI = { styleId: string; sizePx: number; bottomPercent: number };

function defaultCaptionFor(reg: CaptionRegistry): CaptionUI {
  const s = reg.styles.find((x) => x.id === reg.defaultStyleId) ?? reg.styles[0];
  return { styleId: s.id, sizePx: s.defaultSizePx, bottomPercent: s.defaultBottomPercent };
}

function toCaptionSel(c: CaptionUI | null, reg: CaptionRegistry | null): CaptionSel | undefined {
  if (!c || !reg) return undefined;
  const s = reg.styles.find((x) => x.id === c.styleId);
  if (!s) return undefined;
  return { styleId: c.styleId, sizePx: Math.round(c.sizePx), bottomPercent: c.bottomPercent };
}

// ---- toggle catalogue (order = on-screen order) --------------------------------------
// Restricted to Toggles' BOOLEAN fields only — Toggles still carries colorGradeLook
// (string) / colorGradeIntensity (number), but there's no CATALOG entry for colorGrade at
// all anymore: color grade is no longer a Setup-time choice (see the Toggles init below —
// it's fixed on with a "natural" look) and is instead picked AFTER the first render, in the
// Ready/Fine-tune "Look" sheet, where a real live preview against the actual footage exists.
// No CATALOG entry for "music" either, and for the same reason as colorGrade PLUS one more:
// there's no separate on/off switch for it — a selected track (musicSelection) IS the on/off
// state (see reRender/finishRender). A redundant toggle row here could disagree with the
// actual selection, which is exactly how music got picked but silently never rendered before.
type Key = { [K in keyof Toggles]: Toggles[K] extends boolean ? K : never }[keyof Toggles];
const OVERLAY_KEYS: Key[] = ["captions", "broll", "cards", "zoom"];
const CATALOG: { key: Key; title: string; desc: string; color: string; Icon: LucideIcon }[] = [
  { key: "removeSilences", title: "Trim silences", desc: "Cut dead air & long pauses", color: "var(--su-forest)", Icon: Scissors },
  // Separate from removeSilences on purpose: this cuts real spoken content (a redone line),
  // not dead air, so it's a different kind of risk and gets its own on/off switch rather than
  // being silently bundled into the silence toggle. Biased conservative (see pipeline.py's
  // _RETAKE_JUDGE_SYSTEM) -- when unsure it leaves the retake in rather than cutting real content.
  { key: "removeRetakes", title: "Remove retakes", desc: "Cut redone lines (auto-detected, reviewable after)", color: "var(--su-coral)", Icon: Redo2 },
  { key: "cleanAudio", title: "Clean audio", desc: "Reduce background noise", color: "var(--su-red)", Icon: Mic },
  { key: "captions", title: "Captions", desc: "Hinglish, word-by-word", color: "var(--su-amber)", Icon: Captions },
  { key: "broll", title: "B-roll", desc: "Auto stock clips on key moments", color: "var(--su-plum)", Icon: Clapperboard },
  { key: "cards", title: "Graphic cards", desc: "Stat / quote overlays", color: "var(--su-rust)", Icon: LayoutTemplate },
  { key: "zoom", title: "Auto-zoom", desc: "Emphasis punch-ins", color: "var(--su-periwinkle)", Icon: ZoomIn },
  // v1 correctness fix, not a stylistic add-on: B-roll/cards already had NO fade at all (an
  // instant hard-cut pop); graphic cards already fade, this extends the same treatment so all
  // three overlay kinds behave consistently. Deliberately just on/off — no transition-style
  // picker (crossfade/whip-pan/glitch/etc), since short-form-editing research is consistent
  // that a small, near-invisible technique beats a menu of rarely-used options for this genre.
  { key: "sfxAuto", title: "Sound effects", desc: "A few accents on cutaways, zooms & cards", color: "var(--su-indigo)", Icon: Volume2 },
  { key: "smoothTransitions", title: "Smooth cutaways", desc: "Fade B-roll in & out, not a hard cut", color: "var(--su-teal)", Icon: Blend },
];

// Mirrors zoom_plan.py's own tiers (SCALE_GENTLE / SCALE_SLOW / SCALE_PUNCH) so a chip here means
// the same thing the planner means. Research is consistent that this audience wants presets, not
// keyframes -- three named strengths beats a raw scale slider nobody can calibrate by eye.
const ZOOM_STRENGTH = { subtle: 1.14, medium: 1.24, strong: 1.42 } as const;
const zoomTier = (s: number): keyof typeof ZOOM_STRENGTH =>
  s < 1.19 ? "subtle" : s < 1.33 ? "medium" : "strong";

type Screen = "create" | "setup" | "processing" | "ready" | "finetune" | "saved";
type Phase = { key: string; label: string; status: "pending" | "run" | "done" };
type SceneChoice = {
  candidates: Candidate[]; idx: number; cachedUrl: string; removed: boolean;
  // Retime (Fine-tune trim slider) — only meaningful when the source is a video AND its real
  // duration is known (user uploads report it; stock candidates don't, so they never get a
  // slider — retiming a pre-trimmed stock clip is much lower value than a user's own footage).
  sourceStartMs?: number;
  sourceDurationMs?: number;
  // Set only when this moment was matched to one of the user's own clips (not a stock search) —
  // shown in place of primaryQuery so the review/Fine-tune lists reflect what actually got picked.
  userClipDescription?: string;
};
type QcCheck = { label: string; pass: boolean; detail: string };

const friendly = (e: ProgressEvt): string => {
  if (e.event === "step" && e.name) return `${e.name}…`;
  if (e.event === "log" && e.text) return String(e.text).slice(0, 80);
  return "";
};

const fmtMs = (ms: number): string => {
  const s = Math.max(0, Math.round(ms / 1000));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
};

// Whole-second display is fine for durations, but not for a cut EDGE: rounding a boundary to the
// nearest second hides exactly the precision the user is trying to set, and you can't target what
// you can't read. Tenths match what word-level timings can actually justify (ASR word boundaries
// are only accurate to tens of ms at best, worse for code-switched speech), so showing frames or
// milliseconds here would imply precision the data doesn't have.
const fmtMsPrecise = (ms: number): string => {
  const t = Math.max(0, ms) / 1000;
  const m = Math.floor(t / 60);
  return `${m}:${(t - m * 60).toFixed(1).padStart(4, "0")}`;
};

// ------------------------------------------------------------------ small UI bits ----------
function Wordmark() {
  return (
    <div className="su-wordmark" aria-label="superUP">
      <img src={logoSuper} alt="" />
      <img src={logoUp} alt="" />
    </div>
  );
}

function Switch({ on, onClick }: { on: boolean; onClick: () => void }) {
  return (
    <div className={`su-switch${on ? " on" : ""}`} role="switch" aria-checked={on} onClick={onClick}>
      <div className="knob" />
    </div>
  );
}

function ToggleRow({ item, on, disabled, onToggle }: { item: (typeof CATALOG)[number]; on: boolean; disabled?: boolean; onToggle: () => void }) {
  const { Icon } = item;
  return (
    <div className="su-row" style={{ opacity: disabled ? 0.55 : 1 }}>
      <div className="su-ico" style={{ background: item.color }}>
        <Icon size={18} />
      </div>
      <div className="txt">
        <div className="title">{item.title}</div>
        <div className="desc">{item.desc}</div>
      </div>
      {disabled ? <Check size={18} color="var(--su-ok)" /> : <Switch on={on} onClick={onToggle} />}
    </div>
  );
}

// ---- look picker (color grade) -------------------------------------------------------
// Exposes the 8 looks vex/color_grading.py's engine already implements. Color grade is
// baked at ingest (like clean-audio/trim-silence), so this picker only ever appears
// post-render (Ready/Fine-tune's "Look" sheet) — Setup uses a fixed "natural" default and
// no longer shows this at all, since re-grading afterward (via the pregrade checkpoint,
// see applyLook) is just as cheap and now comes with a real live preview against the
// actual video (a CSS `filter` applied to the PreviewPlayer below, swapped instantly per
// tap — see LOOK_FILTER_BASE/cssFilterFor) instead of a per-look static thumbnail render.
const LOOK_COLORS: Record<ColorGradeLook, string> = {
  auto: "var(--su-indigo)",
  natural: "var(--su-forest)",
  vibrant: "var(--su-amber)",
  cinematic: "var(--su-maroon)",
  warm: "var(--su-rust)",
  cool: "var(--su-periwinkle)",
  documentary: "var(--su-teal)",
  punchy: "var(--su-plum)",
};
const LOOK_LABELS: Record<ColorGradeLook, string> = {
  auto: "Auto", natural: "Natural", vibrant: "Vibrant", cinematic: "Cinematic",
  warm: "Warm", cool: "Cool", documentary: "Documentary", punchy: "Punchy",
};
const LOOK_DESCRIPTIONS: Record<ColorGradeLook, string> = {
  auto: "Adapts per scene — the engine picks a grade shot-by-shot",
  natural: "Subtle, true-to-life polish (today's default)",
  vibrant: "Bolder color, extra pop",
  cinematic: "Cooler shadows, filmic contrast",
  warm: "Golden-hour warmth",
  cool: "Crisp, blue-leaning tone",
  documentary: "Flat, honest, minimally graded",
  punchy: "High contrast, saturated — social-feed loud",
};

// Approximate each server-side ffmpeg look as a CSS `filter()` chain so it can be swapped
// onto a live <video> element with zero latency and no server round-trip. This is a live-
// scrub aid, not a pixel-exact match — CSS filter is a single flat color matrix, so it can't
// reproduce true per-luminance split-toning (e.g. cinematic's teal-shadow/orange-highlight);
// the real grade only happens server-side via applyLook's regrade job. Deltas from neutral
// are scaled by `intensity` (0-1, the same slider that drives the real grade's strength) so
// dragging the slider updates the live preview too, not just picking a different look.
const LOOK_FILTER_BASE: Record<ColorGradeLook, { contrast?: number; brightness?: number; saturate?: number; sepia?: number; grayscale?: number; hueRotate?: number }> = {
  auto: { contrast: 1.04, brightness: 1.02, saturate: 1.04 },
  natural: { contrast: 1.05, brightness: 1.02, saturate: 1.05 },
  vibrant: { saturate: 1.6, contrast: 1.15, brightness: 1.05 },
  cinematic: { contrast: 1.2, saturate: 0.82, brightness: 0.95, sepia: 0.15, hueRotate: -8 },
  warm: { sepia: 0.25, saturate: 1.35, hueRotate: -8, brightness: 1.05, contrast: 1.05 },
  cool: { saturate: 1.1, contrast: 1.1, hueRotate: 10, grayscale: 0.04 },
  documentary: { grayscale: 0.18, contrast: 1.08, saturate: 0.85, brightness: 0.98 },
  punchy: { contrast: 1.35, saturate: 1.55, brightness: 1.06, sepia: 0.05 },
};
function cssFilterFor(look: ColorGradeLook, intensity: number): string {
  const b = LOOK_FILTER_BASE[look];
  const scale = (base: number | undefined, neutral: number) => neutral + ((base ?? neutral) - neutral) * intensity;
  const parts = [
    `contrast(${scale(b.contrast, 1).toFixed(3)})`,
    `brightness(${scale(b.brightness, 1).toFixed(3)})`,
    `saturate(${scale(b.saturate, 1).toFixed(3)})`,
  ];
  if (b.sepia) parts.push(`sepia(${(b.sepia * intensity).toFixed(3)})`);
  if (b.grayscale) parts.push(`grayscale(${(b.grayscale * intensity).toFixed(3)})`);
  if (b.hueRotate) parts.push(`hue-rotate(${(b.hueRotate * intensity).toFixed(1)}deg)`);
  return parts.join(" ");
}

function LookPicker({ look, intensity, onChange }: {
  look: ColorGradeLook; intensity: number; onChange: (look: ColorGradeLook, intensity: number) => void;
}) {
  return (
    <div className="su-stack" style={{ gap: 12 }}>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 8 }}>
        {COLOR_GRADE_LOOKS.map((l) => {
          const active = l === look;
          return (
            <button
              key={l}
              onClick={() => onChange(l, intensity)}
              style={{
                display: "flex", flexDirection: "column", alignItems: "center", gap: 6,
                padding: "8px 4px", borderRadius: 12, cursor: "pointer",
                border: active ? `2px solid ${LOOK_COLORS[l]}` : "1px solid var(--su-line-strong)",
                background: active ? "var(--su-cream-100)" : "#fff",
              }}
            >
              <div style={{ width: 26, height: 26, borderRadius: "50%", background: LOOK_COLORS[l] }} />
              <div style={{ fontSize: 11, fontWeight: 700, color: "var(--su-ink-900)" }}>{LOOK_LABELS[l]}</div>
            </button>
          );
        })}
      </div>
      <div style={{ fontSize: 12, color: "var(--su-ink-500)" }}>{LOOK_DESCRIPTIONS[look]}</div>
      <div>
        <div style={{ display: "flex", justifyContent: "space-between", fontSize: 12, color: "var(--su-ink-700)", marginBottom: 6 }}>
          <span>Intensity</span>
          <span>{Math.round(intensity * 100)}%</span>
        </div>
        <input
          type="range" min={0} max={100} step={5}
          value={Math.round(intensity * 100)}
          onChange={(e) => onChange(look, Number(e.target.value) / 100)}
          style={{ width: "100%" }}
        />
      </div>
    </div>
  );
}

function Sheet({ title, sub, onClose, children }: { title: string; sub?: string; onClose: () => void; children: React.ReactNode }) {
  return (
    <div className="su-sheet-bg" onClick={onClose}>
      <div className="su-sheet" onClick={(e) => e.stopPropagation()}>
        <div className="grab" />
        <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: 12 }}>
          <div>
            <h3>{title}</h3>
            {sub ? <div className="muted">{sub}</div> : null}
          </div>
          <button className="su-iconbtn" onClick={onClose} aria-label="Close">
            <X size={16} />
          </button>
        </div>
        <div style={{ marginTop: 14 }}>{children}</div>
      </div>
    </div>
  );
}

function VideoPlayer({ src, muted = false, compact = false, big = false }: { src: string; muted?: boolean; compact?: boolean; big?: boolean }) {
  const ref = useRef<HTMLVideoElement | null>(null);
  const [playing, setPlaying] = useState(false);
  const [prog, setProg] = useState(0);
  const [cur, setCur] = useState(0);
  const [dur, setDur] = useState(0);
  const toggle = () => {
    const v = ref.current;
    if (!v) return;
    if (v.paused) void v.play();
    else v.pause();
  };
  const seek = (e: React.MouseEvent<HTMLDivElement>) => {
    const v = ref.current;
    if (!v || !dur) return;
    const r = e.currentTarget.getBoundingClientRect();
    v.currentTime = Math.max(0, Math.min(1, (e.clientX - r.left) / r.width)) * dur;
  };
  const fmt = (s: number) => (isFinite(s) ? `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}` : "0:00");
  return (
    <div className={`su-player${compact ? " compact" : ""}${big ? " big" : ""}`}>
      <video
        ref={ref}
        src={src}
        muted={muted}
        playsInline
        onClick={toggle}
        onPlay={() => setPlaying(true)}
        onPause={() => setPlaying(false)}
        onLoadedMetadata={() => setDur(ref.current?.duration ?? 0)}
        onTimeUpdate={() => {
          const v = ref.current;
          if (!v) return;
          setCur(v.currentTime);
          setProg(v.duration ? v.currentTime / v.duration : 0);
        }}
      />
      {!playing && (
        <button className="su-play" onClick={toggle} aria-label="Play">
          <Play size={compact ? 20 : 24} fill="currentColor" />
        </button>
      )}
      <span className="su-vtime">{fmt(cur)} / {fmt(dur)}</span>
      <div className="su-vbar" onClick={seek}>
        <div className="su-vfill" style={{ width: `${prog * 100}%` }} />
      </div>
    </div>
  );
}

// A raw <video> (native controls, real rendered result) with its ref exposed to the parent —
// shared by the Look sheet (attaches a live CSS `filter`) and the Music sheet (plays alongside
// a second native <audio> element, see useMusicAudition below). One player, two very different
// live-preview layers on top of it, instead of each sheet building its own bespoke player.
const PreviewPlayer = forwardRef<HTMLVideoElement, { src: string; filter?: string }>(
  function PreviewPlayer({ src, filter }, ref) {
    return (
      <video
        ref={ref}
        src={src}
        controls
        playsInline
        style={{ width: "100%", borderRadius: 10, background: "#000", filter: filter || "none" }}
      />
    );
  },
);

// ---- live music audition (two plain native media elements) ---------------------------
// Lets the Music sheet play the actual rendered video's own audio AND a candidate track
// together, live, with instant volume control — no server round-trip needed just to audition
// a pick. Deliberately NOT built on the Web Audio API (an earlier version used AudioContext +
// createMediaElementSource + a GainNode graph): that approach has two sharp edges that bit us
// in practice — (1) connecting a media element to a Web Audio graph reroutes its audio through
// the graph FOR THE LIFETIME OF THAT ELEMENT, even after the context is closed, so the video
// could go permanently silent once a preview had run once; (2) AudioContext starts "suspended"
// and resume() is async, so audio (both the video's own AND the music) could stay silent for a
// beat after tapping play, which read as "the video just stopped." Two native elements with
// their own .volume property sidestep both problems entirely — simpler and far more robust.
// Ducking here is a flat volume multiplier per preset (not real-time speech-gated) — a
// deliberate simplification so this can't silently break; the REAL sidechain-ducked mix only
// ever happens server-side via reRender() on "Apply & re-render".
function useMusicAudition() {
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const musicRef = useRef<HTMLAudioElement | null>(null);
  const [active, setActive] = useState(false);

  const applyVolume = useCallback((gainDb: number, ducking: MusicSelection["ducking"]) => {
    const el = musicRef.current;
    if (!el) return;
    const duckFactor = ducking === "strong" ? 0.35 : ducking === "medium" ? 0.55 : ducking === "light" ? 0.75 : 1;
    el.volume = Math.max(0, Math.min(1, Math.pow(10, gainDb / 20) * duckFactor));
  }, []);

  const stop = useCallback(() => {
    videoRef.current?.pause();
    musicRef.current?.pause();
    setActive(false);
  }, []);

  // Must run synchronously inside the tap handler that picked/changed the track — that tap IS
  // the user gesture browsers require before unmuted audio is allowed to start.
  const start = useCallback((url: string, gainDb: number, ducking: MusicSelection["ducking"]) => {
    const v = videoRef.current, m = musicRef.current;
    if (!v || !m) return;
    m.pause();
    m.src = url;
    m.loop = true;
    m.currentTime = 0;
    applyVolume(gainDb, ducking);
    v.currentTime = 0;
    void v.play();
    void m.play();
    setActive(true);
  }, [applyVolume]);

  useEffect(() => () => stop(), [stop]); // safety net on full unmount

  // Memoize the returned object so its identity is STABLE across renders. Without this it was a
  // fresh literal every render, so any effect depending on `musicAudition` (e.g. the "stop audio
  // on sheet change" effect) re-ran on EVERY render and paused audio the instant it started --
  // that was the SFX ▶ preview's "AbortError: play() interrupted by pause()" bug. refs are
  // stable and start/stop/applyVolume are useCallback, so this only changes when `active` does.
  return useMemo(() => ({ videoRef, musicRef, active, start, stop, applyVolume }), [active, start, stop, applyVolume]);
}

// ==========================================================================================
// dev-only screen preview: /mobile.html?dev=<screen> jumps straight to a screen with mock data,
// so any screen can be inspected in isolation. Gated behind import.meta.env.DEV — a no-op in prod.
const DEV_SCREEN = ((): Screen | null => {
  if (!import.meta.env.DEV || typeof location === "undefined") return null;
  const s = new URLSearchParams(location.search).get("dev");
  const all: Screen[] = ["create", "setup", "processing", "ready", "finetune", "saved"];
  return all.includes(s as Screen) ? (s as Screen) : null;
})();

export default function MobileApp() {
  const [screen, setScreen] = useState<Screen>(DEV_SCREEN ?? "create");
  const [file, setFile] = useState<File | null>(null);
  const [fileUrl, setFileUrl] = useState<string>("");
  const [hover, setHover] = useState(false);
  const fileRef = useRef<HTMLInputElement | null>(null);

  const [toggles, setToggles] = useState<Toggles>({
    removeSilences: true,
    removeRetakes: true,
    cleanAudio: true,
    // Fixed on, no Setup-time UI — color grade is chosen AFTER the first render (Ready/
    // Fine-tune's "Look" sheet, with a real live preview), not blind before it exists.
    colorGrade: true,
    // Matches the exact behavior this app already shipped (hardcoded look="natural",
    // intensity=0.5, server-side) — the untouched default must stay identical to before, or
    // every existing user's first render changes look before they've had a chance to pick one.
    colorGradeLook: "natural",
    colorGradeIntensity: 0.5,
    captions: true,
    broll: true,
    cards: true,
    zoom: true,
    smoothTransitions: true,
    sfxAuto: true,
    music: false,
  });

  const [reg, setReg] = useState<CaptionRegistry | null>(null);
  const [caption, setCaption] = useState<CaptionUI | null>(null);
  useEffect(() => {
    fetchCaptionStyles()
      .then((r) => {
        setReg(r);
        setCaption((c) => c ?? defaultCaptionFor(r)); // controls live from the start
      })
      .catch(() => {});
  }, []);

  const [phases, setPhases] = useState<Phase[]>(
    DEV_SCREEN === "processing"
      ? [
          { key: "prepare", label: "Preparing your clip", status: "done" },
          { key: "plan", label: "Planning moments", status: "run" },
          { key: "fetch", label: "Finding B-roll", status: "pending" },
          { key: "render", label: "Rendering your reel", status: "pending" },
        ]
      : [],
  );
  const [detail, setDetail] = useState("");
  const [error, setError] = useState<string | null>(null);

  const [moments, setMoments] = useState<Moment[]>([]);
  const [sceneChoices, setSceneChoices] = useState<Record<string, SceneChoice>>({});
  const [zooms, setZooms] = useState<Zoom[]>([]);
  // Cards are edited by editing the MOMENTS array itself (text/template/style/timing all live on
  // m.card), so buildAcceptedFrom needs no new plumbing -- it already reads m.card. Removal is the
  // one thing that can't be expressed that way without losing the card, so it's a separate id list
  // and stays reversible.
  const [removedCardIds, setRemovedCardIds] = useState<string[]>([]);
  const [words, setWords] = useState<{ id: string; text: string }[]>([]);
  const [editedText, setEditedText] = useState<Record<string, string>>({});
  const [projectId, setProjectId] = useState<string>(""); // isolates this user's project server-side
  const [reelMeta, setReelMeta] = useState<{ width: number; height: number; durationMs: number } | null>(null);
  const [retakesInfo, setRetakesInfo] = useState<RetakesInfo | null>(null);
  const [restoringRetakes, setRestoringRetakes] = useState(false);
  const [pendingKeepIds, setPendingKeepIds] = useState<string[]>([]); // Trimmed sheet's local edits, before Apply
  const [bakedCutIds, setBakedCutIds] = useState<string[]>([]);       // cuts present in the CURRENT render
  // SNAPSHOT undo for the Trim sheet, not a command log. The whole trim state is a small array of
  // cuts plus a list of ids, so copying it per action is free -- whereas a command log would need a
  // hand-written inverse for every action (mark / unmark / restore / word-range / island) and each
  // inverse is a chance to be subtly wrong. Undo never re-renders: it restores the decisions and
  // reconciles the server ledger, and pixels are only rebuilt on the next Apply.
  const [trimUndo, setTrimUndo] = useState<{ retakes: RetakeCut[]; keepIds: string[] }[]>([]);
  const [trimRedo, setTrimRedo] = useState<{ retakes: RetakeCut[]; keepIds: string[] }[]>([]);
  const [trimSyncing, setTrimSyncing] = useState(false);
  const previewRef = useRef<HTMLVideoElement | null>(null);
  const skipRafRef = useRef<number | null>(null);
  const zoomVidRef = useRef<HTMLVideoElement | null>(null);
  // The live transform applied to the zoom preview video. null = no preview running.
  const [selZoom, setSelZoom] = useState<number | null>(null);
  const [surfaceMs, setSurfaceMs] = useState(0);
  const [selBroll, setSelBroll] = useState<string | null>(null);   // the shared surface's playhead, for "move here"
  const [selCard, setSelCard] = useState<string | null>(null);
  const [zoomPv, setZoomPv] = useState<{ scale: number; left: number; top: number; ms: number } | null>(null);
  const zoomPvTimer = useRef<number | null>(null);
  const weMutedRef = useRef(false);   // only ever unmute a mute WE applied, never the user's own
  const [previewTimeMs, setPreviewTimeMs] = useState(0);
  // Refs mirroring the two pieces of trim state. snapshotTrim/undo/redo must read the values as of
  // the moment they run; closing over the state would make every one of them a new function on
  // every keystroke and could snapshot a stale copy.
  const retakesInfoRef = useRef<RetakesInfo | null>(null);
  const pendingKeepIdsRef = useRef<string[]>([]);
  const [marking, setMarking] = useState(false);
  const [pendingStartMs, setPendingStartMs] = useState<number | null>(null);
  // Transcript-based selection (the precision path). `expandedSeg` = which line is showing its
  // word chips; `wordSel` = the first/last word index picked within that line.
  const [expandedSeg, setExpandedSeg] = useState<number | null>(null);
  const [wordSel, setWordSel] = useState<{ seg: number; from: number; to: number } | null>(null);
  const [snapWarning, setSnapWarning] = useState<string | null>(null);
  // "before" = the pre-cut checkpoint (nothing removed yet — fast first pass, no render needed).
  // "after"  = the project's CURRENT base, i.e. cuts already applied — review the real result.
  const [trimMode, setTrimMode] = useState<"before" | "after">("before");
  const [showTrimDetail, setShowTrimDetail] = useState(false);

  const [result, setResult] = useState<{ outputName: string; qc: QcCheck[] } | null>(
    DEV_SCREEN && ["ready", "finetune", "saved"].includes(DEV_SCREEN) ? { outputName: "demo.mp4", qc: [] } : null,
  );
  const [rebaking, setRebaking] = useState(false);
  const [sheet, setSheet] = useState<null | "broll" | "cards" | "zooms" | "captions" | "look" | "brollReview" | "sfx" | "music" | "chat" | "trimmed">(null);
  const [regrading, setRegrading] = useState(false);
  const musicAudition = useMusicAudition();

  // Shared "one preview at a time" toggle for the SFX and Music catalog browse lists — was
  // previously `new Audio(url).play()` fired fresh on every click with no reference kept, so
  // repeated taps (or tapping two different sounds/tracks) stacked overlapping playback with no
  // way to stop it. One real <audio> element + "which id is currently playing" state fixes it:
  // tapping the SAME id again pauses it; tapping a DIFFERENT id swaps src and restarts; either
  // one also stops the Music sheet's own video+music audition (below) so at most one thing
  // plays at once, matching how a normal music/sound picker behaves.
  const [catalogPreviewId, setCatalogPreviewId] = useState<string | null>(null);
  const [sfxDebug, setSfxDebug] = useState<string>("");  // TEMP: visible readout of SFX ▶ preview attempts
  // Holds the currently-playing preview Audio. We create a FRESH `new Audio(url)` per tap
  // (the same pattern the instant in-video SFX preview uses and that's confirmed working)
  // rather than reusing one shared hidden <audio> element -- reusing/loading that shared
  // element was leaving the ▶ button doing nothing.
  const catalogPreviewRef = useRef<HTMLAudioElement | null>(null);
  const stopCatalogPreview = useCallback(() => {
    if (catalogPreviewRef.current) { catalogPreviewRef.current.pause(); catalogPreviewRef.current = null; }
    setCatalogPreviewId(null);
  }, []);
  const toggleCatalogPreview = useCallback((id: string, url: string) => {
    // stop whatever is currently previewing (enforces one-at-a-time)
    if (catalogPreviewRef.current) { catalogPreviewRef.current.pause(); catalogPreviewRef.current = null; }
    if (catalogPreviewId === id) { setCatalogPreviewId(null); setSfxDebug(`stopped ${id}`); return; }  // tapping the playing one again = stop
    musicAudition.stop();
    setSfxDebug(`tap ${id} → loading ${url}`);
    const a = new Audio(url);
    a.onended = () => setCatalogPreviewId((cur) => (cur === id ? null : cur));
    a.onerror = () => setSfxDebug(`LOAD ERROR ${id}: ${a.error?.code ?? "?"} (${url})`);
    catalogPreviewRef.current = a;
    setCatalogPreviewId(id);
    a.play().then(() => setSfxDebug(`▶ playing ${id}`)).catch((e) => {
      setSfxDebug(`PLAY REJECTED ${id}: ${(e as Error).name} — ${(e as Error).message}`);
      setCatalogPreviewId((cur) => (cur === id ? null : cur));
    });
  }, [catalogPreviewId, musicAudition]);

  // Fine-tune's sound-effects sheet: the bundled 17-sound catalog (fetched once) and whatever
  // the user has placed so far. sfxPlayerRef backs the sheet's embedded scrubber — "Add" reads
  // its currentTime, so placement is "wherever you scrubbed to," not a typed-in number.
  const [sfxCatalog, setSfxCatalog] = useState<{ sounds: SfxSound[]; categories: SfxCategory[] }>({ sounds: [], categories: [] });
  const [sfxHits, setSfxHits] = useState<PlacedSfx[]>([]);
  const sfxPlayerRef = useRef<HTMLVideoElement | null>(null);
  useEffect(() => { fetchSfxCatalog().then(setSfxCatalog).catch(() => {}); }, []);

  // INSTANT in-video SFX preview: as the SFX sheet's video plays, fire each placed hit's
  // sound the moment playback reaches its timestamp -- no server render needed, so adding a
  // sound + scrubbing back to that moment plays it right away. The exported reel still bakes
  // the SFX server-side (unchanged); this is purely a client-side live preview.
  useEffect(() => {
    const v = sfxPlayerRef.current;
    if (!v || sheet !== "sfx") return;
    // preload one Audio per placed hit (keyed by hit id so re-adds/removes stay in sync)
    const players: Record<string, HTMLAudioElement> = {};
    for (const h of sfxHits) {
      const snd = sfxCatalog.sounds.find((s) => s.id === h.soundId);
      if (snd) { const a = new Audio(snd.url); a.preload = "auto"; players[h.id] = a; }
    }
    const fired = new Set<string>();  // hits already played this pass (so we don't retrigger every timeupdate)
    const onTime = () => {
      const t = v.currentTime * 1000;
      for (const h of sfxHits) {
        // fire within a small window after the timestamp (timeupdate lands every ~250ms)
        if (!fired.has(h.id) && t >= h.atMs && t < h.atMs + 350) {
          fired.add(h.id);
          const a = players[h.id];
          if (a) { try { a.currentTime = 0; } catch { /* not loaded yet */ } void a.play().catch(() => {}); }
        }
        // re-arm once playback has moved back before the hit (so replaying the section re-fires it)
        if (fired.has(h.id) && t < h.atMs - 60) fired.delete(h.id);
      }
    };
    const rearm = () => fired.clear();  // seeking/rewind resets everything
    v.addEventListener("timeupdate", onTime);
    v.addEventListener("seeking", rearm);
    return () => {
      v.removeEventListener("timeupdate", onTime);
      v.removeEventListener("seeking", rearm);
      Object.values(players).forEach((a) => { a.pause(); a.src = ""; });
    };
  }, [sheet, sfxHits, sfxCatalog]);

  // Music sheet (Setup, opened before any render exists, and Fine-tune, after): the bundled
  // 40-track CC0 catalog (fetched once) + whatever the user uploaded themselves this session.
  // musicSelection is null = no background track (the "music" toggle default-off state).
  const [musicCatalog, setMusicCatalog] = useState<{ tracks: MusicTrack[]; categories: MusicCategory[]; duckingPresets: string[] }>({ tracks: [], categories: [], duckingPresets: [] });
  const [userMusicTracks, setUserMusicTracks] = useState<UserMusicTrack[]>([]);
  const [musicSelection, setMusicSelection] = useState<MusicSelection | null>(null);
  const [musicUploading, setMusicUploading] = useState(false);
  const musicFileRef = useRef<HTMLInputElement | null>(null);
  useEffect(() => { fetchMusicCatalog().then(setMusicCatalog).catch(() => {}); }, []);

  const onPickMusicFile = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0];
    e.target.value = "";
    if (!f) return;
    setMusicUploading(true);
    try {
      const track = await uploadMusicTrack(f);
      setUserMusicTracks((arr) => [...arr, track]);
      setMusicSelection({ trackId: track.key, gainDb: -12, ducking: "medium" });
      stopCatalogPreview();
      musicAudition.start(track.url, -12, "medium");
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setMusicUploading(false);
    }
  };

  // Tapping a track in the Music sheet is the ONLY step now — no separate "preview" button.
  // Tapping the already-selected track again turns it off; tapping any other track switches to
  // it immediately. Both branches call musicAudition.start/stop synchronously, right inside the
  // tap that's already a real user gesture, so video+music actually start playing with no extra
  // click required (see useMusicAudition's comment for why this couldn't just happen in an effect).
  const selectMusicTrack = useCallback((trackId: string, url: string) => {
    stopCatalogPreview();
    if (musicSelection?.trackId === trackId) {
      musicAudition.stop();
      setMusicSelection(null);
      return;
    }
    const gainDb = musicSelection?.gainDb ?? -12;
    const ducking = musicSelection?.ducking ?? "medium";
    setMusicSelection({ trackId, gainDb, ducking });
    musicAudition.start(url, gainDb, ducking);
  }, [musicSelection, musicAudition, stopCatalogPreview]);

  // Fine-tune's chat editor (post-render only — it operates on an already-rendered reel's
  // moments/toggles/sfx/music, all of which only exist once a first render has happened).
  const [chatMessages, setChatMessages] = useState<ChatMessage[]>([]);
  const [chatInput, setChatInput] = useState("");
  const [chatBusy, setChatBusy] = useState(false);
  const [chatPendingActions, setChatPendingActions] = useState<ChatAction[]>([]);
  const [chatApplying, setChatApplying] = useState(false);

  // Setup screen: the user's own uploaded B-roll (Setup upload widget), each with a one-line
  // description that gets sent to planBroll() so the LLM can match a clip to a moment instead
  // of a stock search. unusedUserClipKeys comes back from planBroll (nothing silently vanishes —
  // the review sheet tells the user which of their clips didn't get placed).
  const [userBroll, setUserBroll] = useState<UserBrollClip[]>([]);
  const [brollUploading, setBrollUploading] = useState(false);
  const [unusedUserClipKeys, setUnusedUserClipKeys] = useState<string[]>([]);
  const brollFileRef = useRef<HTMLInputElement | null>(null);

  // Ready screen: pull the REAL produced-file dimensions/duration (server already probes it)
  useEffect(() => {
    if (screen === "ready" && result?.outputName) {
      fetch(`/api/output-meta/${encodeURIComponent(result.outputName)}`)
        .then((r) => r.json())
        .then((m) => { if (m && !m.error) setReelMeta({ width: m.width, height: m.height, durationMs: m.durationMs }); })
        .catch(() => {});
    }
  }, [screen, result?.outputName]);

  // Ready + Fine-tune screens: what the auto-cut pass removed (silence + retakes) — powers the
  // ready-screen summary line and the Trimmed sheet's list. Empty/zeroed when nothing was cut,
  // so this is safe to always fetch rather than branch on whether removeSilences was even on.
  useEffect(() => {
    if ((screen === "ready" || screen === "finetune") && projectId) {
      fetchRetakes(projectId)
        .then((info) => {
          setRetakesInfo(info);
          // Snapshot of what is actually baked into the CURRENT render. Everything after this is a
          // local edit, so the Apply button can tell "changed" from "unchanged" by diffing against
          // this. Deriving it from retakesInfo itself does not work: a freshly marked cut is stored
          // with restored:false exactly like an already-rendered one, so it looked already-applied
          // and the button stayed on "No changes to apply" -- you could mark cuts and never apply.
          setBakedCutIds(info.retakes.filter((r) => !r.restored).map((r) => r.id));
        })
        .catch(() => {});
    }
  }, [screen, projectId, result?.outputName]);

  // Seed the Trimmed sheet's local edits fresh each time it OPENS (not on every retakesInfo
  // refetch while it's already open, which would clobber in-progress un-applied toggles).
  useEffect(() => {
    if (sheet === "trimmed" && retakesInfo) {
      setPendingKeepIds(retakesInfo.retakes.filter((r) => r.restored).map((r) => r.id));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- deliberately NOT keyed on retakesInfo, see above
  }, [sheet]);

  // Music sheet's live audition (Web Audio graph) is only meaningful while that sheet is open
  // — tear it down the moment the user navigates away, so reopening never inherits a stale
  // graph/stuck-playing element, and so nothing keeps playing silently in the background.
  // Same for the shared SFX/Music catalog browse-preview — closing either sheet stops it too.
  useEffect(() => {
    if (sheet !== "music") musicAudition.stop();
    stopCatalogPreview();
  }, [sheet, musicAudition, stopCatalogPreview]);
  // Live-update volume/ducking while auditioning, so the slider/preset buttons feel instant —
  // matches "make music low or high" being heard immediately, not after Apply & re-render.
  // (Switching TRACKS is handled synchronously inside selectMusicTrack itself, not here — it
  // needs to run inside the tap's own gesture, not react to a state change a render later.)
  useEffect(() => {
    if (!musicAudition.active || !musicSelection) return;
    musicAudition.applyVolume(musicSelection.gainDb, musicSelection.ducking);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- only re-run on the two fields that matter
  }, [musicSelection?.gainDb, musicSelection?.ducking]);

  useEffect(() => { retakesInfoRef.current = retakesInfo; }, [retakesInfo]);
  useEffect(() => { pendingKeepIdsRef.current = pendingKeepIds; }, [pendingKeepIds]);

  const setPhase = (key: string, status: Phase["status"]) =>
    setPhases((ps) => ps.map((p) => (p.key === key ? { ...p, status } : p)));

  const pickFile = (f: File) => {
    setFile(f);
    if (fileUrl) URL.revokeObjectURL(fileUrl);
    setFileUrl(URL.createObjectURL(f));
    setScreen("setup");
  };

  const onPickBrollFile = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0];
    e.target.value = "";
    if (!f) return;
    setBrollUploading(true);
    try {
      const clip = await uploadBrollClip(f, f.type.startsWith("image/") ? "image" : "video");
      setUserBroll((arr) => [...arr, clip]);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBrollUploading(false);
    }
  };

  const buildAccepted = useCallback(
    (t: Toggles): Accepted[] => buildAcceptedFrom(moments, sceneChoices, t, removedCardIds),
    [moments, sceneChoices, removedCardIds],
  );

  // Shared render tail — used both by the one-tap fast path (no review needed) and by
  // confirmBrollReview() below (after the user reviews/reassigns/retimes B-roll placements).
  // Owns its own error handling since the review path calls it OUTSIDE run()'s try/catch.
  // `sfx` is passed in rather than read from state: run() plans the auto hits and calls this in the
  // same tick, so the setSfxHits() above has not landed yet and the closure would still see []. That
  // would have made the Sound-effects toggle silently do nothing on the FIRST render -- the exact
  // failure the toggle is supposed to avoid.
  const finishRender = useCallback(async (pid: string, mo: Moment[], choices: Record<string, SceneChoice>, zs: Zoom[],
                                          sfx?: PlacedSfx[]) => {
    setPhase("render", "run");
    setDetail("Compositing…");
    try {
      const accepted = buildAcceptedFrom(mo, choices, toggles, removedCardIds);
      // A selected track IS the on/off state now — there's no separate "music enabled" switch
      // anymore (that used to live on Setup's toggle, which no longer exists), so gating on
      // musicSelection directly is correct, not a stale toggles.music that nothing sets.
      const hits = sfx ?? sfxHits;
      const hasOverlays = accepted.length > 0 || (toggles.zoom && zs.length > 0) || hits.length > 0 || !!musicSelection;
      const captionsOff = !toggles.captions && hasOverlays;
      const res = await render({ project: pid, accepted, zooms: toggles.zoom ? zs : [], captionsOff, caption: toCaptionSel(caption, reg), editedText: {}, smoothTransitions: toggles.smoothTransitions, sfxHits: hits, music: musicSelection, onEvt: (e) => setDetail(friendly(e)) });
      setPhase("render", "done");
      setResult(res);
      setScreen("ready");
    } catch (e) {
      setError((e as Error).message);
    }
  }, [toggles, caption, reg, sfxHits, musicSelection, removedCardIds]);

  // ---- the one-tap run: ingest -> auto-plan/accept overlays -> bake --------------------
  const run = useCallback(async () => {
    setError(null);
    setDetail("");
    setScreen("processing");
    const wantBroll = toggles.broll,
      wantCards = toggles.cards,
      wantZoom = toggles.zoom;
    const plan: Phase[] = [{ key: "prepare", label: "Preparing your clip", status: "run" }];
    if (wantBroll || wantCards || wantZoom) plan.push({ key: "plan", label: "Planning moments", status: "pending" });
    if (wantBroll) plan.push({ key: "fetch", label: "Finding B-roll", status: "pending" });
    plan.push({ key: "render", label: "Rendering your reel", status: "pending" });
    setPhases(plan);
    const has = (k: string) => plan.some((p) => p.key === k);

    try {
      const pid = await ingest(file!, toggles, (e) => setDetail(friendly(e)));
      setProjectId(pid);
      setPhase("prepare", "done");
      try {
        const proj = await fetch(`/api/project?project=${encodeURIComponent(pid)}`).then((r) => r.json());
        setWords((proj.words ?? []).map((w: { id: string; text: string }) => ({ id: w.id, text: w.text })));
      } catch {
        /* words only needed for caption editing */
      }

      let mo: Moment[] = [];
      let zs: Zoom[] = [];
      if (has("plan")) setPhase("plan", "run");
      if (wantBroll || wantCards) {
        setDetail("Picking cutaway moments…");
        const planned = await planBroll(pid, userBroll);
        mo = planned.moments;
        setUnusedUserClipKeys(planned.unusedUserClipKeys);
      }
      if (wantZoom) {
        setDetail("Finding emphasis beats…");
        zs = await planZooms(pid);
      }
      if (has("plan")) setPhase("plan", "done");

      const choices: Record<string, SceneChoice> = {};
      if (wantBroll) {
        setPhase("fetch", "run");
        for (const m of mo.filter((x) => x.type === "scene")) {
          // A moment the planner matched to one of the user's own clips skips stock search
          // entirely — the clip is already uploaded+cached, so this is a free/instant assignment.
          const uc = m.assignedUserClipKey ? userBroll.find((c) => c.key === m.assignedUserClipKey) : undefined;
          if (uc) {
            choices[m.momentId] = {
              candidates: [{ id: uc.key, source: "You", kind: uc.kind, thumbUrl: "", mediaUrl: uc.url, width: 0, height: 0 }],
              idx: 0, cachedUrl: uc.url, removed: false, sourceStartMs: 0,
              sourceDurationMs: uc.durationMs, userClipDescription: uc.description,
            };
            continue;
          }
          setDetail(`Finding clips: “${m.primaryQuery}”`);
          try {
            const cands = await fetchCandidates(m.primaryQuery, m.spanEndMs - m.spanStartMs);
            if (!cands.length) continue;
            const url = await cacheMedia(cands[0].mediaUrl, cands[0].kind);
            choices[m.momentId] = { candidates: cands, idx: 0, cachedUrl: url, removed: false };
          } catch {
            /* one failed fetch shouldn't kill the run */
          }
        }
        setPhase("fetch", "done");
      }
      setMoments(mo);
      setSceneChoices(choices);
      setZooms(zs);

      // Plan the accents from what was just planned above. Placed BEFORE the render so the first
      // reel already has them, exactly like zooms and B-roll -- an SFX toggle that only took effect
      // on a later re-render would read as broken.
      let autoSfx: PlacedSfx[] = [];
      if (toggles.sfxAuto) {
        const durMs = mo.length ? Math.max(...mo.map((m) => m.spanEndMs)) + 4000 : 0;
        autoSfx = planSfx(mo, choices, zs, [], durMs);
        if (autoSfx.length) setSfxHits(autoSfx);
      }

      // Go straight to the render -- no pre-render B-roll review step. B-roll (swap / remove /
      // retime) is fully editable afterward in Fine-tune, which re-renders on demand, so gating
      // the FIRST render behind a separate review screen was redundant friction. The auto-placed
      // B-roll is shown in Fine-tune and can be adjusted there if needed.
      await finishRender(pid, mo, choices, zs, autoSfx);
    } catch (e) {
      setError((e as Error).message);
    }
  }, [file, toggles, caption, reg, userBroll, finishRender]);

  // re-bake from Fine-tune (reuses the ingested base)
  const reRender = useCallback(async (baseOverride?: string, phasePrefix?: Phase[]) => {
    setRebaking(true);
    setError(null);
    // Show the loading screen for the whole re-bake, exactly like the first render, so the
    // user isn't dropped back onto the ready page showing the OLD reel while the new one is
    // still rendering in the background (that made re-applies of music/look look like they
    // "went to ready before finishing"). We only return to ready once the render is done.
    setDetail("Compositing…");
    setPhases([...(phasePrefix ?? []), { key: "render", label: "Rendering your reel", status: "run" }]);
    setScreen("processing");
    try {
      const accepted = buildAccepted(toggles);
      const hasOverlays = accepted.length > 0 || (toggles.zoom && zooms.length > 0) || sfxHits.length > 0 || !!musicSelection;
      const captionsOff = !toggles.captions && hasOverlays;
      const res = await render({ project: projectId, accepted, zooms: toggles.zoom ? zooms : [], captionsOff, caption: toCaptionSel(caption, reg), editedText, smoothTransitions: toggles.smoothTransitions, sfxHits, music: musicSelection, baseOverride, onEvt: (e) => setDetail(friendly(e)) });
      setPhase("render", "done");
      setResult(res);
      setScreen("ready");
    } catch (e) {
      setError((e as Error).message);  // stays on the processing screen and shows the error (same as run())
    } finally {
      setRebaking(false);
    }
  }, [buildAccepted, toggles, zooms, editedText, caption, reg, projectId, sfxHits, musicSelection]);

  // Fine-tune "Look" sheet's Apply action: re-grade the cached pre-grade checkpoint (fast — no
  // re-transcribe), then re-bake overlays on top of the newly-graded base via the SAME
  // reRender() Fine-tune's other edits already use.
  const applyLook = useCallback(async (look: ColorGradeLook, intensity: number) => {
    setRegrading(true);
    setError(null);
    // Close the sheet and switch to the loading screen up front, so the grade+render runs on
    // a visible "working…" screen instead of behind the stale ready page. The grade is its own
    // step ahead of the render; reRender() below keeps us on the loading screen until done.
    setSheet(null);
    setDetail("Applying look…");
    const gradePhase: Phase[] = [{ key: "grade", label: "Applying look", status: "run" }];
    setPhases([...gradePhase, { key: "render", label: "Rendering your reel", status: "pending" }]);
    setScreen("processing");
    try {
      const { regradedBase } = await applyColorGrade({ project: projectId, look, intensity, onEvt: (e) => setDetail(friendly(e)) });
      setToggles((t) => ({ ...t, colorGradeLook: look, colorGradeIntensity: intensity }));
      setPhase("grade", "done");
      // Bake directly on the exact regraded file this call produced, rather than trusting the
      // server to have swapped the project's base pointer in time (that ordering was racy and
      // intermittently rendered the reel on the old, ungraded base). Pass the completed grade
      // step so the loading screen shows grade ✓ -> render running.
      await reRender(regradedBase, [{ key: "grade", label: "Applying look", status: "done" }]);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setRegrading(false);
    }
  }, [projectId, reRender]);

  // Every card edit is a moment edit -- no separate override map to drift out of sync with the
  // moments the render actually reads.
  const patchCard = (momentId: string, patch: Partial<BrollCard>) =>
    setMoments((ms) => ms.map((m) => (m.momentId === momentId && m.card
      ? { ...m, card: { ...m.card, ...patch } } : m)));
  const retimeCard = (momentId: string, startMs: number) =>
    setMoments((ms) => ms.map((m) => (m.momentId === momentId
      ? { ...m, spanStartMs: Math.max(0, startMs), spanEndMs: Math.max(0, startMs) + (m.spanEndMs - m.spanStartMs) } : m)));
  const setCardDuration = (momentId: string, durMs: number) =>
    setMoments((ms) => ms.map((m) => (m.momentId === momentId
      ? { ...m, spanEndMs: m.spanStartMs + Math.max(1200, durMs) } : m)));
  // Adding is as cheap as removing: the render takes an arbitrary list of card items, nothing
  // requires one to have come from the planner.
  const addCardAt = (atMs: number) => {
    const at = Math.max(0, Math.round(atMs));
    const id = `usercard-${crypto.randomUUID().slice(0, 8)}`;
    setSelCard(id);
    setMoments((ms) => [...ms, {
      momentId: id, type: "text_or_stat", spanStartMs: at, spanEndMs: at + 3000,
      primaryQuery: "", fallbackQueries: [], transcriptPhrase: "",
      card: { cardType: "phrase", headline: "Your text here", value: null, items: null, style: "ink" },
    }]);
  };

  // Plays a zoom on the REAL rendered reel with a CSS transform that reproduces the renderer's own
  // crop, rather than approximating it. This matters: the obvious version -- transform-origin at the
  // anchor, then scale -- is only correct for a CENTRED anchor. zoompan CLAMPS its crop window to
  // the frame edge, and transform-origin does not, so for "On her" the two disagreed by up to 10% of
  // the frame. Verified numerically against _zoom_xy_expr's formula: origin-based was wrong on 9 of
  // 12 anchor/scale combinations. So compute the same clamped window the renderer computes, and
  // express it as scale + translate from the top-left.
  //
  // The one thing that stays approximate is WHERE the face is: it's detected server-side at render
  // time, so the client can only assume the upper third a seated talking head occupies. Said plainly
  // in the sheet rather than left to be discovered.
  const FACE_ANCHOR_Y = 0.35;
  const cropWindow = (frac: number, z: number) => {
    const w = 1 / z;
    return Math.min(Math.max(frac - w / 2, 0), 1 - w);      // identical to _zoom_xy_expr
  };
  const previewZoom = (z: Zoom) => {
    const v = zoomVidRef.current;
    if (!v) return;
    if (zoomPvTimer.current) window.clearTimeout(zoomPvTimer.current);
    const fy = (z.anchor ?? "face") === "center" ? 0.5 : FACE_ANCHOR_Y;
    const ms = z.style === "quick_punch" ? 280 : 600;
    const target = { scale: z.targetScale, ms,
                     left: cropWindow(0.5, z.targetScale), top: cropWindow(fy, z.targetScale) };
    v.currentTime = Math.max(0, z.startMs / 1000);
    setZoomPv({ scale: 1, left: 0, top: 0, ms: 0 });        // at rest, so the ramp is visible
    void v.play().catch(() => {});
    requestAnimationFrame(() => setZoomPv(target));
    zoomPvTimer.current = window.setTimeout(() => {
      v.pause();
      setZoomPv(null);
    }, Math.min(4500, (z.endMs - z.startMs) + 900));
  };

  const patchZoom = (i: number, patch: Partial<Zoom>) =>
    setZooms((zs) => zs.map((z, j) => (j === i ? { ...z, ...patch } : z)));
  // Same as cards: the render takes an arbitrary list, so adding one is no harder than removing one.
  const addZoomAt = (atMs: number) => {
    const fresh: Zoom = { startMs: Math.max(0, Math.round(atMs)), endMs: Math.max(0, Math.round(atMs)) + 2200,
                          style: "slow_push", targetScale: ZOOM_STRENGTH.medium, anchor: "face" };
    setZooms((zs) => {
      const next = [...zs, fresh].sort((a, b) => a.startMs - b.startMs);
      setSelZoom(next.indexOf(fresh));   // select what was just added, so its controls are right there
      return next;
    });
  };

  const swapClip = async (momentId: string) => {
    const sc = sceneChoices[momentId];
    if (!sc || sc.candidates.length < 2) return;
    const idx = (sc.idx + 1) % sc.candidates.length;
    const url = await cacheMedia(sc.candidates[idx].mediaUrl, sc.candidates[idx].kind);
    // Spread the LIVE s[momentId], not the pre-await `sc` closure — otherwise this silently
    // clobbers any other change (retime, remove) applied to the same moment while this await
    // was in flight, e.g. a chat-confirmed batch that retimes then swaps the same clip.
    setSceneChoices((s) => (s[momentId] ? { ...s, [momentId]: { ...s[momentId], idx, cachedUrl: url, removed: false } } : s));
  };
  const toggleRemoveClip = (momentId: string) =>
    setSceneChoices((s) => ({ ...s, [momentId]: { ...s[momentId], removed: !s[momentId].removed } }));
  const retimeClip = (momentId: string, sourceStartMs: number) =>
    setSceneChoices((s) => ({ ...s, [momentId]: { ...s[momentId], sourceStartMs } }));

  // Trimmed sheet: apply a new set of restored (un-cut) retake ids. Mirrors applyLook's shape
  // exactly — close the sheet, show the loading screen through both the restore step and the
  // re-render, bake on the exact file this call produced (not a possibly-stale project pointer).
  const applyRetakeRestore = useCallback(async (keepIds: string[]) => {
    setRestoringRetakes(true);
    setError(null);
    setSheet(null);
    setDetail("Restoring…");
    setPhases([{ key: "restore", label: "Restoring retake(s)", status: "run" }, { key: "render", label: "Rendering your reel", status: "pending" }]);
    setScreen("processing");
    try {
      const { restoredBase } = await restoreRetakes({ project: projectId, keepIds, onEvt: (e) => setDetail(friendly(e)) });
      setRetakesInfo((info) => (info ? { ...info, retakes: info.retakes.map((r) => ({ ...r, restored: keepIds.includes(r.id) })) } : info));
      await reRender(restoredBase, [{ key: "restore", label: "Restoring retake(s)", status: "done" }]);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setRestoringRetakes(false);
    }
  }, [projectId, reRender]);

  // Trim timeline tap handler — the whole tap-tap-to-mark interaction lives here:
  //   tap a red region  -> remove it (manual cuts deleted outright; AI cuts flipped to restored)
  //   tap with no open marker  -> drop a START marker
  //   tap with an open marker  -> close the region, commit it (server snaps to word boundaries)
  // Every tap also seeks the preview so you can see exactly where the marker landed.
  const onTrimTimelineTap = useCallback(async (ms: number, hitRegionId: string | null) => {
    if (previewRef.current) previewRef.current.currentTime = ms / 1000;
    setPreviewTimeMs(ms);

    if (hitRegionId) {
      setPendingStartMs(null);
      const hit = retakesInfo?.retakes.find((r) => r.id === hitRegionId);
      if (!hit) return;
      if (hit.confidence === "manual") {
        try {
          snapshotTrim();
          await unmarkRetake(projectId, hit.id);
          setRetakesInfo((info) => (info ? { ...info, retakes: info.retakes.filter((r) => r.id !== hit.id) } : info));
        } catch (e) {
          setError((e as Error).message);
        }
      } else {
        snapshotTrim();
        setPendingKeepIds((ids) => (ids.includes(hit.id) ? ids : [...ids, hit.id])); // restore on apply
      }
      return;
    }

    if (pendingStartMs == null) {
      setPendingStartMs(ms);
      return;
    }
    const a = Math.min(pendingStartMs, ms);
    const b = Math.max(pendingStartMs, ms);
    setPendingStartMs(null);
    if (b - a < 60) return; // a double-tap in ~the same spot isn't a range
    snapshotTrim();
    setMarking(true);
    setError(null);
    try {
      // In "after" mode the tap is on the ALREADY-CUT video's clock, so send it as
      // outStart/outEnd and let the server invert the cut mapping back to checkpoint time.
      const res = await markRetake(
        projectId,
        trimMode === "after" ? { outStartMs: Math.round(a), outEndMs: Math.round(b) } : { startMs: Math.round(a), endMs: Math.round(b) },
      );
      setRetakesInfo((info) =>
        info
          ? {
              ...info,
              retakes: [...info.retakes, {
                id: res.id, text: res.text || "(no speech in this range)",
                reason: "Trimmed by you", confidence: "manual",
                startMs: res.startMs, endMs: res.endMs,
                cutStartMs: res.startMs, cutEndMs: res.endMs,
                restored: false,
              }],
            }
          : info,
      );
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setMarking(false);
    }
  }, [retakesInfo, pendingStartMs, projectId, trimMode]);

  // Called BEFORE any trim mutation, so the stack holds the state to go back to.
  const snapshotTrim = useCallback(() => {
    setTrimUndo((h) => {
      const cur = retakesInfoRef.current;
      if (!cur) return h;
      return [...h.slice(-49), { retakes: cur.retakes, keepIds: pendingKeepIdsRef.current }];
    });
    setTrimRedo([]);   // a new action invalidates the redo branch, as everywhere else
  }, []);

  // Make the SERVER ledger match a restored snapshot. Only manual cuts need this: an AI cut is
  // never deleted from the ledger (it is only flagged restored via keepIds), whereas a manual cut is
  // a real ledger row that mark/unmark creates and destroys. Matching is by RANGE, not id, because
  // re-marking a range necessarily produces a new id -- the range is the stable identity.
  const reconcileTrim = useCallback(async (target: { retakes: RetakeCut[]; keepIds: string[] }) => {
    setTrimSyncing(true);
    try {
      const rangeOf = (r: RetakeCut) => {
        const a = r.cutStartMs ?? r.startMs, b = r.cutEndMs ?? r.endMs;
        return a == null || b == null ? null : { a: Math.round(a), b: Math.round(b) };
      };
      const key = (r: RetakeCut) => { const g = rangeOf(r); return g ? `${g.a}:${g.b}` : ""; };
      const cur = retakesInfoRef.current;
      const ranged = (r: RetakeCut) => r.confidence === "manual" && rangeOf(r) != null;
      const manualNow = (cur?.retakes ?? []).filter(ranged);
      const manualWant = target.retakes.filter(ranged);
      const wantKeys = new Set(manualWant.map(key));
      const nowKeys = new Set(manualNow.map(key));
      for (const r of manualNow) {
        if (!wantKeys.has(key(r))) await unmarkRetake(projectId, r.id).catch(() => {});
      }
      for (const r of manualWant) {
        const g = rangeOf(r);
        if (g && !nowKeys.has(key(r))) {
          await markRetake(projectId, { startMs: g.a, endMs: g.b }).catch(() => {});
        }
      }
      setPendingKeepIds(target.keepIds);
      // Refetch rather than trusting the local copy: re-marking mints new ids server-side, so the
      // ledger is the only thing that knows the truth after a reconcile.
      const fresh = await fetchRetakes(projectId);
      setRetakesInfo(fresh);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setTrimSyncing(false);
    }
  }, [projectId]);

  const undoTrim = useCallback(async () => {
    const prev = trimUndo[trimUndo.length - 1];
    const cur = retakesInfoRef.current;
    if (!prev || !cur) return;
    setTrimUndo((h) => h.slice(0, -1));
    setTrimRedo((h) => [...h, { retakes: cur.retakes, keepIds: pendingKeepIdsRef.current }]);
    await reconcileTrim(prev);
  }, [trimUndo, reconcileTrim]);

  const redoTrim = useCallback(async () => {
    const next = trimRedo[trimRedo.length - 1];
    const cur = retakesInfoRef.current;
    if (!next || !cur) return;
    setTrimRedo((h) => h.slice(0, -1));
    setTrimUndo((h) => [...h, { retakes: cur.retakes, keepIds: pendingKeepIdsRef.current }]);
    await reconcileTrim(next);
  }, [trimRedo, reconcileTrim]);

  // Commit an explicit checkpoint-time range as a cut. Shared by the whole-line and word-range
  // paths below, so both go through the same server snapping/ledger write.
  const commitCut = useCallback(async (startMs: number, endMs: number) => {
    snapshotTrim();
    setMarking(true);
    setError(null);
    setSnapWarning(null);
    try {
      const res = await markRetake(projectId, { startMs: Math.round(startMs), endMs: Math.round(endMs) });
      if (!res.snapped) {
        setSnapWarning("That range didn't line up with any words — check the preview before applying.");
      }
      setRetakesInfo((info) =>
        info
          ? {
              ...info,
              retakes: [...info.retakes, {
                id: res.id, text: res.text || "(no speech in this range)",
                reason: "Trimmed by you", confidence: "manual",
                startMs: res.startMs, endMs: res.endMs,
                cutStartMs: res.startMs, cutEndMs: res.endMs, restored: false,
              }],
            }
          : info,
      );
      setWordSel(null);
      setExpandedSeg(null);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setMarking(false);
    }
  }, [projectId]);

  // Tap a word chip: first tap sets the range start, second sets the end (tapping the same word
  // twice cuts just that word). Mirrors the timeline's two-tap model so there's one thing to
  // learn, but each target is a list-row-sized chip instead of ~1.35pt of bar per second.
  const onWordTap = useCallback((seg: number, idx: number) => {
    setWordSel((cur) => {
      if (!cur || cur.seg !== seg) return { seg, from: idx, to: idx };
      return { seg, from: Math.min(cur.from, idx), to: Math.max(cur.to, idx) };
    });
    const w = retakesInfo?.segments.find((s) => s.segIndex === seg)?.words[idx];
    if (w && previewRef.current) {
      previewRef.current.currentTime = w.startMs / 1000;
      setPreviewTimeMs(w.startMs);
    }
  }, [retakesInfo]);

  const addSfxHit = (soundId: string) => {
    const atMs = Math.round((sfxPlayerRef.current?.currentTime ?? 0) * 1000);
    setSfxHits((arr) => [...arr, { id: crypto.randomUUID(), soundId, atMs, gainDb: 0 }]);
  };
  const removeSfxHit = (id: string) => setSfxHits((arr) => arr.filter((h) => h.id !== id));

  // Pre-render review sheet's confirm (also fires on backdrop/X close — there's no meaningful
  // "cancel" here short of aborting the whole render, so closing the sheet always proceeds with
  // whatever reassign/remove/retime choices are currently selected).
  const confirmBrollReview = useCallback(() => {
    void finishRender(projectId, moments, sceneChoices, zooms);
  }, [finishRender, projectId, moments, sceneChoices, zooms]);

  // A compact snapshot of "everything the chat editor is allowed to reference/change" — sent
  // fresh with every message so chat_agent.py's prompt only ever sees real ids/enums, never
  // stale ones (see that file's HARD RULES: it must not invent an id not present here).
  const buildChatState = useCallback(() => {
    const momentsState = moments
      .filter((m) => m.type === "scene" && sceneChoices[m.momentId])
      .map((m) => {
        const sc = sceneChoices[m.momentId];
        const windowMs = m.spanEndMs - m.spanStartMs;
        const canRetime = sc.candidates[sc.idx].kind === "video" && (sc.sourceDurationMs ?? 0) > windowMs;
        return {
          momentId: m.momentId, spanStartMs: m.spanStartMs, spanEndMs: m.spanEndMs,
          description: sc.userClipDescription || m.primaryQuery, removed: sc.removed,
          canRetime, maxRetimeMs: canRetime ? Math.max(0, (sc.sourceDurationMs ?? 0) - windowMs) : 0,
        };
      });
    return {
      moments: momentsState,
      // music: !!musicSelection, NOT toggles.music — there's no separate on/off flag anymore,
      // a selected track IS "on" (see the music-selection bug fix in reRender/finishRender).
      toggles: { captions: toggles.captions, broll: toggles.broll, cards: toggles.cards, zoom: toggles.zoom, smoothTransitions: toggles.smoothTransitions, music: !!musicSelection },
      captionStyleId: caption?.styleId ?? null,
      availableCaptionStyles: (reg?.styles ?? []).filter((s) => s.available).map((s) => s.id),
      colorGradeLook: toggles.colorGradeLook,
      colorGradeIntensity: toggles.colorGradeIntensity,
      availableLooks: COLOR_GRADE_LOOKS,
      sfxHits: sfxHits.map((h) => ({ id: h.id, soundId: h.soundId, atMs: h.atMs })),
      availableSfx: sfxCatalog.sounds.map((s) => s.id),
      music: musicSelection,
      availableMusic: [...musicCatalog.tracks.map((t) => t.id), ...userMusicTracks.map((t) => t.key)],
    };
  }, [moments, sceneChoices, toggles, caption, reg, sfxHits, sfxCatalog, musicSelection, musicCatalog, userMusicTracks]);

  const sendChatMessage = useCallback(async () => {
    const text = chatInput.trim();
    if (!text || chatBusy) return;
    setChatInput("");
    setChatMessages((m) => [...m, { role: "user", content: text }]);
    setChatBusy(true);
    try {
      const state = buildChatState();
      const { reply, actions } = await chatEdit(text, chatMessages, state);
      setChatMessages((m) => [...m, { role: "assistant", content: reply }]);
      setChatPendingActions(actions);
    } catch (e) {
      setChatMessages((m) => [...m, { role: "assistant", content: `Sorry, something went wrong: ${(e as Error).message}` }]);
    } finally {
      setChatBusy(false);
    }
  }, [chatInput, chatBusy, chatMessages, buildChatState]);

  // Apply every confirmed action via the SAME client functions the manual UI already uses (swap/
  // retime/toggle/sfx/music setters) — the chatbot never re-implements this logic server-side, it
  // only decides WHICH of these already-tested actions to call. set_color_grade_look is special:
  // it's the one action that triggers a real regrade job, so it's applied last via applyLook
  // (which already re-renders internally) instead of a separate reRender() call.
  const applyChatActions = useCallback(async () => {
    const actions = chatPendingActions;
    setChatPendingActions([]);
    setChatApplying(true);
    try {
      let colorGrade: { look: ColorGradeLook; intensity: number } | null = null;
      for (const a of actions) {
        switch (a.tool) {
          case "swap_broll_clip":
            await swapClip(a.momentId);
            break;
          case "set_broll_removed":
            setSceneChoices((s) => (s[a.momentId] ? { ...s, [a.momentId]: { ...s[a.momentId], removed: a.removed } } : s));
            break;
          case "retime_broll_clip":
            retimeClip(a.momentId, a.sourceStartMs);
            break;
          case "set_caption_style": {
            const style = reg?.styles.find((s) => s.id === a.styleId);
            if (style) setCaption({ styleId: style.id, sizePx: style.defaultSizePx, bottomPercent: style.defaultBottomPercent });
            break;
          }
          case "set_toggle":
            // "music" has no real toggle anymore — a selected track IS on/off (see buildChatState's
            // comment). Honor "turn music off" as "clear the selection," since that's the only
            // thing that actually stops music from rendering; anything else here is unaffected.
            if (a.key === "music" && !a.on) setMusicSelection(null);
            else setToggles((t) => ({ ...t, [a.key]: a.on }));
            break;
          case "set_color_grade_look":
            colorGrade = { look: a.look, intensity: a.intensity };
            break;
          case "add_sfx":
            setSfxHits((arr) => [...arr, { id: crypto.randomUUID(), soundId: a.soundId, atMs: a.atMs, gainDb: 0 }]);
            break;
          case "remove_sfx":
            removeSfxHit(a.id);
            break;
          case "set_music":
            setMusicSelection({ trackId: a.trackId, gainDb: a.gainDb, ducking: a.ducking });
            break;
        }
      }
      if (colorGrade) {
        await applyLook(colorGrade.look, colorGrade.intensity);
      } else {
        await reRender();
      }
    } catch (e) {
      // swapClip's cacheMedia() call is the one action here that can genuinely throw (network
      // fetch) — surface it in the chat transcript, the same place sendChatMessage's own errors
      // show up, rather than letting it vanish as an unhandled rejection.
      setChatMessages((m) => [...m, { role: "assistant", content: `Sorry, applying those changes failed: ${(e as Error).message}` }]);
    } finally {
      setChatApplying(false);
    }
  }, [chatPendingActions, reg, applyLook, reRender]);

  const startOver = (to: Screen = "create") => {
    if (fileUrl) URL.revokeObjectURL(fileUrl);
    setFile(null);
    setFileUrl("");
    setResult(null);
    setProjectId("");
    setReelMeta(null);
    setMoments([]);
    setSceneChoices({});
    setZooms([]);
    setRemovedCardIds([]);
    setWords([]);
    setEditedText({});
    setUserBroll([]);
    setUnusedUserClipKeys([]);
    setSfxHits([]);
    setUserMusicTracks([]);
    setMusicSelection(null);
    setChatMessages([]);
    setChatPendingActions([]);
    setError(null);
    setScreen(to);
  };

  const qcFails = (result?.qc ?? []).filter((c) => !c.pass).length; // real QC status for the Ready line
  // "12 pauses, 2 retakes trimmed · 4m12s -> 2m18s" — shown once, right after the first render,
  // so an automatic bulk edit is never silent (see the Trimmed sheet for the reviewable detail).
  const trimmedSummary = (() => {
    const info = retakesInfo;
    if (!info) return null;
    const activeRetakes = info.retakes.filter((r) => !r.restored).length;
    const parts: string[] = [];
    if (info.silenceTrimmedMs > 500) parts.push(`${fmtMs(info.silenceTrimmedMs)} of pauses`);
    if (activeRetakes > 0) parts.push(`${activeRetakes} retake${activeRetakes > 1 ? "s" : ""}`);
    if (!parts.length) return null;
    const dur = info.originalDurationMs && info.currentDurationMs
      ? ` · ${fmtMs(info.originalDurationMs)} → ${fmtMs(info.currentDurationMs)}` : "";
    return `${parts.join(", ")} trimmed${dur}`;
  })();
  // Display label for the current music pick — could be a catalog track or one of this session's uploads.
  const musicLabel = musicSelection
    ? (musicCatalog.tracks.find((t) => t.id === musicSelection.trackId)?.label
        ?? userMusicTracks.find((t) => t.key === musicSelection.trackId)?.label
        ?? null)
    : null;

  // ============================ RENDER ============================
  return (
    <div className="su-phone">
      {screen === "create" && (
        <CreateScreen hover={hover} setHover={setHover} fileRef={fileRef} onPick={pickFile} hasFile={!!file} />
      )}

      {screen === "setup" && (
        <>
          <div className="su-pattern su-pad" style={{ paddingTop: 20, paddingBottom: 14, display: "flex", alignItems: "center", gap: 12 }}>
            <button className="su-iconbtn" onClick={() => setScreen("create")} aria-label="Back"><ArrowLeft size={18} /></button>
            <Wordmark />
          </div>
          <div className="su-pad" style={{ paddingTop: 0, paddingBottom: 6 }}>
            <h1 className="su-h1">What should we apply?</h1>
            <p className="su-sub" style={{ marginTop: 6 }}>Everything's on. Tap any to turn it off.</p>
          </div>
          <div className="su-scroll su-pad su-stack" style={{ paddingTop: 8 }}>
            <VideoPlayer src={fileUrl} muted compact />
            <div className="su-stack" style={{ marginTop: 4, gap: 9 }}>
              {CATALOG.map((item) => (
                <ToggleRow key={item.key} item={item} on={toggles[item.key]} onToggle={() => setToggles((t) => ({ ...t, [item.key]: !t[item.key] }))} />
              ))}
            </div>
            {toggles.captions && reg && (
              <div className="su-card su-stack" style={{ gap: 10 }}>
                <div className="su-label">Caption style</div>
                <CaptionStyleControls reg={reg} value={caption} onChange={setCaption} />
              </div>
            )}
            {toggles.broll && (
              <div className="su-card su-stack" style={{ gap: 10 }}>
                <div className="su-label">Your own B-roll (optional)</div>
                <p className="su-sub" style={{ margin: 0 }}>Upload clips and describe what's in each one — we'll match them to the right moment automatically.</p>
                {userBroll.map((c, i) => (
                  <div key={c.key} style={{ display: "flex", gap: 8, alignItems: "center" }}>
                    {c.kind === "video" ? (
                      <video src={c.url} muted style={{ width: 40, height: 71, objectFit: "cover", borderRadius: 6, background: "#000", flexShrink: 0 }} />
                    ) : (
                      <img src={c.url} alt="" style={{ width: 40, height: 71, objectFit: "cover", borderRadius: 6, flexShrink: 0 }} />
                    )}
                    <input
                      value={c.description}
                      onChange={(e) => setUserBroll((arr) => arr.map((x, j) => (j === i ? { ...x, description: e.target.value } : x)))}
                      placeholder={`What's in this clip? e.g. "me typing on a laptop"`}
                      style={{
                        flex: 1, minWidth: 0, border: "1px solid var(--su-line-strong)", background: "#fff",
                        borderRadius: 8, padding: "8px 10px", fontSize: 13, color: "var(--su-ink-900)",
                      }}
                    />
                    <button className="su-iconbtn" onClick={() => setUserBroll((arr) => arr.filter((_, j) => j !== i))} aria-label="Remove clip"><Trash2 size={15} /></button>
                  </div>
                ))}
                <button className="su-btn ghost" disabled={brollUploading} onClick={() => brollFileRef.current?.click()}>
                  {brollUploading ? <Loader2 size={16} className="su-spin" style={{ border: 0 }} /> : <UploadCloud size={16} />}
                  {brollUploading ? "Uploading…" : "Add a clip"}
                </button>
                <input ref={brollFileRef} type="file" accept="video/*,image/*" style={{ display: "none" }} onChange={(e) => void onPickBrollFile(e)} />
              </div>
            )}
            <div className="su-info">Always applied · transcribe · 1080×1920 · −14 LUFS</div>
            <p className="su-sub" style={{ margin: 0 }}>Music and colour look come next — right after your first render, previewed live on your actual video.</p>
          </div>
          <div className="su-footer">
            <button className="su-btn go" onClick={() => void run()}>
              <Zap size={18} /> Start editing
            </button>
          </div>
        </>
      )}

      {screen === "processing" && (
        <div className="su-pattern su-fill" style={{ padding: 26 }}>
          <div style={{ position: "absolute", top: 20, left: 20, zIndex: 2 }}><Wordmark /></div>
          <div className="su-center-block">
          {!error && (
            <div className="su-orb-wrap">
              <div className="su-orb" />
            </div>
          )}
          <h1 className="su-h1" style={{ marginBottom: 20, textAlign: "center" }}>{error ? "Something went wrong" : "Editing your reel…"}</h1>
          <div className="su-card">
            {phases.map((p) => (
              <div key={p.key} className={`su-step ${p.status}`}>
                <div className="dot">
                  {p.status === "done" ? <Check size={13} /> : p.status === "run" ? <Loader2 size={12} className="su-spin" style={{ border: 0 }} /> : ""}
                </div>
                <div className="lab">{p.label}</div>
              </div>
            ))}
            {!error && detail ? <p className="su-sub" style={{ marginTop: 10, paddingLeft: 4 }}>{detail}</p> : null}
            {error ? <p className="su-err" style={{ marginTop: 10 }}>{error}</p> : null}
          </div>
          {error ? (
            <div className="su-stack" style={{ marginTop: 16 }}>
              <button className="su-btn" onClick={() => void run()}><RotateCcw size={16} /> Try again</button>
              <button className="su-btn ghost" onClick={() => startOver()}>Start over</button>
            </div>
          ) : null}
          </div>
        </div>
      )}

      {screen === "ready" && result && (
        <>
          <div className="su-pattern su-pad" style={{ paddingTop: 20, paddingBottom: 6, display: "flex", alignItems: "center", justifyContent: "space-between" }}>
            <Wordmark />
            <button className="su-iconbtn" onClick={() => startOver("create")} aria-label="New reel"><Plus size={18} /></button>
          </div>
          <div className="su-fill" style={{ padding: "6px 20px" }}>
            <div className="su-center-block" style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 10 }}>
              <div style={{ textAlign: "center" }}>
                <h1 className="su-h1">Your reel is ready 🎉</h1>
                <div className="su-metaline" style={{ marginTop: 8 }}>
                  <Check size={14} color={qcFails ? "#C8163A" : "var(--su-ok)"} />
                  {qcFails ? `${qcFails} check${qcFails > 1 ? "s" : ""} flagged` : "Quality checked"}
                  <span className="dotsep" /> {reelMeta ? `${reelMeta.width}×${reelMeta.height}` : "1080×1920"}
                  {reelMeta ? <><span className="dotsep" /> {fmtMs(reelMeta.durationMs)}</> : null}
                  <span className="dotsep" /> −14 LUFS
                </div>
              </div>
              <VideoPlayer key={result.outputName} src={`/api/result/${result.outputName}`} big />
              {trimmedSummary ? (
                <button
                  className="su-sub"
                  style={{ background: "none", border: 0, padding: 0, textDecoration: "underline", cursor: "pointer" }}
                  onClick={() => { setScreen("finetune"); setSheet("trimmed"); }}
                >
                  {trimmedSummary}
                </button>
              ) : null}
            </div>
          </div>
          <div className="su-footer su-stack" style={{ gap: 10 }}>
            {/* Music & Look moved here from Setup — picked AFTER the first render, previewed
                live against the actual reel (mixed audio / applied filter), not chosen blind
                before anything exists to preview against. */}
            <div style={{ display: "flex", gap: 8 }}>
              <button className="su-action" onClick={() => setSheet("music")}>
                <Music2 size={15} /> {musicLabel ? "Music" : "Add music"}
              </button>
              <button className="su-action" onClick={() => setSheet("look")}>
                <Palette size={15} /> Look — {LOOK_LABELS[toggles.colorGradeLook]}
              </button>
            </div>
            <a href={`/api/result/${result.outputName}`} download={result.outputName} style={{ display: "block" }}>
              <button className="su-btn"><Download size={18} /> Download reel</button>
            </a>
            <div style={{ display: "flex", gap: 8 }}>
              <button className="su-action" onClick={() => setScreen("finetune")}><Pencil size={15} /> Fine-tune</button>
              <button className="su-action" onClick={() => setScreen("saved")}><Download size={15} /> Save</button>
              <button className="su-action" onClick={() => setScreen("saved")}><Share2 size={15} /> Share</button>
            </div>
          </div>
        </>
      )}

      {screen === "finetune" && (
        <>
          <div className="su-pattern su-pad" style={{ paddingTop: 20, paddingBottom: 8, display: "flex", alignItems: "center", gap: 12 }}>
            <button className="su-iconbtn" onClick={() => setScreen("ready")} aria-label="Back"><ArrowLeft size={18} /></button>
            <h2 className="su-h2">Fine-tune</h2>
          </div>
          <div className="su-scroll su-pad su-stack" style={{ paddingTop: 4 }}>
            <p className="su-sub">Toggle what's applied, or open a section to edit in detail. Your base is reused, so re-rendering is quick.</p>
            <div className="su-stack" style={{ gap: 9 }}>
              {CATALOG.filter((c) => OVERLAY_KEYS.includes(c.key)).map((item) => (
                <ToggleRow key={item.key} item={item} on={toggles[item.key]} onToggle={() => setToggles((t) => ({ ...t, [item.key]: !t[item.key] }))} />
              ))}
            </div>
            <div className="su-label" style={{ marginTop: 8 }}>Edit in detail</div>
            <button className="su-navrow" onClick={() => setSheet("broll")}>
              <div className="su-ico" style={{ background: "var(--su-plum)" }}><Shuffle size={17} /></div>
              <div className="txt" style={{ flex: 1 }}>
                <div className="title">B-roll clips</div>
                <div className="desc">Swap or remove — {moments.filter((m) => m.type === "scene" && sceneChoices[m.momentId]).length} clips</div>
              </div>
              <ChevronRight size={18} color="var(--su-ink-300)" />
            </button>
            {retakesInfo && (retakesInfo.retakes.length > 0 || retakesInfo.silenceTrimmedMs > 500) && (
              <button className="su-navrow" onClick={() => setSheet("trimmed")}>
                <div className="su-ico" style={{ background: "var(--su-forest)" }}><Scissors size={17} /></div>
                <div className="txt" style={{ flex: 1 }}>
                  <div className="title">Trimmed</div>
                  <div className="desc">
                    {retakesInfo.retakes.length > 0
                      ? `${retakesInfo.retakes.filter((r) => !r.restored).length} retake${retakesInfo.retakes.length === 1 ? "" : "s"} cut — review or restore`
                      : "Pauses trimmed automatically"}
                  </div>
                </div>
                <ChevronRight size={18} color="var(--su-ink-300)" />
              </button>
            )}
            <button className="su-navrow" onClick={() => setSheet("zooms")}>
              <div className="su-ico" style={{ background: "var(--su-periwinkle)" }}><ZoomIn size={17} /></div>
              <div className="txt" style={{ flex: 1 }}>
                <div className="title">Zooms</div>
                <div className="desc">
                  {(() => { const n = zooms.filter((z) => !z.off).length;
                    return n ? `Strength, speed, timing — ${n} punch-in${n === 1 ? "" : "s"}` : "Add a punch-in"; })()}
                </div>
              </div>
              <ChevronRight size={18} color="var(--su-ink-300)" />
            </button>
            <button className="su-navrow" onClick={() => setSheet("cards")}>
              <div className="su-ico" style={{ background: "var(--su-rust)" }}><LayoutTemplate size={17} /></div>
              <div className="txt" style={{ flex: 1 }}>
                <div className="title">Graphic cards</div>
                <div className="desc">
                  {(() => {
                    const n = moments.filter((m) => m.card && !removedCardIds.includes(m.momentId)).length;
                    return n ? `Edit text, style or timing — ${n} card${n === 1 ? "" : "s"}` : "Add a stat, quote or list";
                  })()}
                </div>
              </div>
              <ChevronRight size={18} color="var(--su-ink-300)" />
            </button>
            <button className="su-navrow" onClick={() => setSheet("captions")}>
              <div className="su-ico" style={{ background: "var(--su-amber)" }}><Captions size={17} /></div>
              <div className="txt" style={{ flex: 1 }}>
                <div className="title">Edit captions</div>
                <div className="desc">Style, size & fix words</div>
              </div>
              <ChevronRight size={18} color="var(--su-ink-300)" />
            </button>
            <button className="su-navrow" onClick={() => setSheet("look")}>
              <div className="su-ico" style={{ background: "var(--su-clay)" }}><Palette size={17} /></div>
              <div className="txt" style={{ flex: 1 }}>
                <div className="title">Look</div>
                <div className="desc">Preview &amp; change the colour grade — {LOOK_LABELS[toggles.colorGradeLook]}</div>
              </div>
              <ChevronRight size={18} color="var(--su-ink-300)" />
            </button>
            <button className="su-navrow" onClick={() => setSheet("sfx")}>
              <div className="su-ico" style={{ background: "var(--su-teal)" }}><Volume2 size={17} /></div>
              <div className="txt" style={{ flex: 1 }}>
                <div className="title">Sound effects</div>
                <div className="desc">{sfxHits.length ? `${sfxHits.length} placed` : "Add a whoosh, ding, or hit"}</div>
              </div>
              <ChevronRight size={18} color="var(--su-ink-300)" />
            </button>
            <button className="su-navrow" onClick={() => setSheet("music")}>
              <div className="su-ico" style={{ background: "var(--su-indigo)" }}><Music2 size={17} /></div>
              <div className="txt" style={{ flex: 1 }}>
                <div className="title">Music</div>
                <div className="desc">{musicLabel ? `Playing — ${musicLabel}` : "Add a background track"}</div>
              </div>
              <ChevronRight size={18} color="var(--su-ink-300)" />
            </button>
            <button className="su-navrow" onClick={() => setSheet("chat")}>
              <div className="su-ico" style={{ background: "var(--su-maroon)" }}><MessageCircle size={17} /></div>
              <div className="txt" style={{ flex: 1 }}>
                <div className="title">Ask AI to edit</div>
                <div className="desc">Tell it what to change, in plain English</div>
              </div>
              <ChevronRight size={18} color="var(--su-ink-300)" />
            </button>
            {error ? <p className="su-err">{error}</p> : null}
          </div>
          <div className="su-footer">
            <button className="su-btn dark" disabled={rebaking} onClick={() => void reRender()}>
              {rebaking ? <Loader2 size={16} className="su-spin" style={{ border: 0 }} /> : <RotateCcw size={16} />}
              {rebaking ? "Re-rendering…" : "Re-render"}
            </button>
          </div>
        </>
      )}

      {screen === "saved" && (
        <SavedScreen
          outputName={result?.outputName ?? ""}
          projectId={projectId}
          onCreateAnother={() => startOver("create")}
        />
      )}

      {/* ---- shared sheets (opened from Fine-tune) ---- */}
      {sheet === "broll" && (
        <Sheet title="B-roll clips" sub="Tap a cutaway on the bar to swap, remove or retime it." onClose={() => setSheet(null)}>
          {(() => {
            const scenes = moments.filter((m) => m.type === "scene" && sceneChoices[m.momentId]);
            const sel = scenes.find((x) => x.momentId === selBroll) ?? scenes[0] ?? null;
            return (
              <>
                <EditSurface
                  src={result ? `/api/result/${result.outputName}` : ""}
                  durationMs={reelMeta?.durationMs ?? 0}
                  spans={scenes.map((x) => ({ id: x.momentId, startMs: x.spanStartMs,
                                              endMs: x.spanEndMs,
                                              muted: sceneChoices[x.momentId]?.removed }))}
                  selectedId={sel?.momentId ?? null}
                  addLabel=""
                  onSelect={(id) => setSelBroll(id)}
                  onPlayhead={setSurfaceMs}
                />
                <div style={{ display: "flex", flexDirection: "column", gap: 16, marginTop: 12 }}>
                  {sel ? (
                    <BrollRow
                      key={sel.momentId} m={sel} sc={sceneChoices[sel.momentId]}
                      onSwap={() => void swapClip(sel.momentId)}
                      onToggleRemove={() => toggleRemoveClip(sel.momentId)}
                      onRetime={(ms) => retimeClip(sel.momentId, ms)}
                    />
                  ) : <p className="muted">No B-roll clips in this reel.</p>}
                </div>
              </>
            );
          })()}
          <button className="su-btn" style={{ marginTop: 16 }} disabled={rebaking} onClick={() => { setSheet(null); void reRender(); }}>Apply &amp; re-render</button>
        </Sheet>
      )}

      {sheet === "trimmed" && retakesInfo && (() => {
        // Regions drawn on the timeline, in whichever timeline the current mode uses.
        // "before": checkpoint time (cutStartMs/cutEndMs as-is, everything still present).
        // "after":  the already-cut base's own clock — an applied cut has no width there (its
        //           content is gone), so only NEW pending marks are drawable as regions.
        const applied = retakesInfo.retakes.filter((r) => !r.restored);
        const appliedIds = new Set(applied.map((r) => r.id));
        const active = retakesInfo.retakes.filter((r) => !pendingKeepIds.includes(r.id));
        const timelineDurationMs =
          trimMode === "after"
            ? (retakesInfo.currentDurationMs ?? retakesInfo.preRetakeDurationMs ?? 0)
            : (retakesInfo.preRetakeDurationMs ?? 0);
        const regions = (trimMode === "after"
          ? active.filter((r) => !appliedIds.has(r.id)) // newly marked, not yet applied/cut out
          : active
        )
          .filter((r) => r.cutStartMs != null && r.cutEndMs != null)
          .map((r) => ({ id: r.id, startMs: r.cutStartMs!, endMs: r.cutEndMs!, manual: r.confidence === "manual" }));
        // Diff against what is baked, in BOTH directions -- a cut you added and a cut you removed
        // are equally a reason to re-render. The previous version counted neither reliably: it
        // asked whether each cut was in `appliedIds`, which is derived from the same locally-mutated
        // list, so a new mark matched itself and counted as nothing. Un-marking a manual cut deletes
        // it from the list outright, so that went uncounted too and Apply stayed disabled.
        const baked = new Set(bakedCutIds);
        const liveCutIds = new Set(
          retakesInfo.retakes
            .filter((r) => !pendingKeepIds.includes(r.id) && r.cutStartMs != null)
            .map((r) => r.id),
        );
        const addedCount = [...liveCutIds].filter((id) => !baked.has(id)).length;
        const droppedCount = [...baked].filter((id) => !liveCutIds.has(id)).length;
        const pendingCount = addedCount + droppedCount;

        // Skipping cut regions on a requestAnimationFrame loop, NOT on the video's timeupdate
        // event. timeupdate fires roughly 4x/sec, so a cut region got up to ~250ms of playback
        // before the jump landed -- and ~250ms is a whole short word. With two adjacent cut
        // regions that produced exactly the reported "one, one, one connector": the first word of
        // each removed take, then the real one. Nothing was wrong with the cut itself; the file on
        // disk is correct and this player was simply late every time. A frame loop reacts within
        // ~16ms, and LOOKAHEAD_MS jumps just before the boundary so even that is inaudible.
        // 150ms, not 40ms. Seeking alone cannot stop a fragment being heard: audio already handed
        // to the output device (typically 50-100ms of it) plays out no matter when currentTime
        // changes, so a 40ms lead still let the first phoneme through -- "o, o, one connector"
        // instead of the earlier "one, one, one connector". The lead has to exceed the output
        // buffer. It costs nothing audible because the cut edge is placed inside silence with
        // _PAUSE_KEEP (250ms) handed back, so jumping 150ms early lands in that silence. The
        // element is also muted across the seek, to kill whatever is already queued.
        const LOOKAHEAD_MS = 150;
        // Coalesce exactly the way the renderer does: join two cuts when NO SURVIVING WORD sits
        // between them, with no limit on the gap's length (_coalesce_wordless_gaps). Using a fixed
        // duration threshold here instead was wrong and visibly so -- the renderer merges a
        // wordless gap of any size, so a 2s stretch of the speaker resetting between takes is
        // absent from the finished file while the preview still played all of it ("between 2 trims
        // there is space where the person is doing something weird"). Matching the rule rather than
        // approximating it means the preview shows the actual result. The small duration floor stays
        // as a fallback for slivers too short to hold a word at all.
        const SLIVER_MS = 120;
        const allWords = retakesInfo.words ?? [];
        const hasWordBetween = (fromMs: number, toMs: number) =>
          allWords.some((w) => w.startMs >= fromMs - 20 && w.endMs <= toMs + 20);
        const merged: { startMs: number; endMs: number }[] = [];
        for (const r of [...regions].sort((a, b) => a.startMs - b.startMs)) {
          const last = merged[merged.length - 1];
          const joinable = last && (r.startMs - last.endMs <= SLIVER_MS
                                    || !hasWordBetween(last.endMs, r.startMs));
          if (joinable) last!.endMs = Math.max(last!.endMs, r.endMs);
          else merged.push({ startMs: r.startMs, endMs: r.endMs });
        }
        // Leftovers stranded BETWEEN two cuts. A wordless gap is absorbed automatically (above, and
        // by _coalesce_wordless_gaps server-side), but a gap holding actual words is real speech and
        // must never be widened into silently -- deleting a sentence nobody selected is how the word
        // "not" once vanished from "so it's not lying on purpose". So these are surfaced instead of
        // absorbed: the island is named, with its own text, and removing it is one tap. That routes
        // through the ordinary manual-mark path, so the render honours it by exactly the same
        // mechanism as any other cut and preview cannot drift from output.
        const ISLAND_MAX_MS = 3000;   // longer than this reads as its own content, not a leftover
        const islands = merged.slice(0, -1).map((r, i) => {
          const a = r.endMs, b = merged[i + 1].startMs;
          const ws = allWords.filter((w) => w.startMs >= a - 20 && w.endMs <= b + 20);
          return { startMs: a, endMs: b, text: ws.map((w) => w.text).join(" ").trim() };
        }).filter((x) => x.text.length > 0 && x.endMs - x.startMs <= ISLAND_MAX_MS);
        const skipIfInsideCut = () => {
          const v = previewRef.current;
          if (!v) return;
          const ms = v.currentTime * 1000;
          const r = merged.find((x) => ms >= x.startMs - LOOKAHEAD_MS && ms < x.endMs);
          if (r) {
            if (!v.muted) { v.muted = true; weMutedRef.current = true; }
            v.currentTime = r.endMs / 1000 + 0.005;
            setPreviewTimeMs(r.endMs);
          } else {
            if (weMutedRef.current) { v.muted = false; weMutedRef.current = false; }
            setPreviewTimeMs(ms);
          }
        };
        const stopSkipLoop = () => {
          if (skipRafRef.current != null) cancelAnimationFrame(skipRafRef.current);
          skipRafRef.current = null;
        };
        const startSkipLoop = () => {
          stopSkipLoop();
          const tick = () => {
            const v = previewRef.current;
            if (!v || v.paused || v.ended) { skipRafRef.current = null; return; }
            skipIfInsideCut();
            skipRafRef.current = requestAnimationFrame(tick);
          };
          skipRafRef.current = requestAnimationFrame(tick);
        };
        return (
        <Sheet
          title="Trim"
          sub={
            pendingStartMs != null
              ? "Tap again to close this region — it'll turn red."
              : trimMode === "after"
                ? "Watching the cut version. Tap twice to trim more, or tap a red region to undo."
                : "Tap twice on the bar to mark a region to cut. Tap a red region to undo it."
          }
          onClose={() => { setSheet(null); setPendingStartMs(null); }}
        >
          <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
            <div style={{ display: "flex", gap: 6 }}>
              <button
                className="su-chip"
                style={{ flex: 1, justifyContent: "center", ...(trimMode === "before" ? { background: "var(--su-ink-900)", color: "#fff" } : {}) }}
                onClick={() => { setTrimMode("before"); setPendingStartMs(null); setPreviewTimeMs(0); }}
              >
                Original
              </button>
              <button
                className="su-chip"
                style={{ flex: 1, justifyContent: "center", ...(trimMode === "after" ? { background: "var(--su-ink-900)", color: "#fff" } : {}) }}
                onClick={() => { setTrimMode("after"); setPendingStartMs(null); setPreviewTimeMs(0); }}
              >
                After cuts
              </button>
            </div>

            <video
              key={trimMode}
              ref={previewRef}
              src={trimMode === "after" ? retakeFinalUrl(projectId, retakesInfo.currentDurationMs ?? 0) : retakePreviewUrl(projectId)}
              controls
              playsInline
              style={{ width: "100%", borderRadius: 10, background: "#000" }}
              onPlay={() => startSkipLoop()}
              onPause={() => stopSkipLoop()}
              onEnded={() => stopSkipLoop()}
              onSeeked={() => skipIfInsideCut()}
              onTimeUpdate={() => { if (previewRef.current?.paused) skipIfInsideCut(); }}
            />

            <RetakeTimeline
              durationMs={timelineDurationMs}
              currentTimeMs={previewTimeMs}
              cutRanges={regions}
              paintRanges={merged.map((m) => ({
                ...m,
                manual: regions.filter((r) => r.startMs >= m.startMs - 20 && r.endMs <= m.endMs + 20)
                  .every((r) => r.manual),
              }))}
              pendingStartMs={pendingStartMs}
              segments={trimMode === "before" ? retakesInfo.segments : []}
              onTap={(ms, hitId) => void onTrimTimelineTap(ms, hitId)}
            />

            <div style={{ display: "flex", alignItems: "center", gap: 8, minHeight: 20 }}>
              <span className="su-sub" style={{ flex: 1 }}>
                {trimSyncing
                  ? "Undoing…"
                  : marking
                  ? "Marking…"
                  : pendingStartMs != null
                    ? `Start set at ${fmtMs(pendingStartMs)} — tap the end point`
                    : `${regions.length} region${regions.length === 1 ? "" : "s"} marked to cut`}
              </span>
              {pendingStartMs != null && (
                <button className="su-iconbtn" aria-label="Cancel this marker" onClick={() => setPendingStartMs(null)}>
                  <X size={15} />
                </button>
              )}
              <button
                className="su-iconbtn"
                aria-label="Undo last trim change"
                title="Undo"
                disabled={trimUndo.length === 0 || trimSyncing || marking}
                onClick={() => void undoTrim()}
              >
                <RotateCcw size={15} />
              </button>
              <button
                className="su-iconbtn"
                aria-label="Redo trim change"
                title="Redo"
                disabled={trimRedo.length === 0 || trimSyncing || marking}
                onClick={() => void redoTrim()}
              >
                <Redo2 size={15} />
              </button>
            </div>

            {snapWarning && <p className="su-err" style={{ margin: 0 }}>{snapWarning}</p>}

            {islands.length > 0 && (
              <div style={{ display: "grid", gap: 6 }}>
                <span className="su-sub">
                  {islands.length === 1 ? "A bit is left" : `${islands.length} bits are left`} between your cuts
                </span>
                {islands.map((is) => (
                  <button
                    key={`${is.startMs}-${is.endMs}`}
                    className="su-chip"
                    disabled={marking}
                    style={{ justifyContent: "space-between", textAlign: "left", gap: 8, minHeight: 44 }}
                    onClick={() => void commitCut(is.startMs, is.endMs)}
                  >
                    <span style={{ flex: 1, minWidth: 0 }}>
                      <span style={{ opacity: 0.6 }}>{fmtMs(is.startMs)} · {((is.endMs - is.startMs) / 1000).toFixed(1)}s </span>
                      “{is.text.length > 44 ? `${is.text.slice(0, 44)}…` : is.text}”
                    </span>
                    <Scissors size={15} style={{ flexShrink: 0 }} />
                  </button>
                ))}
              </div>
            )}

            {/* PRECISION PATH. One second of the bar above is ~1.35pt (~0.2mm) at fit zoom, far
                below the ~5mm floor where a touch target stops trading size for error — so the bar
                is for orientation, and aiming happens here instead. Tap a line to cut the whole
                line; open it to cut an exact span of words. Each row/chip is list-sized, which
                clears the one-handed-thumb threshold, and no zoom, drag or haptics are needed. */}
            {retakesInfo.segments.length > 0 && (
              <>
                <div className="su-label" style={{ marginTop: 4 }}>Cut by what was said</div>
                <div className="su-stack" style={{ gap: 6, maxHeight: 260, overflowY: "auto" }}>
                  {retakesInfo.segments.map((s) => {
                    const alreadyCut = regions.some((r) => s.startMs >= r.startMs - 20 && s.endMs <= r.endMs + 20);
                    const open = expandedSeg === s.segIndex;
                    const sel = wordSel && wordSel.seg === s.segIndex ? wordSel : null;
                    return (
                      <div key={s.segIndex} style={{ opacity: alreadyCut ? 0.4 : 1 }}>
                        <div style={{ display: "flex", gap: 6, alignItems: "flex-start" }}>
                          <button
                            className="su-navrow"
                            style={{ flex: 1, textAlign: "left", minHeight: 44, padding: "8px 10px" }}
                            disabled={alreadyCut || marking}
                            onClick={() => {
                              if (previewRef.current) previewRef.current.currentTime = s.startMs / 1000;
                              setPreviewTimeMs(s.startMs);
                              setExpandedSeg(open ? null : s.segIndex);
                              setWordSel(null);
                            }}
                          >
                            <div className="txt" style={{ flex: 1, minWidth: 0 }}>
                              <div style={{ fontSize: 11, color: "var(--su-ink-500)" }}>
                                {fmtMsPrecise(s.startMs)}{alreadyCut ? " · already cut" : ""}
                              </div>
                              <div style={{ fontSize: 13, color: "var(--su-ink-900)", whiteSpace: "normal" }}>
                                {s.text}
                              </div>
                            </div>
                            <ChevronRight size={15} style={{ transform: open ? "rotate(90deg)" : "none", flexShrink: 0 }} />
                          </button>
                          {!alreadyCut && (
                            <button
                              className="su-iconbtn"
                              aria-label="Cut this whole line"
                              disabled={marking}
                              onClick={() => void commitCut(s.startMs, s.endMs)}
                              style={{ minWidth: 44, minHeight: 44 }}
                            >
                              <Scissors size={15} />
                            </button>
                          )}
                        </div>
                        {open && s.words.length > 0 && (
                          <div style={{ padding: "8px 4px 4px" }}>
                            <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
                              {s.words.map((w, i) => {
                                const inSel = sel != null && i >= sel.from && i <= sel.to;
                                return (
                                  <button
                                    key={i}
                                    onClick={() => onWordTap(s.segIndex, i)}
                                    style={{
                                      minHeight: 38, padding: "8px 10px", borderRadius: 8, cursor: "pointer",
                                      fontFamily: "inherit", fontSize: 13, fontWeight: 600,
                                      border: `1px solid ${inSel ? "var(--su-red)" : "var(--su-line-strong)"}`,
                                      background: inSel ? "var(--su-red)" : "var(--su-cream-50)",
                                      color: inSel ? "#fff" : "var(--su-ink-900)",
                                    }}
                                  >
                                    {w.text}
                                  </button>
                                );
                              })}
                            </div>
                            <div style={{ display: "flex", gap: 8, alignItems: "center", marginTop: 8 }}>
                              <span className="su-sub" style={{ flex: 1 }}>
                                {sel
                                  ? `${sel.to - sel.from + 1} word${sel.to === sel.from ? "" : "s"} · ${fmtMsPrecise(s.words[sel.from].startMs)}–${fmtMsPrecise(s.words[sel.to].endMs)}`
                                  : "Tap a word, then the last word of the part to remove."}
                              </span>
                              {sel && (
                                <button
                                  className="su-chip"
                                  disabled={marking}
                                  onClick={() => void commitCut(s.words[sel.from].startMs, s.words[sel.to].endMs)}
                                >
                                  <Scissors size={13} /> Cut
                                </button>
                              )}
                            </div>
                          </div>
                        )}
                      </div>
                    );
                  })}
                </div>
              </>
            )}

            {retakesInfo.silenceTrimmedMs > 500 && (
              <p className="su-sub">
                Plus {fmtMs(retakesInfo.silenceTrimmedMs)} of pauses and dead air, already trimmed automatically.
              </p>
            )}

            {/* Detail list collapsed by default — keeps the video + timeline the dominant thing
                on screen (adaptive-height guidance), with the AI's reasoning available on demand. */}
            {retakesInfo.retakes.length > 0 && (
              <>
                <button
                  className="su-action"
                  onClick={() => setShowTrimDetail((v) => !v)}
                  style={{ justifyContent: "space-between" }}
                >
                  <span>{showTrimDetail ? "Hide" : "Show"} what was cut &amp; why ({retakesInfo.retakes.length})</span>
                  <ChevronRight size={15} style={{ transform: showTrimDetail ? "rotate(90deg)" : "none" }} />
                </button>
                {showTrimDetail && retakesInfo.retakes.map((r) => {
                  const isKept = pendingKeepIds.includes(r.id);
                  return (
                    <div key={r.id} className="su-stack" style={{ gap: 4, opacity: isKept ? 0.55 : 1 }}>
                      <div style={{ display: "flex", gap: 8, alignItems: "flex-start" }}>
                        <div style={{ flex: 1, minWidth: 0 }}>
                          <div style={{ fontSize: 11, color: "var(--su-ink-500)", textTransform: "uppercase", letterSpacing: 0.4 }}>
                            {r.confidence === "manual" ? "By you" : r.confidence === "high" ? "High confidence" : "Medium confidence"} · {isKept ? "kept" : "cut"}
                            {r.cutStartMs != null ? ` · ${fmtMs(r.cutStartMs)}` : ""}
                          </div>
                          <div style={{ fontSize: 14, fontWeight: 600, color: "var(--su-ink-900)", textDecoration: isKept ? "none" : "line-through" }}>
                            "{r.text}"
                          </div>
                          <div style={{ fontSize: 12, color: "var(--su-ink-500)", marginTop: 2 }}>{r.reason}</div>
                        </div>
                        <button
                          className="su-iconbtn"
                          aria-label={isKept ? "Cut this again" : "Restore"}
                          onClick={() => {
                            snapshotTrim();
                            if (r.confidence === "manual" && !isKept) {
                              void unmarkRetake(projectId, r.id)
                                .then(() => setRetakesInfo((info) => (info ? { ...info, retakes: info.retakes.filter((x) => x.id !== r.id) } : info)))
                                .catch((e) => setError((e as Error).message));
                              return;
                            }
                            setPendingKeepIds((ids) => (isKept ? ids.filter((x) => x !== r.id) : [...ids, r.id]));
                          }}
                        >
                          {isKept ? <Trash2 size={15} /> : <RotateCcw size={15} />}
                        </button>
                      </div>
                    </div>
                  );
                })}
              </>
            )}
            {!retakesInfo.retakes.length ? <p className="muted">Nothing cut yet — tap twice on the bar to trim a part out.</p> : null}
          </div>
          <button
            className="su-btn"
            style={{ marginTop: 16 }}
            disabled={restoringRetakes || marking || pendingCount === 0}
            onClick={() => void applyRetakeRestore(pendingKeepIds)}
          >
            {pendingCount === 0 ? "No changes to apply" : `Apply ${pendingCount} change${pendingCount === 1 ? "" : "s"} & re-render`}
          </button>
        </Sheet>
        );
      })()}

      {sheet === "zooms" && (() => {
        const dur = reelMeta?.durationMs ?? 0;
        const i = selZoom != null && zooms[selZoom] ? selZoom : null;
        const z = i != null ? zooms[i] : null;
        return (
        <Sheet
          title="Zooms"
          sub="Scrub to a moment, tap a zoom to edit it, or add one where you are."
          onClose={() => {
            if (zoomPvTimer.current) window.clearTimeout(zoomPvTimer.current);
            zoomVidRef.current?.pause(); setZoomPv(null); setSheet(null);
          }}
        >
          <EditSurface
            src={result ? `/api/result/${result.outputName}` : ""}
            durationMs={dur}
            videoRef={zoomVidRef}
            videoStyle={{
              transformOrigin: "0 0",
              transform: `scale(${zoomPv?.scale ?? 1}) translate(${-(zoomPv?.left ?? 0) * 100}%, ${-(zoomPv?.top ?? 0) * 100}%)`,
              transition: zoomPv && zoomPv.ms > 0
                ? `transform ${zoomPv.ms}ms cubic-bezier(0.65,0,0.35,1)` : "none",
            }}
            spans={zooms.map((x, k) => ({ id: String(k), startMs: x.startMs, endMs: x.endMs, muted: !!x.off }))}
            selectedId={i != null ? String(i) : null}
            addLabel="Add a zoom here"
            onSelect={(id) => setSelZoom(Number(id))}
            onAdd={(at) => { addZoomAt(at); }}
            onPlayhead={setSurfaceMs}
          />

          {z && i != null ? (
            <div className="su-stack" style={{ gap: 8, marginTop: 12, opacity: z.off ? 0.45 : 1 }}>
              <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                <span className="su-sub" style={{ flex: 1 }}>
                  {fmtMsPrecise(z.startMs)} · {((z.endMs - z.startMs) / 1000).toFixed(1)}s
                  {z.transcriptPhrase ? ` · “${z.transcriptPhrase.slice(0, 22)}”` : ""}
                </span>
                <button className="su-chip" disabled={z.off || !result} style={{ minHeight: 34 }}
                        onClick={() => previewZoom(z)}><Play size={13} /> Play it</button>
                <button className="su-chip" disabled={z.off} style={{ minHeight: 34 }}
                        onClick={() => patchZoom(i, { startMs: Math.round(surfaceMs),
                                                     endMs: Math.round(surfaceMs) + (z.endMs - z.startMs) })}>
                  Move here
                </button>
                <button className="su-iconbtn" aria-label={z.off ? "Turn back on" : "Turn off"}
                        onClick={() => patchZoom(i, { off: !z.off })}>
                  {z.off ? <RotateCcw size={15} /> : <Trash2 size={15} />}
                </button>
              </div>
              <div style={{ display: "flex", gap: 6 }}>
                {(["subtle", "medium", "strong"] as const).map((k) => (
                  <button key={k} className="su-chip" disabled={z.off}
                    style={{ flex: 1, justifyContent: "center", ...(zoomTier(z.targetScale) === k ? { background: "var(--su-ink-900)", color: "#fff" } : {}) }}
                    onClick={() => patchZoom(i, { targetScale: ZOOM_STRENGTH[k] })}>
                    {k === "subtle" ? "Subtle" : k === "medium" ? "Medium" : "Strong"}
                  </button>
                ))}
              </div>
              <div style={{ display: "flex", gap: 6 }}>
                {(["slow_push", "quick_punch"] as const).map((st) => (
                  <button key={st} className="su-chip" disabled={z.off}
                    style={{ flex: 1, justifyContent: "center", ...(z.style === st ? { background: "var(--su-ink-900)", color: "#fff" } : {}) }}
                    onClick={() => patchZoom(i, { style: st })}>
                    {st === "slow_push" ? "Slow push" : "Punch"}
                  </button>
                ))}
                {(["face", "center"] as const).map((an) => (
                  <button key={an} className="su-chip" disabled={z.off}
                    style={{ flex: 1, justifyContent: "center", ...((z.anchor ?? "face") === an ? { background: "var(--su-ink-900)", color: "#fff" } : {}) }}
                    onClick={() => patchZoom(i, { anchor: an })}>
                    {an === "face" ? "On her" : "Centre"}
                  </button>
                ))}
              </div>
              <div style={{ display: "flex", gap: 10, alignItems: "center" }}>
                <span className="su-sub" style={{ width: 62 }}>Length</span>
                <input type="range" min={800} max={5000} step={100} value={z.endMs - z.startMs}
                  disabled={z.off} onChange={(e) => patchZoom(i, { endMs: z.startMs + Number(e.target.value) })}
                  style={{ flex: 1 }} />
              </div>
            </div>
          ) : (
            <p className="su-sub" style={{ marginTop: 12 }}>
              {zooms.length ? "Tap a zoom on the bar to edit it." : "No zooms yet — scrub to a moment and add one."}
            </p>
          )}
          <p className="su-sub" style={{ marginTop: 10 }}>
            “Play it” shows the real strength, speed and timing. “On her” is approximated here — the
            exact face position is found during the render.
          </p>
          {!toggles.zoom && zooms.length > 0 ? (
            <p className="su-sub" style={{ margin: "6px 0 0" }}>
              Auto-zoom is off in Fine-tune, so none of these will render until you turn it back on.
            </p>
          ) : null}
          <button className="su-btn" style={{ marginTop: 16 }} disabled={rebaking}
                  onClick={() => { setSheet(null); void reRender(); }}>Apply &amp; re-render</button>
        </Sheet>
        );
      })()}

      {sheet === "cards" && (() => {
        const cards = moments.filter((m) => m.card);
        const dur = reelMeta?.durationMs ?? 0;
        const m = cards.find((x) => x.momentId === selCard) ?? null;
        const c = m?.card ?? null;
        const gone = m ? removedCardIds.includes(m.momentId) : false;
        return (
        <Sheet
          title="Graphic cards"
          sub="Scrub to a moment, tap a card to edit it, or add one where you are."
          onClose={() => setSheet(null)}
        >
          <EditSurface
            src={result ? `/api/result/${result.outputName}` : ""}
            durationMs={dur}
            spans={cards.map((x) => ({ id: x.momentId, startMs: x.spanStartMs, endMs: x.spanEndMs,
                                       muted: removedCardIds.includes(x.momentId) }))}
            selectedId={selCard}
            addLabel="Add a card here"
            onSelect={(id) => setSelCard(id)}
            onAdd={(at) => addCardAt(at)}
            onPlayhead={setSurfaceMs}
          />

          {m && c ? (
            <div style={{ display: "flex", gap: 12, marginTop: 12, opacity: gone ? 0.45 : 1 }}>
              {/* A card is a FULL-FRAME overlay that does not exist in the rendered video until the
                  next render, so the player above cannot show it -- this is the only way to see it,
                  and it needs to be big enough to judge type and wrapping. */}
              <CardSample card={c} width={112} />
              <div className="su-stack" style={{ gap: 8, flex: 1, minWidth: 0 }}>
                <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                  <span className="su-sub" style={{ flex: 1 }}>
                    {fmtMsPrecise(m.spanStartMs)} · {((m.spanEndMs - m.spanStartMs) / 1000).toFixed(1)}s
                    {m.momentId.startsWith("usercard-") ? " · yours" : ""}
                  </span>
                  <button className="su-chip" disabled={gone} style={{ minHeight: 34 }}
                          onClick={() => retimeCard(m.momentId, Math.round(surfaceMs))}>
                    Move here
                  </button>
                  <button className="su-iconbtn" aria-label={gone ? "Put back" : "Remove"}
                          onClick={() => setRemovedCardIds((ids) =>
                            gone ? ids.filter((x) => x !== m.momentId) : [...ids, m.momentId])}>
                    {gone ? <RotateCcw size={15} /> : <Trash2 size={15} />}
                  </button>
                </div>
                <div style={{ display: "flex", gap: 6 }}>
                  {(["stat", "phrase", "list"] as const).map((t) => (
                    <button key={t} className="su-chip" disabled={gone}
                      style={{ flex: 1, justifyContent: "center", ...(c.cardType === t ? { background: "var(--su-ink-900)", color: "#fff" } : {}) }}
                      onClick={() => patchCard(m.momentId, { cardType: t })}>
                      {t === "stat" ? "Stat" : t === "phrase" ? "Quote" : "List"}
                    </button>
                  ))}
                </div>
                <div style={{ display: "flex", gap: 6 }}>
                  {(["ink", "amber", "night"] as const).map((st) => (
                    <button key={st} className="su-chip" disabled={gone}
                      style={{ flex: 1, justifyContent: "center", ...((c.style ?? "ink") === st ? { background: "var(--su-ink-900)", color: "#fff" } : {}) }}
                      onClick={() => patchCard(m.momentId, { style: st })}>
                      {st === "ink" ? "Ink" : st === "amber" ? "Amber" : "Night"}
                    </button>
                  ))}
                </div>
              </div>
            </div>
          ) : (
            <p className="su-sub" style={{ marginTop: 12 }}>
              {cards.length ? "Tap a card on the bar to edit it." : "No cards yet — scrub to a moment and add one."}
            </p>
          )}

          {m && c ? (
            <div className="su-stack" style={{ gap: 8, marginTop: 10, opacity: gone ? 0.45 : 1 }}>
              <input value={c.headline} disabled={gone} placeholder="Headline"
                onChange={(e) => patchCard(m.momentId, { headline: e.target.value })}
                style={{ border: "1px solid var(--su-line-strong)", borderRadius: 8, padding: "9px 10px",
                         fontSize: 13, fontWeight: 700, color: "var(--su-ink-900)", background: "#fff" }} />
              {c.cardType === "stat" ? (
                <input value={c.value ?? ""} disabled={gone} placeholder="The number, e.g. 10,000+"
                  onChange={(e) => patchCard(m.momentId, { value: e.target.value })}
                  style={{ border: "1px solid var(--su-line-strong)", borderRadius: 8, padding: "9px 10px",
                           fontSize: 13, color: "var(--su-ink-900)", background: "#fff" }} />
              ) : null}
              {c.cardType === "list" ? (
                <textarea value={(c.items ?? []).join("\n")} disabled={gone} rows={3}
                  placeholder={"One item per line (up to 4)"}
                  onChange={(e) => patchCard(m.momentId, {
                    items: e.target.value.split("\n").map((x) => x.trim()).filter(Boolean).slice(0, 4) })}
                  style={{ border: "1px solid var(--su-line-strong)", borderRadius: 8, padding: "9px 10px",
                           fontSize: 13, color: "var(--su-ink-900)", background: "#fff", resize: "vertical",
                           fontFamily: "inherit" }} />
              ) : null}
              <div style={{ display: "flex", gap: 10, alignItems: "center" }}>
                <span className="su-sub" style={{ width: 62 }}>Length</span>
                <input type="range" min={1200} max={6000} step={100} disabled={gone}
                  value={m.spanEndMs - m.spanStartMs}
                  onChange={(e) => setCardDuration(m.momentId, Number(e.target.value))}
                  style={{ flex: 1 }} />
              </div>
              <p className="su-sub" style={{ margin: 0 }}>
                The preview mirrors the renderer's layout and colours, but the final card is drawn
                during the render — check long headlines there.
              </p>
            </div>
          ) : null}

          <button className="su-btn" style={{ marginTop: 16 }} disabled={rebaking}
                  onClick={() => { setSheet(null); void reRender(); }}>Apply &amp; re-render</button>
        </Sheet>
        );
      })()}

      {sheet === "brollReview" && (
        <Sheet
          title="Review B-roll placement"
          sub="We matched clips to these moments — reassign, remove, or retime before rendering."
          onClose={() => { setSheet(null); confirmBrollReview(); }}
        >
          <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
            {moments.filter((m) => m.type === "scene" && sceneChoices[m.momentId]).map((m) => (
              <BrollRow
                key={m.momentId} m={m} sc={sceneChoices[m.momentId]}
                onSwap={() => void swapClip(m.momentId)}
                onToggleRemove={() => toggleRemoveClip(m.momentId)}
                onRetime={(ms) => retimeClip(m.momentId, ms)}
              />
            ))}
            {unusedUserClipKeys.length > 0 ? (
              <p className="su-sub">
                {unusedUserClipKeys.length} of your uploaded clip{unusedUserClipKeys.length > 1 ? "s" : ""} didn't
                match a moment well and won't appear in this reel.
              </p>
            ) : null}
          </div>
          <button className="su-btn" style={{ marginTop: 16 }} onClick={() => { setSheet(null); confirmBrollReview(); }}>
            <Check size={16} /> Confirm &amp; render
          </button>
        </Sheet>
      )}

      {sheet === "sfx" && (
        <Sheet title="Sound effects" sub="Preview a sound, scrub the clip below to a moment, then add it there." onClose={() => setSheet(null)}>
          <EditSurface
            src={result ? `/api/result/${result.outputName}` : ""}
            durationMs={reelMeta?.durationMs ?? 0}
            videoRef={sfxPlayerRef}
            spans={[]}
            points={sfxHits.map((h) => ({ id: h.id, atMs: h.atMs }))}
            addLabel=""
            onPlayhead={setSurfaceMs}
          />
          <p className="su-sub" style={{ margin: "6px 0 0" }}>
            Dots are your placed sounds. Scrub to a moment, then tap + on any sound below to drop it
            there.
          </p>
          {sfxDebug ? (
            <div style={{ marginTop: 8, padding: "6px 10px", background: "#111", color: "#0f0", fontFamily: "monospace", fontSize: 11, borderRadius: 6, wordBreak: "break-all" }}>
              {sfxDebug}
            </div>
          ) : null}
          {sfxCatalog.categories.map((cat) => (
            <div key={cat.id} style={{ marginTop: 14 }}>
              <div className="su-label">{cat.label}</div>
              <div style={{ display: "flex", flexWrap: "wrap", gap: 8, marginTop: 6 }}>
                {sfxCatalog.sounds.filter((s) => s.category === cat.id).map((s) => (
                  <div key={s.id} style={{ display: "flex", alignItems: "center", gap: 4, border: "1px solid var(--su-line-strong)", borderRadius: 8, padding: "5px 6px 5px 10px" }}>
                    <span style={{ fontSize: 12, fontWeight: 600, color: "var(--su-ink-900)" }}>{s.label}</span>
                    <button className="su-iconbtn" onClick={() => toggleCatalogPreview(s.id, s.url)} aria-label={catalogPreviewId === s.id ? `Stop ${s.label}` : `Preview ${s.label}`}>
                      {catalogPreviewId === s.id ? <Pause size={13} /> : <Play size={13} />}
                    </button>
                    <button className="su-iconbtn" onClick={() => addSfxHit(s.id)} aria-label={`Add ${s.label}`}><Plus size={13} /></button>
                  </div>
                ))}
              </div>
            </div>
          ))}
          {sfxHits.length > 0 ? (
            <div style={{ marginTop: 16 }}>
              <div className="su-label">
                Placed{sfxHits.some((h) => h.source === "auto")
                  ? ` — ${sfxHits.filter((h) => h.source === "auto").length} added automatically, tap any to remove`
                  : ""}
              </div>
              <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginTop: 6 }}>
                {sfxHits.map((h) => {
                  const snd = sfxCatalog.sounds.find((s) => s.id === h.soundId);
                  return (
                    <button
                      key={h.id} onClick={() => removeSfxHit(h.id)}
                      style={{ border: "1px solid var(--su-line-strong)", background: "var(--su-yellow)", borderRadius: 8, padding: "5px 9px", fontSize: 12, display: "flex", alignItems: "center", gap: 4, color: "var(--su-ink-900)" }}
                    >
                      {snd?.label ?? h.soundId} @ {(h.atMs / 1000).toFixed(1)}s
                      {h.source === "auto" ? ` · ${h.reason ?? "auto"}` : ""} <X size={12} />
                    </button>
                  );
                })}
              </div>
            </div>
          ) : null}
          <button className="su-btn" style={{ marginTop: 16 }} disabled={rebaking} onClick={() => { setSheet(null); void reRender(); }}>Apply &amp; re-render</button>
        </Sheet>
      )}

      {sheet === "music" && (
        <Sheet title="Music" sub="Tap a track — it plays with your video right away. Tap it again to stop." onClose={() => { musicAudition.stop(); setSheet(null); }}>
          <PreviewPlayer ref={musicAudition.videoRef} src={result ? `/api/result/${result.outputName}` : ""} />
          <audio ref={musicAudition.musicRef} style={{ display: "none" }} />
          {musicAudition.active ? (
            <p className="su-sub" style={{ margin: "10px 0 0" }}>
              🔊 Playing your video's own audio + {musicLabel ?? "this track"} together. Ducking is a simplified
              preview — the real sidechain-ducked mix happens after Apply &amp; re-render.
            </p>
          ) : null}
          <div style={{ marginTop: 14 }}>
            <MusicPicker
              catalog={musicCatalog}
              userTracks={userMusicTracks}
              selection={musicSelection}
              onSelectTrack={selectMusicTrack}
              onChange={setMusicSelection}
              onUploadClick={() => musicFileRef.current?.click()}
              uploading={musicUploading}
            />
          </div>
          <input ref={musicFileRef} type="file" accept="audio/*" style={{ display: "none" }} onChange={(e) => void onPickMusicFile(e)} />
          <button
            className="su-btn" style={{ marginTop: 16 }} disabled={rebaking}
            onClick={() => { musicAudition.stop(); setSheet(null); void reRender(); }}
          >
            {rebaking ? <Loader2 size={16} className="su-spin" style={{ border: 0 }} /> : <RotateCcw size={16} />}
            {rebaking ? "Re-rendering…" : "Apply & re-render"}
          </button>
        </Sheet>
      )}

      {sheet === "chat" && (
        <Sheet title="Ask AI to edit" sub="Tell it what to change — it'll show you the changes before applying them." onClose={() => setSheet(null)}>
          <div style={{ display: "flex", flexDirection: "column", gap: 8, maxHeight: 260, overflowY: "auto" }}>
            {chatMessages.length === 0 ? (
              <p className="su-sub" style={{ margin: 0 }}>
                Try: "change the captions to hormozi style", "remove the second B-roll clip", or "add a whoosh at 3 seconds".
              </p>
            ) : null}
            {chatMessages.map((m, i) => (
              <div
                key={i}
                style={{
                  alignSelf: m.role === "user" ? "flex-end" : "flex-start", maxWidth: "85%",
                  background: m.role === "user" ? "var(--su-indigo)" : "var(--su-cream-100)",
                  color: m.role === "user" ? "#fff" : "var(--su-ink-900)",
                  borderRadius: 12, padding: "8px 12px", fontSize: 13,
                }}
              >
                {m.content}
              </div>
            ))}
            {chatBusy ? (
              <div style={{ alignSelf: "flex-start", display: "flex", alignItems: "center", gap: 6, fontSize: 12, color: "var(--su-ink-500)" }}>
                <Loader2 size={14} className="su-spin" style={{ border: 0 }} /> thinking…
              </div>
            ) : null}
          </div>
          {chatPendingActions.length > 0 ? (
            <div className="su-card su-stack" style={{ gap: 8, marginTop: 12 }}>
              <div className="su-label">Proposed changes</div>
              {chatPendingActions.map((a, i) => (
                <div key={i} style={{ fontSize: 12, color: "var(--su-ink-900)" }}>
                  • {describeChatAction(a, moments, sfxCatalog, musicCatalog, userMusicTracks)}
                </div>
              ))}
              <div style={{ display: "flex", gap: 8 }}>
                <button className="su-btn" disabled={chatApplying} onClick={() => void applyChatActions()}>
                  {chatApplying ? <Loader2 size={16} className="su-spin" style={{ border: 0 }} /> : <Check size={16} />}
                  {chatApplying ? "Applying…" : "Apply & re-render"}
                </button>
                <button className="su-btn ghost" disabled={chatApplying} onClick={() => setChatPendingActions([])}>Discard</button>
              </div>
            </div>
          ) : null}
          <div style={{ display: "flex", gap: 8, marginTop: 12 }}>
            <input
              value={chatInput}
              onChange={(e) => setChatInput(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter") void sendChatMessage(); }}
              placeholder="What should I change?"
              disabled={chatBusy}
              style={{ flex: 1, border: "1px solid var(--su-line-strong)", borderRadius: 8, padding: "8px 10px", fontSize: 13, color: "var(--su-ink-900)" }}
            />
            <button className="su-iconbtn" disabled={chatBusy || !chatInput.trim()} onClick={() => void sendChatMessage()} aria-label="Send"><MessageCircle size={16} /></button>
          </div>
        </Sheet>
      )}

      {sheet === "captions" && (
        <Sheet title="Captions" sub="Change the style, or tap a word to fix it, then re-render." onClose={() => setSheet(null)}>
          <CaptionStyleControls reg={reg} value={caption} onChange={setCaption} />
          <div style={{ fontSize: 12, fontWeight: 700, margin: "16px 0 6px", color: "var(--su-ink-900)" }}>Edit words</div>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
            {words.map((w) => {
              const cur = editedText[w.id] ?? w.text;
              return (
                <button
                  key={w.id}
                  onClick={() => {
                    const next = window.prompt("Edit word", cur);
                    if (next != null) {
                      setEditedText((m) => {
                        const c = { ...m };
                        if (next.trim() && next.trim() !== w.text) c[w.id] = next.trim();
                        else delete c[w.id];
                        return c;
                      });
                    }
                  }}
                  style={{
                    border: "1px solid var(--su-line-strong)",
                    background: editedText[w.id] ? "var(--su-yellow)" : "#fff",
                    borderRadius: 8, padding: "5px 9px", fontSize: 13, cursor: "pointer", color: "var(--su-ink-900)",
                  }}
                >
                  {cur}
                </button>
              );
            })}
            {!words.length ? <p className="muted">Transcript not available.</p> : null}
          </div>
          <button className="su-btn" style={{ marginTop: 16 }} disabled={rebaking} onClick={() => { setSheet(null); void reRender(); }}>Apply &amp; re-render</button>
        </Sheet>
      )}

      {sheet === "look" && (
        <Sheet
          title="Look"
          sub="Live preview on your actual video — tap a look to apply it instantly, then confirm to bake it in."
          onClose={() => setSheet(null)}
        >
          <PreviewPlayer
            src={result ? `/api/result/${result.outputName}` : ""}
            filter={cssFilterFor(toggles.colorGradeLook, toggles.colorGradeIntensity)}
          />
          <div style={{ marginTop: 14 }}>
            <LookPicker
              look={toggles.colorGradeLook}
              intensity={toggles.colorGradeIntensity}
              onChange={(look, intensity) => setToggles((t) => ({ ...t, colorGradeLook: look, colorGradeIntensity: intensity }))}
            />
          </div>
          <button
            className="su-btn"
            style={{ marginTop: 16 }}
            disabled={regrading}
            onClick={() => void applyLook(toggles.colorGradeLook, toggles.colorGradeIntensity)}
          >
            {regrading ? <Loader2 size={16} className="su-spin" style={{ border: 0 }} /> : <Palette size={16} />}
            {regrading ? "Applying…" : "Apply & re-render"}
          </button>
        </Sheet>
      )}

    </div>
  );
}


// ---- automatic sound-effect placement -------------------------------------------------------
// Needs NO new detection: every anchor is a signal the pipeline already produced. B-roll spans,
// zoom spans and card moments are exactly where an accent belongs, which is why this is a mapping
// rather than a model. Kept client-side because all three already live in React state and sfxHits
// is already what gets sent to the render -- no backend involved.
//
// Density is the whole design problem, not placement. The editing research is consistent that
// over-dense SFX is the single clearest amateur tell (MrBeast publicly cut his own cuts-per-minute
// 38 -> 23 and views nearly tripled), so this deliberately places FEW: one family of sounds, a hard
// cap, a minimum gap, and the hook/close left alone -- mirroring the guards zoom_plan.py already
// applies for the same reason.
const SFX_MAX_HITS = 5;
const SFX_MIN_GAP_MS = 8000;    // same cadence zoom_plan.py uses (MIN_GAP_MS)
const SFX_EDGE_PROTECT_MS = 3500; // hook and close belong to the speaker (HOOK/CLOSE_PROTECT_MS)

function planSfx(
  moments: Moment[], choices: Record<string, SceneChoice>, zooms: Zoom[],
  removedCardIds: string[], durationMs: number,
): PlacedSfx[] {
  const close = Math.max(0, durationMs - SFX_EDGE_PROTECT_MS);
  type Cand = { atMs: number; soundId: string; reason: string; priority: number };
  const cands: Cand[] = [];
  // A cutaway arriving is the strongest, most legible accent point of the three.
  for (const m of moments) {
    if (m.type === "scene" && choices[m.momentId] && !choices[m.momentId].removed) {
      cands.push({ atMs: m.spanStartMs, soundId: "whoosh", reason: "B-roll cutaway", priority: 3 });
    } else if (m.card && !removedCardIds.includes(m.momentId)) {
      cands.push({ atMs: m.spanStartMs, soundId: "pop", reason: "graphic card", priority: 2 });
    }
  }
  // A punch-in gets the lighter sibling so a reel with both does not sound like one repeated noise.
  for (const z of zooms) {
    if (!z.off) cands.push({ atMs: z.startMs, soundId: "whoosh-2", reason: "punch-in", priority: 1 });
  }
  // Strongest anchors claim their slot FIRST, then weaker ones fill the gaps. Sorting by time
  // instead was tried and is wrong: a cutaway -- the most legible accent point there is -- lost its
  // slot to a punch-in three seconds earlier purely because that came first, so priority did almost
  // nothing. Placing by priority means the gap rule drops the weakest candidate, not the latest one.
  const taken: number[] = [];
  const chosen: Cand[] = [];
  for (const c of cands
    .filter((c) => c.atMs >= SFX_EDGE_PROTECT_MS && c.atMs <= close)
    .sort((a, b) => b.priority - a.priority || a.atMs - b.atMs)) {
    if (chosen.length >= SFX_MAX_HITS) break;
    if (taken.some((t) => Math.abs(c.atMs - t) < SFX_MIN_GAP_MS)) continue;
    taken.push(c.atMs);
    chosen.push(c);
  }
  return chosen
    .sort((a, b) => a.atMs - b.atMs)
    .map((c) => ({ id: crypto.randomUUID(), soundId: c.soundId, atMs: Math.round(c.atMs),
                   gainDb: 0, source: "auto" as const, reason: c.reason }));
}

// pure helper (used inside run(), where `moments` state isn't updated yet)
function buildAcceptedFrom(moments: Moment[], choices: Record<string, SceneChoice>, t: Toggles,
                          removedCardIds: string[] = []): Accepted[] {
  const out: Accepted[] = [];
  for (const m of moments) {
    if (m.type === "scene") {
      const sc = choices[m.momentId];
      if (t.broll && sc && !sc.removed) {
        out.push({
          momentId: m.momentId, startMs: m.spanStartMs, endMs: m.spanEndMs,
          mediaKind: sc.candidates[sc.idx].kind, url: sc.cachedUrl, sourceStartMs: sc.sourceStartMs || 0,
        });
      }
    } else if (t.cards && m.card && !removedCardIds.includes(m.momentId)) {
      out.push({ momentId: m.momentId, startMs: m.spanStartMs, endMs: m.spanEndMs, kind: "card", card: m.card });
    }
  }
  return out;
}

// Trimmed sheet's trim timeline -- an ORIENTATION surface, not the precision instrument. It shows
// speech landmarks, what's already cut, and where the playhead is, and supports coarse tap-tap
// mark-in/mark-out (the named "Mark In / Mark Out + Multi Trim" pattern). It is deliberately not
// how a short cut gets aimed: at fit zoom one second is ~1.35pt (~0.2mm) while a finger's own
// positional noise is measured in whole seconds of timeline, so precise selection lives in the
// transcript word chips instead. Edges still snap to word boundaries server-side on commit, and
// a range that couldn't be snapped is reported rather than silently cut at the raw tap time.
function RetakeTimeline({ durationMs, cutRanges, paintRanges, pendingStartMs, currentTimeMs, onTap, segments }: {
  durationMs: number;
  cutRanges: { id: string; startMs: number; endMs: number; manual: boolean }[];
  // What to DRAW, when it differs from what to hit-test. Playback skips cuts that were merged
  // across a wordless gap, so drawing the raw regions left two red bars with a visible space
  // between them for something the player already jumps over in one go -- it reads as "the gap
  // wasn't removed" when it was. Hit-testing stays on cutRanges so a tap still resolves to one
  // region id and can be unmarked individually.
  paintRanges?: { startMs: number; endMs: number; manual: boolean }[];
  pendingStartMs: number | null;
  currentTimeMs: number;
  onTap: (ms: number, hitRegionId: string | null) => void;
  segments: { segIndex: number; startMs: number; endMs: number }[];
}) {
  const barRef = useRef<HTMLDivElement | null>(null);
  const pct = (ms: number) => `${Math.min(100, Math.max(0, (ms / Math.max(durationMs, 1)) * 100))}%`;
  const handleTap = (e: React.MouseEvent<HTMLDivElement>) => {
    const rect = barRef.current?.getBoundingClientRect();
    if (!rect || durationMs <= 0) return;
    const ms = Math.min(1, Math.max(0, (e.clientX - rect.left) / rect.width)) * durationMs;
    const hit = cutRanges.find((r) => ms >= r.startMs && ms <= r.endMs);
    onTap(ms, hit ? hit.id : null);
  };
  return (
    <div
      ref={barRef}
      onClick={handleTap}
      style={{
        position: "relative", height: 44, background: "var(--su-cream-300)",
        borderRadius: 8, cursor: "pointer", overflow: "hidden", touchAction: "manipulation",
      }}
    >
      {/* Speech landmarks. Without these the bar is a featureless block -- there is nothing to
          aim at even before finger precision becomes the limit. These are deliberately NOT tap
          targets (at fit zoom a segment is only a few points wide); they exist so the user can
          see where speech, pauses and cuts sit. Aiming happens in the transcript list. */}
      {segments.map((s) => (
        <div
          key={`seg${s.segIndex}`}
          style={{
            position: "absolute", top: 14, bottom: 14, left: pct(s.startMs),
            width: `${Math.max(0.25, ((s.endMs - s.startMs) / Math.max(durationMs, 1)) * 100)}%`,
            background: "var(--su-ink-300)", opacity: 0.55, borderRadius: 2,
          }}
        />
      ))}
      {(paintRanges ?? cutRanges).map((r, ri) => (
        <div
          key={`paint${ri}`}
          style={{
            position: "absolute", top: 0, bottom: 0, left: pct(r.startMs),
            width: `${Math.max(0.8, ((r.endMs - r.startMs) / Math.max(durationMs, 1)) * 100)}%`,
            background: r.manual ? "var(--su-red)" : "var(--su-coral)", opacity: 0.9,
          }}
        />
      ))}
      {pendingStartMs != null && (
        // Open (unclosed) start marker — deliberately distinct from a finished region so it's
        // obvious the next tap will close it rather than start something new.
        <div style={{ position: "absolute", top: 0, bottom: 0, left: pct(pendingStartMs), width: 3, background: "var(--su-yellow)" }}>
          <div style={{ position: "absolute", top: 0, left: 0, width: 9, height: 9, background: "var(--su-yellow)" }} />
        </div>
      )}
      <div style={{ position: "absolute", top: 0, bottom: 0, width: 2, background: "var(--su-ink-900)", left: pct(currentTimeMs) }} />
    </div>
  );
}

// One B-roll moment row — thumbnail, source label, swap/remove, and (only when the source's
// real duration is known and longer than the placement window) a retime slider. Shared by the
// pre-render review sheet and Fine-tune's "B-roll clips" sheet so both stay in sync by construction.
function BrollRow({ m, sc, onSwap, onToggleRemove, onRetime }: {
  m: Moment; sc: SceneChoice;
  onSwap: () => void; onToggleRemove: () => void; onRetime: (ms: number) => void;
}) {
  const cand = sc.candidates[sc.idx];
  const windowMs = m.spanEndMs - m.spanStartMs;
  const canRetime = cand.kind === "video" && (sc.sourceDurationMs ?? 0) > windowMs;
  const maxStartMs = canRetime ? Math.max(0, (sc.sourceDurationMs ?? 0) - windowMs) : 0;
  return (
    <div className="su-stack" style={{ gap: 8, opacity: sc.removed ? 0.4 : 1 }}>
      <div style={{ display: "flex", gap: 10, alignItems: "center" }}>
        {cand.thumbUrl ? (
          <img src={cand.thumbUrl} alt="" style={{ width: 46, height: 82, objectFit: "cover", borderRadius: 8, background: "#000", flexShrink: 0 }} />
        ) : (
          <div style={{ width: 46, height: 82, borderRadius: 8, background: "var(--su-ink-900)", display: "flex", alignItems: "center", justifyContent: "center", flexShrink: 0 }}>
            <Clapperboard size={18} color="#fff" />
          </div>
        )}
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontSize: 12, color: "var(--su-ink-500)" }}>{(m.spanStartMs / 1000).toFixed(0)}–{(m.spanEndMs / 1000).toFixed(0)}s · {cand.source}</div>
          <div style={{ fontSize: 14, fontWeight: 700, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", color: "var(--su-ink-900)" }}>
            {sc.userClipDescription || m.primaryQuery}
          </div>
        </div>
        {sc.candidates.length > 1 ? <button className="su-iconbtn" onClick={onSwap} aria-label="Swap"><Shuffle size={15} /></button> : null}
        <button className="su-iconbtn" onClick={onToggleRemove} aria-label="Remove"><Trash2 size={15} /></button>
      </div>
      {canRetime ? (
        <div style={{ paddingLeft: 56 }}>
          <div style={{ fontSize: 11, color: "var(--su-ink-500)", marginBottom: 2 }}>Which part of the clip plays</div>
          <input
            type="range" min={0} max={maxStartMs} step={100}
            value={Math.min(sc.sourceStartMs ?? 0, maxStartMs)}
            onChange={(e) => onRetime(Number(e.target.value))}
            style={{ width: "100%" }}
          />
        </div>
      ) : null}
    </div>
  );
}

// Music sheet body — catalog browsing (grouped by category) + this session's own uploads +
// volume/ducking controls once a track is picked. Shared by Setup (pre-render) and Fine-tune
// (post-render); the Sheet's footer button decides what "confirm" means in each context.
function MusicPicker({ catalog, userTracks, selection, onSelectTrack, onChange, onUploadClick, uploading }: {
  catalog: { tracks: MusicTrack[]; categories: MusicCategory[]; duckingPresets: string[] };
  userTracks: UserMusicTrack[];
  selection: MusicSelection | null;
  // Tapping a track IS the play/stop action (see selectMusicTrack in MobileApp) — same track
  // again = stop, a different track = switch, immediately. No separate preview step: selecting
  // a track and auditioning it are now the same action.
  onSelectTrack: (trackId: string, url: string) => void;
  onChange: (sel: MusicSelection | null) => void; // volume slider / ducking preset tweaks only
  onUploadClick: () => void;
  uploading: boolean;
}) {
  const rowStyle = (active: boolean): React.CSSProperties => ({
    flex: 1, textAlign: "left", padding: "8px 10px", borderRadius: 8, fontSize: 13, fontWeight: 600,
    border: active ? "2px solid var(--su-indigo)" : "1px solid var(--su-line-strong)",
    background: "#fff", color: "var(--su-ink-900)", cursor: "pointer",
  });
  return (
    <div className="su-stack" style={{ gap: 14 }}>
      <button className="su-btn ghost" disabled={uploading} onClick={onUploadClick}>
        {uploading ? <Loader2 size={16} className="su-spin" style={{ border: 0 }} /> : <UploadCloud size={16} />}
        {uploading ? "Uploading…" : "Upload your own track"}
      </button>
      {!selection ? <p className="su-sub" style={{ margin: 0 }}>No music yet — tap a track below to play it with your video.</p> : null}
      {userTracks.length > 0 && (
        <div>
          <div className="su-label">Your uploads</div>
          <div className="su-stack" style={{ gap: 6, marginTop: 6 }}>
            {userTracks.map((t) => (
              <div key={t.key} style={{ display: "flex", gap: 8, alignItems: "center" }}>
                <button style={rowStyle(selection?.trackId === t.key)} onClick={() => onSelectTrack(t.key, t.url)}>{t.label}</button>
              </div>
            ))}
          </div>
        </div>
      )}
      {catalog.categories.map((cat) => (
        <div key={cat.id}>
          <div className="su-label">{cat.label}</div>
          <div className="su-stack" style={{ gap: 6, marginTop: 6 }}>
            {catalog.tracks.filter((t) => t.category === cat.id).map((t) => (
              <div key={t.id} style={{ display: "flex", gap: 8, alignItems: "center" }}>
                <button style={rowStyle(selection?.trackId === t.id)} onClick={() => onSelectTrack(t.id, t.url)}>{t.label}</button>
              </div>
            ))}
          </div>
        </div>
      ))}
      {selection && (
        <div className="su-card su-stack" style={{ gap: 10 }}>
          <div className="su-label">Volume &amp; ducking</div>
          <input
            type="range" min={-24} max={0} step={1} value={selection.gainDb}
            onChange={(e) => onChange({ ...selection, gainDb: Number(e.target.value) })}
            style={{ width: "100%" }}
          />
          <div style={{ display: "flex", gap: 6 }}>
            {catalog.duckingPresets.map((d) => (
              <button
                key={d}
                onClick={() => onChange({ ...selection, ducking: d as MusicSelection["ducking"] })}
                style={{
                  flex: 1, padding: "6px 0", borderRadius: 8, fontSize: 12, fontWeight: 700,
                  border: selection.ducking === d ? "2px solid var(--su-indigo)" : "1px solid var(--su-line-strong)",
                  background: selection.ducking === d ? "var(--su-indigo)" : "#fff",
                  color: selection.ducking === d ? "#fff" : "var(--su-ink-900)",
                }}
              >
                {DUCKING_LABELS[d] ?? d}
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

// One-line human-readable summary of a chat action, for the "Proposed changes" confirm list —
// looks up real labels (moment description, sound name, track name) instead of showing raw ids.
function describeChatAction(
  a: ChatAction, moments: Moment[],
  sfxCatalog: { sounds: SfxSound[] }, musicCatalog: { tracks: MusicTrack[] }, userMusicTracks: UserMusicTrack[],
): string {
  const momentLabel = (id: string) => moments.find((m) => m.momentId === id)?.primaryQuery ?? id;
  switch (a.tool) {
    case "swap_broll_clip":
      return `Swap the clip on "${momentLabel(a.momentId)}"`;
    case "set_broll_removed":
      return `${a.removed ? "Remove" : "Restore"} the clip on "${momentLabel(a.momentId)}"`;
    case "retime_broll_clip":
      return `Retime "${momentLabel(a.momentId)}" to start at ${(a.sourceStartMs / 1000).toFixed(1)}s into the source`;
    case "set_caption_style":
      return `Caption style → ${a.styleId}`;
    case "set_toggle":
      return `${a.on ? "Turn on" : "Turn off"} ${a.key}`;
    case "set_color_grade_look":
      return `Colour look → ${a.look} (${Math.round(a.intensity * 100)}%)`;
    case "add_sfx": {
      const snd = sfxCatalog.sounds.find((s) => s.id === a.soundId);
      return `Add "${snd?.label ?? a.soundId}" at ${(a.atMs / 1000).toFixed(1)}s`;
    }
    case "remove_sfx":
      return "Remove a placed sound effect";
    case "set_music": {
      const trk = musicCatalog.tracks.find((t) => t.id === a.trackId) ?? userMusicTracks.find((t) => t.key === a.trackId);
      return `Music → ${trk?.label ?? a.trackId} (ducking: ${a.ducking})`;
    }
    default:
      return "Unknown change";
  }
}




// ---- real audio waveform ---------------------------------------------------------------------
// Decodes the reel's audio once and downsamples it to a bar array for the EditSurface bar. The Trim
// bar draws transcript SEGMENTS, which reads like a waveform but only says "speech / not speech" --
// an actual envelope also shows emphasis and where the pauses really are, which is what you aim at.
//
// decodeAudioData on a fetched ArrayBuffer is safe here. The Web Audio problem this app hit before
// was createMediaElementSource, which permanently reroutes a media element's output and left the
// video silent; nothing in this path touches a media element.
function useWaveform(src: string, bars = 180): number[] | null {
  const [wave, setWave] = useState<number[] | null>(null);
  useEffect(() => {
    if (!src) { setWave(null); return; }
    let dead = false;
    let ctx: AudioContext | null = null;
    (async () => {
      try {
        const buf = await (await fetch(src)).arrayBuffer();
        if (dead) return;
        ctx = new AudioContext();
        const audio = await ctx.decodeAudioData(buf);
        if (dead) return;
        const ch = audio.getChannelData(0);
        const per = Math.max(1, Math.floor(ch.length / bars));
        const out: number[] = [];
        for (let i = 0; i < bars; i++) {
          let peak = 0;
          for (let j = i * per; j < Math.min((i + 1) * per, ch.length); j += 16) {
            const v = Math.abs(ch[j]);
            if (v > peak) peak = v;
          }
          out.push(peak);
        }
        const max = Math.max(...out, 0.0001);
        setWave(out.map((v) => v / max));       // normalised, so quiet recordings still read
      } catch {
        setWave(null);                          // fall back to the segment blocks
      } finally {
        void ctx?.close();
      }
    })();
    return () => { dead = true; };
  }, [src, bars]);
  return wave;
}

// ---- shared editing surface -----------------------------------------------------------------
// Player + timeline + playhead + "add here", used by every sheet that edits things positioned in
// time. Trim and SFX had each solved half of this independently -- Trim had the timeline, SFX had
// add-at-playhead -- while Cards, Zooms and B-roll had neither, so each sheet taught a different
// interaction for the same job. One surface means one thing to learn: scrub, tap an item to edit it,
// tap + to put a new one where you are.
//
// Spans and points share the bar because our items are both: zooms/cards/B-roll occupy a range,
// SFX hits are instants. Speech landmarks come from the transcript (the same blocks the Trim bar
// draws) so there is something to aim at instead of a featureless strip.
function EditSurface({
  src, durationMs, spans, points, selectedId, segments, addLabel, videoRef, videoStyle,
  onSelect, onAdd, onPlayhead,
}: {
  src: string;
  durationMs: number;
  spans: { id: string; startMs: number; endMs: number; muted?: boolean }[];
  points?: { id: string; atMs: number }[];
  selectedId?: string | null;
  segments?: { segIndex: number; startMs: number; endMs: number }[];
  addLabel: string;
  videoRef?: React.RefObject<HTMLVideoElement | null>;
  videoStyle?: React.CSSProperties;
  onSelect?: (id: string) => void;
  onAdd?: (atMs: number) => void;
  onPlayhead?: (ms: number) => void;
}) {
  const ownRef = useRef<HTMLVideoElement | null>(null);
  const vref = videoRef ?? ownRef;
  const wave = useWaveform(src);
  const barRef = useRef<HTMLDivElement | null>(null);
  const [ms, setMs] = useState(0);
  const dur = Math.max(durationMs, 1);
  const pct = (v: number) => `${Math.min(100, Math.max(0, (v / dur) * 100))}%`;
  const seek = (e: React.MouseEvent<HTMLDivElement>) => {
    const r = barRef.current?.getBoundingClientRect();
    if (!r) return;
    const at = Math.min(1, Math.max(0, (e.clientX - r.left) / r.width)) * dur;
    // A tap that lands on an item selects it; empty bar just moves the playhead.
    const hit = spans.find((sp) => at >= sp.startMs && at <= sp.endMs);
    if (hit && onSelect) onSelect(hit.id);
    if (vref.current) vref.current.currentTime = at / 1000;
    setMs(at);
    onPlayhead?.(at);
  };
  return (
    <div className="su-stack" style={{ gap: 8 }}>
      {src ? (
        <div style={{ width: "100%", borderRadius: 10, overflow: "hidden", background: "#000" }}>
          <video
            ref={vref} src={src} controls playsInline
            style={{ width: "100%", display: "block", ...videoStyle }}
            onTimeUpdate={() => {
              const v = vref.current;
              if (!v) return;
              setMs(v.currentTime * 1000);
              onPlayhead?.(v.currentTime * 1000);
            }}
          />
        </div>
      ) : <p className="muted" style={{ margin: 0 }}>Render the reel first to see it here.</p>}

      <div
        ref={barRef} onClick={seek}
        style={{ position: "relative", height: 40, background: "var(--su-cream-300)",
                 borderRadius: 8, overflow: "hidden", cursor: "pointer", touchAction: "manipulation" }}
      >
        {wave ? (
          <div style={{ position: "absolute", inset: 0, display: "flex", alignItems: "center",
                        gap: 1, padding: "0 1px", pointerEvents: "none" }}>
            {wave.map((v, i) => (
              <div key={i} style={{ flex: 1, height: `${Math.max(6, v * 78)}%`,
                                    background: "var(--su-ink-300)", opacity: 0.55, borderRadius: 1 }} />
            ))}
          </div>
        ) : (segments ?? []).map((sg) => (
          <div key={`sg${sg.segIndex}`} style={{
            position: "absolute", top: 13, bottom: 13, left: pct(sg.startMs),
            width: `${Math.max(0.25, ((sg.endMs - sg.startMs) / dur) * 100)}%`,
            background: "var(--su-ink-300)", opacity: 0.5, borderRadius: 2 }} />
        ))}
        {spans.map((sp) => {
          const on = sp.id === selectedId;
          return (
            <div key={sp.id} style={{
              position: "absolute", top: on ? 0 : 5, bottom: on ? 0 : 5, left: pct(sp.startMs),
              width: `${Math.max(1.2, ((sp.endMs - sp.startMs) / dur) * 100)}%`,
              background: sp.muted ? "var(--su-ink-300)" : "var(--su-periwinkle)",
              opacity: sp.muted ? 0.5 : on ? 1 : 0.72,
              border: on ? "2px solid var(--su-ink-900)" : "none", borderRadius: 4 }} />
          );
        })}
        {(points ?? []).map((pt) => (
          <div key={pt.id} style={{
            position: "absolute", top: 6, left: pct(pt.atMs), width: 8, height: 8,
            marginLeft: -4, borderRadius: "50%", background: "var(--su-indigo)" }} />
        ))}
        <div style={{ position: "absolute", top: 0, bottom: 0, width: 2,
                      background: "var(--su-ink-900)", left: pct(ms) }} />
      </div>

      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <span className="su-sub" style={{ flex: 1 }}>{fmtMsPrecise(ms)}</span>
        {onAdd ? (
          <button className="su-chip" style={{ minHeight: 38 }} onClick={() => onAdd(Math.round(ms))}>
            <Plus size={14} /> {addLabel}
          </button>
        ) : null}
      </div>
    </div>
  );
}

// ---- graphic-card preview -------------------------------------------------------------------
// A CSS twin of the Pillow card renderer (_card_bg / _card_content in render_broll_ffmpeg.py), the
// same approach CaptionSample already takes for the ASS caption renderer. Values are copied from
// that renderer rather than re-invented -- the 3-stop gradient, the amber value colour, the 10%/22%
// /14% vertical anchors and the relative type sizes -- and everything is expressed as a fraction of
// the box so one component serves any preview size.
//
// A twin can drift from the renderer, so this is a layout-and-look check, not a pixel promise. It is
// still the difference between choosing a template blind and seeing it: cards carry TEXT, and a
// too-long headline or a number that overflows is exactly what you cannot judge from a form field.
const CARD_TOKENS: Record<string, { bg: string; glow: string; sub: string }> = {
  ink:   { bg: "linear-gradient(157deg,#0c1116,#11171f 55%,#090d12)", glow: "rgba(255,194,51,0.16)", sub: "rgba(255,255,255,0.75)" },
  amber: { bg: "linear-gradient(157deg,#241809,#35230b 55%,#180f05)", glow: "rgba(255,194,51,0.26)", sub: "rgba(255,236,204,0.85)" },
  night: { bg: "linear-gradient(157deg,#0a1026,#182253 55%,#080c1c)", glow: "rgba(120,150,255,0.20)", sub: "rgba(214,224,255,0.82)" },
};

function CardSample({ card, width }: { card: BrollCard; width: number }) {
  const H = width * (1920 / 1080);
  const tok = CARD_TOKENS[card.style ?? "ink"] ?? CARD_TOKENS.ink;
  const px = (v: number) => (v / 1920) * H;                 // renderer units (1080x1920) -> box
  const headline = (card.headline ?? "").trim();
  const shadow = "0 2px 6px rgba(0,0,0,0.55)";
  const value = (card.value ?? "").trim() || "—";
  // same size ladder the renderer uses: shorter numbers are drawn bigger
  const vs = value.length <= 2 ? 420 : value.length <= 4 ? 340 : value.length <= 6 ? 260 : 200;
  const items = (card.items ?? []).filter(Boolean).slice(0, 4);
  return (
    <div style={{ position: "relative", width, height: H, borderRadius: 8, overflow: "hidden",
                  background: tok.bg, flexShrink: 0 }}>
      {/* focal glow — the renderer blurs an ellipse at 34% height */}
      <div style={{ position: "absolute", left: "-12%", right: "-12%", top: "34%",
                    height: H * 0.5, transform: "translateY(-50%)", borderRadius: "50%",
                    background: tok.glow, filter: `blur(${Math.max(6, px(160))}px)` }} />
      {card.cardType === "stat" ? (
        <div style={{ position: "absolute", top: px(0.22 * 1920), left: 0, right: 0, textAlign: "center" }}>
          <div style={{ fontFamily: '"PoppinsCap",system-ui,sans-serif', fontWeight: 800,
                        fontSize: px(vs), lineHeight: 1, color: "#FFC233", textShadow: shadow }}>
            {value}
          </div>
          {headline ? (
            <div style={{ marginTop: px(20), fontFamily: '"PoppinsCap",system-ui,sans-serif',
                          fontWeight: 800, fontSize: px(52), letterSpacing: px(3),
                          color: tok.sub, textShadow: shadow }}>
              {headline.toUpperCase()}
            </div>
          ) : null}
        </div>
      ) : card.cardType === "list" ? (
        <div style={{ position: "absolute", top: px(0.14 * 1920), left: `${(0.11 * 100).toFixed(0)}%`,
                      right: "8%", textAlign: "left" }}>
          {headline ? (
            <div style={{ fontFamily: '"PoppinsCap",system-ui,sans-serif', fontWeight: 800,
                          fontSize: px(62), color: "#fff", textShadow: shadow, marginBottom: px(34) }}>
              {headline}
            </div>
          ) : null}
          {items.map((it, i) => (
            <div key={i} style={{ fontFamily: '"PoppinsCap",system-ui,sans-serif', fontWeight: 800,
                                  fontSize: px(56), color: tok.sub, textShadow: shadow,
                                  marginBottom: px(22) }}>
              {it}
            </div>
          ))}
        </div>
      ) : (
        <div style={{ position: "absolute", top: px(0.10 * 1920), left: "8%", right: "8%",
                      textAlign: "center", fontFamily: '"PoppinsCap",system-ui,sans-serif',
                      fontWeight: 800, fontSize: px(78), lineHeight: 1.15, color: "#fff",
                      textShadow: shadow }}>
          {headline || "Your text here"}
        </div>
      )}
    </div>
  );
}

// ---- caption sample (faithful preview: Poppins + colors + stroke + box + motion) ----------
function CaptionSample({ s, fontPx, phrase, activeIdx, preview = false }: {
  s: CaptionStyleInfo; fontPx: number; phrase: string[]; activeIdx: number; preview?: boolean;
}) {
  const glow = s.glowBlur > 0;
  const hasHighlight = s.activeColor.toLowerCase() !== s.inactiveColor.toLowerCase() || glow;
  const tc = (t: string) => (s.uppercase === "upper" ? t.toUpperCase() : s.uppercase === "lower" ? t.toLowerCase() : t);
  const stroke = (s.outlineWidth * fontPx) / 92;
  const nowrap = preview && (s.karaokeFill || s.typewriter);
  const glowShadow = glow
    ? `0 0 ${(fontPx * 0.12).toFixed(1)}px ${s.glowColor}, 0 0 ${(fontPx * 0.26).toFixed(1)}px ${s.glowColor}, 0 0 ${(fontPx * 0.46).toFixed(1)}px ${s.glowColor}`
    : "";
  const blockStyle = (extra: React.CSSProperties = {}): React.CSSProperties => ({
    fontFamily: '"PoppinsCap", system-ui, sans-serif',
    fontWeight: 800, fontSize: fontPx, lineHeight: 1.15, letterSpacing: "-0.01em",
    display: "inline-flex", flexWrap: nowrap ? "nowrap" : "wrap", whiteSpace: nowrap ? "nowrap" : "normal",
    justifyContent: "center", gap: `0 ${Math.round(fontPx * 0.16)}px`,
    ...(stroke > 0.3 ? { WebkitTextStroke: `${stroke.toFixed(2)}px ${s.outlineColor}`, paintOrder: "stroke fill" as const } : {}),
    textShadow: "0 2px 6px rgba(0,0,0,0.45)",
    ...extra,
  });
  const words = (colorAt: (i: number) => string, activeAt: (i: number) => boolean) =>
    phrase.map((w, i) => {
      const active = activeAt(i);
      return (
        <span key={i} style={{
          color: colorAt(i), display: "inline-block", whiteSpace: "nowrap",
          transform: active ? "scale(1.06)" : "none", transition: "transform 130ms ease, color 130ms ease",
          ...(active && glow ? { textShadow: glowShadow, animation: "capGlow 1.6s ease-in-out infinite" } : {}),
        }}>{tc(w)}</span>
      );
    });
  let inner: React.ReactNode;
  if (s.karaokeFill) {
    inner = (
      <span style={{ position: "relative", display: "inline-flex" }}>
        <span style={blockStyle()}>{words(() => s.inactiveColor, () => false)}</span>
        <span style={blockStyle({ position: "absolute", top: 0, left: 0, right: 0, bottom: 0, animation: "capWipe 2.6s linear infinite" })}>
          {words(() => s.fillColor, () => false)}
        </span>
      </span>
    );
  } else if (s.typewriter) {
    const steps = Math.max(6, phrase.join(" ").length);
    inner = <span style={blockStyle({ animation: `capType 2.8s steps(${steps}) infinite` })}>{words(() => s.inactiveColor, () => false)}</span>;
  } else {
    inner = <span style={blockStyle()}>{words((i) => (hasHighlight && i === activeIdx ? s.activeColor : s.inactiveColor), (i) => hasHighlight && i === activeIdx)}</span>;
  }
  const body: React.ReactNode = s.fadeMs > 0
    ? <span style={{ display: "inline-block", animation: "capFade 2.8s ease-in-out infinite" }}>{inner}</span>
    : inner;
  if (s.boxStyle === "box") {
    const h = s.boxColor.replace("#", "");
    const [r, g, b] = [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16) || 0);
    return (
      <span style={{ background: `rgba(${r},${g},${b},${s.boxOpacity})`, padding: `${Math.round(fontPx * 0.14)}px ${Math.round(fontPx * 0.3)}px`, borderRadius: Math.round(fontPx * 0.14), display: "inline-block" }}>{body}</span>
    );
  }
  return body;
}

// ---- full caption controls (style + size slider + position slider + live preview) ----------
function CaptionStyleControls({ reg, value, onChange }: {
  reg: CaptionRegistry | null; value: CaptionUI | null; onChange: (v: CaptionUI) => void;
}) {
  const [tick, setTick] = useState(0);
  useEffect(() => {
    const id = window.setInterval(() => setTick((t) => t + 1), 350);
    return () => window.clearInterval(id);
  }, []);
  if (!reg) return <p className="su-sub">Loading styles…</p>;
  const selId = value?.styleId ?? reg.defaultStyleId;
  const cur = reg.styles.find((s) => s.id === selId) ?? reg.styles[0];
  const pick = (s: CaptionStyleInfo) => {
    if (!s.available) return;
    onChange({ styleId: s.id, sizePx: s.defaultSizePx, bottomPercent: s.defaultBottomPercent });
  };
  const lbl = { fontSize: 11, fontWeight: 700 as const, letterSpacing: "0.13em", textTransform: "uppercase" as const, color: "var(--su-ink-500)" };
  const pos = value?.bottomPercent ?? cur.defaultBottomPercent;
  const sizePx = value?.sizePx ?? cur.defaultSizePx;
  const sizeMin = Math.round(cur.defaultSizePx * 0.6);
  const sizeMax = Math.round(cur.defaultSizePx * 1.6);
  const sizePct = Math.round((sizePx / cur.defaultSizePx) * 100);
  return (
    <div className="su-stack" style={{ gap: 12 }}>
      <div style={{ display: "flex", gap: 8, overflowX: "auto", paddingBottom: 4 }}>
        {reg.styles.map((s) => {
          const on = value != null && s.id === value.styleId && s.available;
          return (
            <button
              key={s.id}
              disabled={!s.available}
              onClick={() => pick(s)}
              style={{
                flex: "0 0 auto", width: 132, padding: 8, borderRadius: 12, textAlign: "left",
                border: on ? "2px solid var(--su-accent)" : "1px solid var(--su-line-strong)",
                background: on ? "var(--su-red-100)" : "#fff",
                opacity: s.available ? 1 : 0.5, cursor: s.available ? "pointer" : "not-allowed",
              }}
            >
              <div style={{ height: 46, borderRadius: 8, marginBottom: 6, overflow: "hidden", background: "linear-gradient(150deg,#3a2320,#1c110f)", display: "flex", alignItems: "center", justifyContent: "center" }}>
                {s.available ? <CaptionSample s={s} fontPx={13} phrase={CARD_PHRASE} activeIdx={CARD_ACTIVE} /> : <span style={{ fontSize: 11, color: "var(--su-ink-300)", fontWeight: 600 }}>coming soon</span>}
              </div>
              <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
                <span style={{ fontWeight: 700, fontSize: 13, color: "var(--su-ink-900)" }}>{s.label}</span>
                <span style={{ fontSize: 10, color: "var(--su-ink-500)" }}>{s.available ? s.uppercase : ""}</span>
              </div>
            </button>
          );
        })}
      </div>

      <div>
        <div style={lbl}>Preview</div>
        <div style={{ position: "relative", height: 236, aspectRatio: "9 / 16", margin: "6px auto 0", borderRadius: 18, overflow: "hidden", boxShadow: "0 12px 30px -14px rgba(0,0,0,.55)", background: "linear-gradient(165deg,#3a2320,#1c110f 70%)" }}>
          <div style={{ position: "absolute", left: 8, right: 8, bottom: `${pos}%`, display: "flex", justifyContent: "center" }}>
            <CaptionSample s={cur} fontPx={Math.max(9, Math.round(sizePx * 0.12))} phrase={PREVIEW_PHRASE} activeIdx={tick % PREVIEW_PHRASE.length} />
          </div>
        </div>
        <div style={{ fontSize: 10, color: "var(--su-ink-500)", marginTop: 4, textAlign: "center" }}>{cur.label} · size {sizePct}% · {pos}% from bottom</div>
      </div>

      <div>
        <div style={{ ...lbl, display: "flex", justifyContent: "space-between" }}>
          <span>Size</span>
          <span style={{ fontWeight: 500, color: "var(--su-ink-500)", textTransform: "none" }}>{sizePct}%</span>
        </div>
        <input type="range" min={sizeMin} max={sizeMax} step={1} value={sizePx} disabled={!value}
          onChange={(e) => value && onChange({ ...value, sizePx: Number(e.target.value) })}
          style={{ width: "100%", marginTop: 8, accentColor: "var(--su-accent)", opacity: value ? 1 : 0.5 }} />
      </div>

      <div>
        <div style={{ ...lbl, display: "flex", justifyContent: "space-between" }}>
          <span>Position</span>
          <span style={{ fontWeight: 500, color: "var(--su-ink-500)", textTransform: "none" }}>{pos}% from bottom</span>
        </div>
        <input type="range" min={reg.position.min} max={reg.position.max} step={1} value={pos} disabled={!value}
          onChange={(e) => value && onChange({ ...value, bottomPercent: Number(e.target.value) })}
          style={{ width: "100%", marginTop: 8, accentColor: "var(--su-accent)", opacity: value ? 1 : 0.5 }} />
      </div>

      {!value ? <p className="su-sub" style={{ fontSize: 11 }}>Using the default ({cur.label}). Pick a style to customize size &amp; position.</p> : null}
    </div>
  );
}

// ---- Create a reel (upload) ----------------------------------------------------------------
function CreateScreen({ hover, setHover, fileRef, onPick, hasFile }: {
  hover: boolean; setHover: (b: boolean) => void; fileRef: React.RefObject<HTMLInputElement | null>; onPick: (f: File) => void; hasFile: boolean;
}) {
  return (
    <>
      <div className="su-pattern su-pad" style={{ paddingTop: 20, paddingBottom: 6, display: "flex", alignItems: "center", gap: 12 }}>
        <span className="su-eyebrow">Create a reel</span>
      </div>
      <div className="su-pad" style={{ paddingTop: 6, paddingBottom: 4 }}>
        <h1 className="su-h1">Add your clip</h1>
        <p className="su-sub" style={{ marginTop: 8 }}>We'll caption it, add B-roll &amp; zooms, and hand you a post-ready vertical reel.</p>
      </div>
      <div className="su-scroll su-pad" style={{ display: "flex", flexDirection: "column", justifyContent: "center", paddingTop: 8 }}>
        <div
          className={`su-drop${hover ? " hover" : ""}`}
          onClick={() => fileRef.current?.click()}
          onDragOver={(e) => { e.preventDefault(); setHover(true); }}
          onDragLeave={() => setHover(false)}
          onDrop={(e) => { e.preventDefault(); setHover(false); const f = e.dataTransfer.files?.[0]; if (f) onPick(f); }}
        >
          <div className="su-ico" style={{ background: "var(--su-yellow)", color: "var(--su-ink-900)", width: 54, height: 54, borderRadius: 16 }}>
            <UploadCloud size={26} />
          </div>
          <div style={{ fontWeight: 700, fontSize: 17, color: "var(--su-ink-900)" }}>Drop a video, or tap to pick</div>
          <div className="su-sub">MP4 / MOV · up to a few minutes</div>
          <input ref={fileRef} type="file" accept="video/*" style={{ display: "none" }}
            onChange={(e) => { const f = e.target.files?.[0]; if (f) onPick(f); e.target.value = ""; }} />
        </div>
      </div>
      <div className="su-footer">
        <button className="su-btn" disabled={!hasFile} onClick={() => fileRef.current?.click()}>Pick a video to continue</button>
      </div>
    </>
  );
}

// ---- Saved · share -------------------------------------------------------------------------
function SavedScreen({ outputName, projectId, onCreateAnother }: { outputName: string; projectId?: string; onCreateAnother: () => void }) {
  // Was previously fully non-functional: WhatsApp/Instagram were dead buttons with no onClick,
  // and /api/publish-kit (LLM title/description/hashtags from the transcript) already existed
  // server-side but was never called from the mobile app. Both are wired here now.
  const [kit, setKit] = useState<PublishKit | null>(null);
  const [kitError, setKitError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [shareBusy, setShareBusy] = useState(false);

  useEffect(() => {
    let cancelled = false;
    fetchPublishKit(projectId)
      .then((d) => {
        if (!cancelled) setKit(d);
      })
      .catch((e: unknown) => {
        if (!cancelled) setKitError(e instanceof Error ? e.message : "Couldn't draft a caption");
      });
    return () => {
      cancelled = true;
    };
  }, [projectId]);

  const captionText = kit ? `${kit.title}\n\n${kit.description}\n\n${kit.hashtags.map((h) => `#${h}`).join(" ")}` : "";

  const copyCaption = async () => {
    if (!captionText) return;
    try {
      await navigator.clipboard.writeText(captionText);
      setCopied(true);
      setTimeout(() => setCopied(false), 1800);
    } catch {
      // Clipboard permission denied/unavailable — the text is still visible to select by hand.
    }
  };

  // No web platform lets us deep-link "share to WhatsApp" / "share to Instagram" specifically
  // with a video file attached — the Web Share API opens the OS's native share sheet, which
  // includes whichever apps are installed (WhatsApp/Instagram among them). Both buttons trigger
  // the same share; falls back to the existing plain download when the API/file-sharing isn't
  // available (desktop browsers, older mobile browsers).
  const shareReel = async () => {
    if (!outputName || shareBusy) return;
    setShareBusy(true);
    try {
      const nav = navigator as Navigator & { canShare?: (data?: ShareData) => boolean };
      if (typeof nav.share === "function") {
        try {
          const res = await fetch(`/api/result/${outputName}`);
          const blob = await res.blob();
          const file = new File([blob], outputName, { type: blob.type || "video/mp4" });
          const shareData: ShareData = { files: [file], text: captionText || undefined, title: kit?.title || undefined };
          if (nav.canShare && !nav.canShare(shareData)) throw new Error("file sharing unsupported");
          await nav.share(shareData);
          return;
        } catch (err) {
          if (err instanceof Error && err.name === "AbortError") return; // user closed the share sheet — not a failure
          // fall through to the download fallback below
        }
      }
      const a = document.createElement("a");
      a.href = `/api/result/${outputName}`;
      a.download = outputName;
      a.click();
    } finally {
      setShareBusy(false);
    }
  };

  return (
    <>
      <div className="su-pattern su-fill" style={{ padding: 26 }}>
        <div className="su-center-block" style={{ display: "flex", flexDirection: "column", alignItems: "center", textAlign: "center", gap: 6 }}>
          <div className="su-checkbig"><div className="in"><Check size={30} strokeWidth={3} /></div></div>
          <h1 className="su-h1" style={{ marginTop: 14 }}>Saved to your library</h1>
          <p className="su-sub" style={{ maxWidth: 300 }}>Your reel is in your camera roll and your superUP library. Go get those clicks.</p>

          <div className="su-card su-stack" style={{ marginTop: 16, width: "100%", textAlign: "left", gap: 8 }}>
            <div className="su-label">Caption &amp; hashtags</div>
            {kit ? (
              <>
                <div style={{ fontWeight: 700, fontSize: 14, color: "var(--su-ink-900)" }}>{kit.title}</div>
                <div style={{ fontSize: 13, color: "var(--su-ink-700)" }}>{kit.description}</div>
                <div style={{ fontSize: 13, color: "var(--su-plum)" }}>{kit.hashtags.map((h) => `#${h}`).join(" ")}</div>
                <button className="su-action" style={{ alignSelf: "flex-start", marginTop: 4 }} onClick={() => void copyCaption()}>
                  <Copy size={14} /> {copied ? "Copied!" : "Copy caption"}
                </button>
              </>
            ) : kitError ? (
              <p className="muted">Couldn't draft a caption — you can still share the video below.</p>
            ) : (
              <p className="muted">Drafting a caption…</p>
            )}
          </div>

          <div className="su-share" style={{ marginTop: 18 }}>
            <button disabled={shareBusy} onClick={() => void shareReel()}>
              <span className="ic" style={{ background: "#25D366" }}><MessageCircle size={24} /></span>WhatsApp
            </button>
            <button disabled={shareBusy} onClick={() => void shareReel()}>
              <span className="ic" style={{ background: "linear-gradient(135deg,#F58529,#DD2A7B,#8134AF)" }}><Camera size={24} /></span>Instagram
            </button>
            {outputName ? (
              <a href={`/api/result/${outputName}`} download={outputName} style={{ textDecoration: "none" }}>
                <button><span className="ic" style={{ background: "var(--su-ink-800)" }}><MoreHorizontal size={24} /></span>More</button>
              </a>
            ) : (
              <button><span className="ic" style={{ background: "var(--su-ink-800)" }}><MoreHorizontal size={24} /></span>More</button>
            )}
          </div>
        </div>
      </div>
      <div className="su-footer su-stack">
        <button className="su-btn" onClick={onCreateAnother}><Plus size={18} /> Create another reel</button>
      </div>
    </>
  );
}

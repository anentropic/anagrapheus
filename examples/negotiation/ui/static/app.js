// The negotiation, watched. One EventSource, one state object, and a render pass
// scheduled per animation frame. Everything is built with textContent: the payloads
// are model output and must never be parsed as HTML.
"use strict";

const $ = (id) => document.getElementById(id);

const DOMAIN_EVENTS = new Set(["ClauseTabled", "PositionStated", "SideRemarkOverheard", "RoundClosed"]);
const NAMES = { eu: "EU", uk: "UK" };
const name = (p) => NAMES[p] ?? p;

// ---- state ------------------------------------------------------------------------------

const newStage = () => ({ frames: [], logs: [], events: [], steps: null });

const state = {
  run: null,
  started: 0,
  status: "idle",
  stage: "live",
  pos: { live: 0, replay: 0 },
  follow: true,
  playing: null,
  stages: { live: newStage(), replay: newStage() },
  filters: { model: true, round: true, pins: true, store: false, other: true },
  // Story by default: the workings assume you already know what a scope is.
  mode: localStorage.getItem("mode") === "workings" ? "workings" : "story",
};

function setMode(mode) {
  state.mode = mode;
  localStorage.setItem("mode", mode);
  if (mode === "story") {
    // Land on the end of the current beat, so the narration describes a finished step.
    const stage = state.stages[state.stage];
    if (stage.frames.length) state.pos[state.stage] = beatAt(beats(stage), state.pos[state.stage]).beat.last;
  }
  window.scrollTo(0, 0);
  schedule();
}

// DOM nodes built once per log record / event, then shown or hidden as the frame moves,
// so an expanded model call stays expanded while scrubbing.
let logNodes = [];
let eventNodes = [];

function reset(message) {
  state.run = message.run;
  state.started = message.t;
  state.status = "running";
  state.stage = "live";
  state.pos = { live: 0, replay: 0 };
  state.stages = { live: newStage(), replay: newStage() };
  logNodes = [];
  eventNodes = [];
  $("log").replaceChildren();
  $("events").replaceChildren();
  $("story").replaceChildren();
  $("verdict").hidden = true;
  $("run-error").hidden = true;
  if (document.activeElement !== $("models")) {
    $("models").value = message.specs.filter((s) => !s.startsWith("scripted")).join(" ");
  }
}

// ---- the stream -------------------------------------------------------------------------

function connect() {
  const source = new EventSource("/events");
  source.onmessage = (e) => receive(JSON.parse(e.data));
}

function receive(m) {
  if (m.type === "run") {
    reset(m);
  } else if (state.run === null) {
    return; // history from before any run this page knows about
  } else if (m.type === "frame") {
    const stage = state.stages[m.stage];
    stage.frames.push(m);
    for (const ev of m.events) stage.events.push({ ...ev, frame: m.index });
    for (const ev of m.events) addEventRow(m.stage, ev, m.index);
    if (state.follow) {
      if (m.stage !== state.stage) state.stage = m.stage;
      state.pos[m.stage] = m.index;
    }
  } else if (m.type === "log") {
    state.stages[m.stage].logs.push(m);
    addLogNode(m);
  } else if (m.type === "steps") {
    state.stages[m.stage].steps = m.steps;
    renderStory();
  } else if (m.type === "done") {
    state.status = "done";
    $("verdict").textContent = "✓ " + m.verdict;
    $("verdict").hidden = false;
    if (m.n < history) rewind();
  } else if (m.type === "failed") {
    state.status = "failed";
    $("run-error").textContent = m.error + (m.trace ? "\n\n" + m.trace : "");
    $("run-error").hidden = false;
    if (m.n < history) rewind();
  }
  schedule();
}

// How many messages the stream held when this page loaded. A run that finished inside
// that history is one to read from the start, not one to watch: following it would
// leave the page on the replay's last frame, the least useful place to begin.
let history = 0;

function rewind() {
  state.follow = false;
  state.stage = "live";
  for (const s of ["live", "replay"]) {
    const frames = state.stages[s].frames;
    state.pos[s] = state.mode === "story" && frames.length ? beats(state.stages[s])[0].last : 0;
  }
}

let pending = false;
function schedule() {
  if (pending) return;
  pending = true;
  requestAnimationFrame(() => {
    pending = false;
    render();
  });
}

// ---- helpers ----------------------------------------------------------------------------

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === undefined || v === null || v === false) continue;
    if (k === "class") node.className = v;
    else if (k === "text") node.textContent = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else node.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children) if (c !== null && c !== undefined) node.append(c);
  return node;
}

const show = (v) => (v === null || v === undefined ? "—" : typeof v === "string" ? v : JSON.stringify(v));

function clock(t) {
  const s = Math.max(0, t - state.started);
  return `${String(Math.floor(s / 60)).padStart(2, "0")}:${(s % 60).toFixed(2).padStart(5, "0")}`;
}

/** Bring `node` into view inside `box` only: scrollIntoView would scroll the page too. */
function scrollWithin(box, node, inset = 0) {
  const b = box.getBoundingClientRect();
  const n = node.getBoundingClientRect();
  if (n.top < b.top + inset) box.scrollTop -= b.top + inset - n.top;
  else if (n.bottom > b.bottom) box.scrollTop += n.bottom - b.bottom;
}

const ACCESS = /^witnessed\((.+?), (.+)\)$/;

/** Split beliefs into the propositional ones and the access-beliefs (who witnessed what). */
function partition(beliefs) {
  const plain = [];
  const access = new Map();
  for (const b of beliefs) {
    const m = ACCESS.exec(b.label);
    if (m && b.value === true) {
      if (!access.has(m[2])) access.set(m[2], []);
      access.get(m[2]).push({ who: m[1], id: b.id });
    } else {
      plain.push(b);
    }
  }
  return { plain, access };
}

function currentFrames() {
  const stage = state.stages[state.stage];
  const pos = Math.min(state.pos[state.stage], stage.frames.length - 1);
  return { stage, pos, frame: stage.frames[pos], prev: pos > 0 ? stage.frames[pos - 1] : null };
}

function category(log) {
  const n = log.logger;
  if (n.startsWith("anagrapheus.provider")) return "model";
  if (n === "examples.negotiation.rounds") return "round";
  // A pin's draw or reuse is already on the model call's own line; commits are not.
  if (log.level === "DEBUG" && n.startsWith("anagrapheus.")) return "store";
  if (n.startsWith("anagrapheus.timeline.pins") || n.startsWith("anagrapheus.timeline.replay") || n.startsWith("anagrapheus.chronicle")) return "pins";
  if (n.startsWith("anagrapheus.")) return "store";
  return "other";
}

// ---- render -----------------------------------------------------------------------------

function render() {
  document.body.classList.toggle("mode-story", state.mode === "story");
  document.body.classList.toggle("mode-workings", state.mode === "workings");
  for (const b of document.querySelectorAll(".modes button")) {
    b.classList.toggle("active", b.dataset.mode === state.mode);
    b.setAttribute("aria-pressed", String(b.dataset.mode === state.mode));
  }
  renderStatus();
  renderTimeline();
  if (state.mode === "story") return renderPlain();
  const { frame, prev, pos, stage } = currentFrames();
  if (!frame) {
    for (const id of ["beliefs", "headline", "model-cards", "chronicle"]) $(id).replaceChildren();
    $("frame-label").textContent = state.run === null ? "no frames yet — press Run" : "waiting for the first event…";
  } else {
    const last = pos === stage.frames.length - 1;
    $("frame-label").textContent =
      `${state.stage} · frame ${pos + 1}/${stage.frames.length} · seq ${frame.seq} · ` +
      `${frame.phase || "setup"}${last && state.status === "running" ? " · live" : ""}`;
    renderBeliefs(frame, prev);
    renderMinds(frame, prev);
  }
  renderLog(pos);
  renderEvents(pos);
}

function renderStatus() {
  const s = $("status");
  s.className = `status ${state.status}`;
  s.textContent = state.status === "running" ? "running…" : state.status;
  $("run-button").disabled = state.status === "running";
  for (const tab of document.querySelectorAll(".tab")) {
    tab.classList.toggle("active", tab.dataset.stage === state.stage);
    tab.setAttribute("aria-selected", String(tab.dataset.stage === state.stage));
    tab.disabled = state.stages[tab.dataset.stage].frames.length === 0;
  }
}

function renderTimeline() {
  const { stage, pos } = currentFrames();
  const scrubber = $("scrubber");
  scrubber.max = Math.max(0, stage.frames.length - 1);
  scrubber.value = Math.max(0, pos);
  $("follow").checked = state.follow;
  $("play").querySelector("use").setAttribute("href", state.playing ? "#i-pause" : "#i-play");
  $("play").setAttribute("aria-label", state.playing ? "Pause" : "Play");

  // One chip per beat: setup, then each round's say/leak/read/close.
  const groups = beats(stage);
  const current = stage.frames[pos];
  let lastRound = null;
  const chips = groups.map((g) => {
    const [round, step] = g.phase.includes(": ") ? g.phase.split(": ") : [null, g.phase];
    const starts = round !== null && round !== lastRound;
    lastRound = round;
    const wrong = mistakes(stage.frames[g.last].snapshot);
    return el("button", {
      class: [
        "chip",
        starts ? "round-start" : "",
        current && (current.phase || "setup") === g.phase ? "current" : "",
        wrong.length ? "wrong" : "",
      ].join(" "),
      text: starts ? `${round} · ${step}` : step,
      title: [`${g.phase} — frames ${g.first + 1}–${g.last + 1}`, ...wrong.map(misjudged)].join(" — "),
      // The Story view shows a beat once it is done; the workings, as it begins.
      onclick: () => go(state.mode === "story" ? g.last : g.first),
    });
  });
  $("phases").replaceChildren(...chips);
}

function beliefRow(b, before, extraMeta) {
  const isNew = before === undefined;
  const changed = !isNew && JSON.stringify(before) !== JSON.stringify(b.value);
  return el(
    "li",
    { class: `belief${isNew ? " new" : changed ? " changed" : ""}`, title: b.id },
    el("span", { class: "label", text: b.label }),
    el("span", { class: "value" }, changed ? el("span", { class: "was", text: show(before) }) : null, show(b.value)),
    extraMeta ? el("span", { class: "meta", text: extraMeta }) : null,
  );
}

function accessList(access, previous, open) {
  if (access.size === 0) return null;
  const items = [...access.entries()].map(([event, who]) => {
    const fresh = who.some((w) => !previous.has(w.id));
    return el("li", {
      class: `${fresh ? "new" : ""}${event.includes("SideRemark") ? " remark" : ""}`,
      text: `${event} — witnessed by ${who.map((w) => name(w.who)).join(", ")}`,
    });
  });
  return el(
    "details",
    { class: "access", open: open || undefined },
    el("summary", { text: `believes who witnessed what (${access.size} event${access.size === 1 ? "" : "s"})` }),
    el("ul", {}, ...items),
  );
}

// Which access-belief lists the user has opened, by key, so they survive re-renders.
const openAccess = new Set();
function rememberOpen(node, key) {
  if (!node) return node;
  node.addEventListener("toggle", () => (node.open ? openAccess.add(key) : openAccess.delete(key)));
  return node;
}

function renderBeliefs(frame, prev) {
  const scopes = frame.snapshot.scopes;
  if (scopes.error) return $("beliefs").replaceChildren(el("div", { class: "error-text", text: scopes.error }));
  const before = new Map();
  if (prev && Array.isArray(prev.snapshot.scopes)) {
    for (const s of prev.snapshot.scopes) for (const b of s.beliefs) before.set(`${s.scope}|${b.id}`, b.value);
  }
  const parties = [...new Set(scopes.map((s) => s.party))];
  $("beliefs").replaceChildren(
    ...parties.map((party) =>
      el(
        "div",
        { class: "party" },
        el("h3", { text: name(party) }),
        ...scopes
          .filter((s) => s.party === party)
          .flatMap((s) => {
            const { plain, access } = partition(s.beliefs);
            const prevIds = new Set([...before.keys()].filter((k) => k.startsWith(s.scope + "|")).map((k) => k.split("|")[1]));
            const key = `scope:${s.scope}`;
            return [
              el("div", {
                class: `scope-title ${s.kind}`,
                text: s.kind === "held" ? "held — private, what it actually wants" : "stated — public, what it has said",
              }),
              plain.length
                ? el("ul", { class: "beliefs" }, ...plain.map((b) => beliefRow(b, before.get(`${s.scope}|${b.id}`), `from ${b.source} · ${b.stance.toLowerCase()}`)))
                : el("div", { class: "empty", text: "nothing yet" }),
              rememberOpen(accessList(access, prevIds, openAccess.has(key)), key),
            ];
          }),
      ),
    ),
  );
}

/** The snapshot's second-order checks, one per ordered pair of parties; [] before any. */
function secondOrder(snapshot) {
  return Array.isArray(snapshot.second_order) ? snapshot.second_order : [];
}

const mistakes = (snapshot) => secondOrder(snapshot).filter((e) => e.wrong);
const misjudged = (e) => `${name(e.subject)} is wrong about what ${name(e.reader)} concluded`;

function modelTitle(m) {
  const chain = [m.viewer, ...m.chain].map(name);
  if (chain.length === 2) return `${chain[0]}'s model of ${chain[1]}`;
  return `${chain[0]}'s model of ${chain[1]}'s model of ${chain[2]}`;
}

function renderMinds(frame, prev) {
  const snap = frame.snapshot;
  const checks = snap.second_order;
  if (checks && checks.error) {
    $("headline").replaceChildren(el("div", { class: "error-text", text: checks.error }));
  } else {
    // Every direction, not only the one the story stages: a real model may misjudge
    // either side's reading, both, or neither.
    $("headline").replaceChildren(
      ...secondOrder(snap).map((e) =>
        el(
          "div",
          { class: `headline${e.wrong ? " wrong" : ""}` },
          el("div", { class: "q", text: `second-order check on ${e.belief}` }),
          el(
            "div",
            { class: "pair" },
            el("span", { text: `${name(e.reader)} reads ${name(e.subject)} as` }),
            el("b", { text: show(e.reads) }),
            el("span", { text: `${name(e.subject)} imagines ${name(e.reader)} reads` }),
            el("b", { text: show(e.imagined) }),
          ),
          el("div", { class: "verdict", text: e.wrong ? `✗ ${misjudged(e)}` : "✓ the two levels agree" }),
        ),
      ),
    );
  }

  if (snap.models.error) {
    $("model-cards").replaceChildren(el("div", { class: "error-text", text: snap.models.error }));
  } else {
    const before = new Map();
    if (prev && Array.isArray(prev.snapshot.models)) {
      for (const m of prev.snapshot.models) for (const b of m.beliefs) before.set(`${m.title}|${b.id}`, b.value);
    }
    $("model-cards").replaceChildren(
      ...snap.models.map((m) => {
        const { plain, access } = partition(m.beliefs);
        const prevIds = new Set([...before.keys()].filter((k) => k.startsWith(m.title + "|")).map((k) => k.split("|")[1]));
        const key = `model:${m.title}`;
        return el(
          "div",
          { class: `model depth-${m.chain.length}` },
          el("h4", {}, modelTitle(m), m.truncated ? el("span", { class: "tag", text: "truncated at depth cap" }) : null),
          plain.length
            ? el("ul", { class: "beliefs" }, ...plain.map((b) => beliefRow(b, before.get(`${m.title}|${b.id}`))))
            : el("div", { class: "empty", text: "an empty mind — nothing it could have witnessed" }),
          rememberOpen(accessList(access, prevIds, openAccess.has(key)), key),
          el("div", { class: "evidence", text: `built from ${m.over.length} event${m.over.length === 1 ? "" : "s"} the viewer believes this mind witnessed` }),
        );
      }),
    );
  }

  const chronicle = snap.chronicle;
  if (chronicle.error) $("chronicle").replaceChildren(el("li", { class: "error-text", text: chronicle.error }));
  else if (!chronicle.length) $("chronicle").replaceChildren(el("li", { class: "empty", text: "no round has closed yet" }));
  else $("chronicle").replaceChildren(...chronicle.map((p) => el("li", { text: p })));
}

// ---- activity log -----------------------------------------------------------------------

function renderParts(message, cls) {
  return el(
    "div",
    { class: `turn ${cls}` },
    ...message.parts.map((p) =>
      el(
        "div",
        {},
        el("div", { class: "part-kind", text: p.tool_name ? `${p.part_kind} · ${p.tool_name}` : p.part_kind }),
        p.content !== undefined ? el("pre", { text: show(p.content) }) : null,
        p.args !== undefined ? el("pre", { text: typeof p.args === "string" ? p.args : JSON.stringify(p.args, null, 2) }) : null,
      ),
    ),
  );
}

function addLogNode(log) {
  const d = log.detail;
  const cat = category(log);
  const short = log.logger.replace(/^anagrapheus\./, "").replace(/^examples\.negotiation\./, "");
  let body;
  if (d && d.request) {
    body = el(
      "div",
      { class: "call" },
      el("span", { class: `badge ${d.drawn ? "drawn" : "reused"}`, text: d.drawn ? "drawn" : "reused" }),
      el("span", { class: "msg", text: `${d.model} · ${d.seconds.toFixed(2)}s · ${d.usage.input_tokens} in / ${d.usage.output_tokens} out` }),
      el(
        "details",
        {},
        el("summary", { text: `conversation (${d.request.length} message${d.request.length === 1 ? "" : "s"}) and answer` }),
        el("div", { class: "call-meta", text: `key ${d.key}` }),
        d.tools.length || d.output_tools.length ? el("div", { class: "call-meta", text: `tools: ${[...d.tools, ...d.output_tools].join(", ")}` }) : null,
        ...d.request.map((m) => renderParts(m, "request")),
        renderParts(d.response, "response"),
      ),
    );
  } else if (cat === "round") {
    body = el("span", {}, el("span", { class: "badge round", text: "round" }), el("span", { class: "msg", text: log.message }));
  } else {
    body = el("span", { class: "msg", text: log.message });
  }
  const node = el(
    "li",
    { class: log.level },
    el("span", { class: "time", text: clock(log.t) }),
    el("span", { class: "who", text: short }),
    body,
  );
  logNodes.push({ log, node, cat });
  $("log").append(node);
}

function renderLog(pos) {
  const { stage } = currentFrames();
  const atEnd = pos >= stage.frames.length - 1;
  for (const { log, node, cat } of logNodes) {
    // A record stamped with frame f happened after frame f was taken: it led to f + 1.
    const happened = log.stage === state.stage && (atEnd || log.frame < pos);
    const wanted = state.filters[cat] || log.level === "WARNING" || log.level === "ERROR";
    node.hidden = !(happened && wanted);
    node.classList.toggle("recent", log.stage === state.stage && log.frame === pos - 1);
  }
  if (state.follow) $("log").scrollTop = $("log").scrollHeight;
  else {
    const recent = logNodes.find((n) => !n.node.hidden && n.node.classList.contains("recent"));
    if (recent) scrollWithin($("log"), recent.node);
  }
}

// ---- events -----------------------------------------------------------------------------

function addEventRow(stage, ev, frame) {
  const payload = el("tr", { class: "payload", hidden: true }, el("td"), el("td", { colspan: "2" }, el("pre", { text: JSON.stringify(ev.payload, null, 2) })));
  const row = el(
    "tr",
    { class: `ev${DOMAIN_EVENTS.has(ev.type) ? " domain" : ""}`, onclick: () => (payload.hidden = !payload.hidden) },
    el("td", { class: "seq", text: ev.seq }),
    el("td", { class: "type", text: ev.type }),
    el("td", { text: ev.summary }),
  );
  eventNodes.push({ stage, frame, row, payload });
  $("events").append(row, payload);
}

function renderEvents(pos) {
  let current = null;
  for (const n of eventNodes) {
    const visible = n.stage === state.stage && n.frame <= pos;
    n.row.hidden = !visible;
    if (!visible) n.payload.hidden = true;
    const isCurrent = visible && n.frame === pos;
    n.row.classList.toggle("current", isCurrent);
    if (isCurrent && !current) current = n.row;
  }
  if (current) scrollWithin(document.querySelector(".table-wrap"), current, 28);
}

function renderStory() {
  const blocks = ["live", "replay"]
    .filter((s) => state.stages[s].steps)
    .map((s) =>
      el(
        "div",
        { class: "stage" },
        el("h3", { text: s === "live" ? "live run" : "replay" }),
        el("dl", {}, ...state.stages[s].steps.flatMap((st) => [el("dt", { text: st.label }), el("dd", { text: st.detail })])),
      ),
    );
  $("story").replaceChildren(...blocks);
}

// ---- controls ---------------------------------------------------------------------------

function go(index, { keepFollow = false } = {}) {
  const frames = state.stages[state.stage].frames;
  if (!frames.length) return;
  state.pos[state.stage] = Math.max(0, Math.min(frames.length - 1, index));
  if (!keepFollow) state.follow = state.pos[state.stage] === frames.length - 1 && state.status === "running";
  schedule();
}

function step(delta) {
  if (state.mode === "story") return stepBeat(delta);
  go(state.pos[state.stage] + delta);
}

function togglePlay() {
  if (state.playing) {
    clearInterval(state.playing);
    state.playing = null;
  } else {
    const frames = state.stages[state.stage].frames;
    if (state.pos[state.stage] >= frames.length - 1) go(0);
    state.playing = setInterval(
      () => {
        const n = state.stages[state.stage].frames.length;
        if (state.pos[state.stage] >= n - 1) return togglePlay();
        step(1);
      },
      // A beat is a paragraph to read; a frame is one row of the log.
      state.mode === "story" ? 2500 : 250,
    );
  }
  schedule();
}

$("scrubber").addEventListener("input", (e) => go(Number(e.target.value)));
$("first").onclick = () => go(0);
$("prev").onclick = () => step(-1);
$("next").onclick = () => step(1);
$("last").onclick = () => {
  state.follow = state.status === "running";
  go(Infinity, { keepFollow: true });
};
$("play").onclick = togglePlay;
$("follow").onchange = (e) => {
  state.follow = e.target.checked;
  if (state.follow) go(Infinity, { keepFollow: true });
};
for (const b of document.querySelectorAll(".modes button")) b.onclick = () => setMode(b.dataset.mode);
for (const tab of document.querySelectorAll(".tab")) {
  tab.onclick = () => {
    state.stage = tab.dataset.stage;
    state.follow = false;
    schedule();
  };
}
for (const box of document.querySelectorAll("#filters input")) {
  box.onchange = () => {
    state.filters[box.dataset.cat] = box.checked;
    schedule();
  };
}
document.addEventListener("keydown", (e) => {
  if (e.target instanceof HTMLInputElement && e.target.type !== "range") return;
  if (e.target instanceof HTMLButtonElement && (e.key === " " || e.key === "Enter")) return;
  if (e.key === "ArrowLeft") step(-1);
  else if (e.key === "ArrowRight") step(1);
  else if (e.key === "Home") go(0);
  else if (e.key === "End") $("last").onclick();
  else if (e.key === " ") togglePlay();
  else return;
  e.preventDefault();
});

$("run-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  $("run-error").hidden = true;
  const models = $("models").value.split(/\s+/).filter(Boolean);
  const response = await fetch("/run", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ models }),
  });
  if (!response.ok) {
    const { error } = await response.json();
    $("run-error").textContent = error;
    $("run-error").hidden = false;
  } else {
    state.follow = true;
  }
});

// The stream opens only once the history mark is known; see `rewind`.
fetch("/config")
  .then((r) => r.json())
  .then((c) => {
    if (!$("models").value) $("models").value = c.models.join(" ");
    history = c.history;
  })
  .finally(connect);

// The timeline sticks just under the header, whose height changes when it wraps.
new ResizeObserver(([entry]) => {
  document.documentElement.style.setProperty("--bar-h", `${entry.target.offsetHeight}px`);
}).observe(document.querySelector(".bar"));

render();

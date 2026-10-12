// The Story view: the same run, told in plain language, one beat at a time.
//
// A beat is one step of the story -- the setup, or one round's offers, slip, readings
// or close -- rather than one append to the log. Everything here is derived from the
// frames and log lines app.js already holds; nothing new comes from the server. Loaded
// before app.js, whose helpers (el, state, name, ...) it calls at render time.
"use strict";

// How a position or bottom line reads in a sentence: "says it will ___".
const PHRASES = {
  settle: "reach a settlement",
  concede: "give way",
  hold_firm: "hold firm",
  never: "never give way",
};
const phrase = (v) => (v === null || v === undefined ? null : PHRASES[v] ?? String(v).replaceAll("_", " "));

// "the EU" mid-sentence, "The EU" to open one.
const the = (p) => `the ${name(p)}`;
const The = (p) => `The ${name(p)}`;

/** Who heard something, in words: "both sides", "the EU", "nobody". */
function who(parties) {
  if (!parties.length) return "nobody";
  if (parties.length === 2) return "both sides";
  return parties.map(the).join(" and ");
}

// A value longer than this is a sentence, not a word: a real model's bottom line can be
// "unknown: no confirmed furthest movement; …". It moves under its label.
const LONG = 24;

const BEAT_TITLES = {
  setup: "The setup",
  act: "Each side decides",
  say: "Offers on the table",
  leak: "A slip",
  read: "Reading the other side",
  close: "The round ends",
};

/** The story's beats in a stage: runs of frames sharing one phase. */
function beats(stage) {
  const out = [];
  stage.frames.forEach((f, i) => {
    const phase = f.phase || "setup";
    const b = out[out.length - 1];
    if (b && b.phase === phase) b.last = i;
    else out.push({ phase, first: i, last: i });
  });
  return out;
}

/** The beat containing frame `pos`, and its index. */
function beatAt(list, pos) {
  const index = Math.max(0, list.findIndex((b) => b.first <= pos && pos <= b.last));
  return { beat: list[index], index };
}

function beatTitle(phase) {
  const [round, step] = phase.includes(": ") ? phase.split(": ") : [null, phase];
  const title = BEAT_TITLES[step] ?? step;
  return round ? `${round[0].toUpperCase()}${round.slice(1)} — ${title.toLowerCase()}` : title;
}

// ---- reading a snapshot in plain terms ---------------------------------------------------

/** The value of `predicate(party, …)` among `beliefs`, or null. */
function about(beliefs, predicate, party) {
  const found = (beliefs ?? []).find((b) => b.label.startsWith(`${predicate}(${party},`));
  return found ? found.value : null;
}

/** The seated parties, in seat order, as the snapshot's scopes list them. */
function parties(snapshot) {
  return Array.isArray(snapshot.scopes) ? [...new Set(snapshot.scopes.map((s) => s.party))] : [];
}

/** The party across the table. The round loop is pairwise, so there is exactly one. */
const otherThan = (snapshot, party) => parties(snapshot).find((p) => p !== party);

function scope(snapshot, party, kind) {
  const scopes = Array.isArray(snapshot.scopes) ? snapshot.scopes : [];
  return scopes.find((s) => s.party === party && s.kind === kind)?.beliefs ?? [];
}

function model(snapshot, viewer, chain) {
  const models = Array.isArray(snapshot.models) ? snapshot.models : [];
  return models.find((m) => m.viewer === viewer && m.chain.join() === chain.join())?.beliefs ?? [];
}

/** Who `party` believes heard the event labelled `label` (e.g. "#14 SideRemarkOverheard"). */
function believedWitnesses(snapshot, party, label) {
  return scope(snapshot, party, "held")
    .filter((b) => b.value === true && b.label.startsWith("witnessed(") && b.label.endsWith(`, ${label})`))
    .map((b) => b.label.slice("witnessed(".length).split(",")[0]);
}

// ---- what happened in a beat ---------------------------------------------------------------

const OFFER = /^round (\d+): (\w+) offers to (concede|hold firm) \((.*)\)$/s;
const CONCLUSION = /^round (\d+): (\w+) concludes red_line\((\w+)\) = (\w+)(?: \((.*)\))?$/s;

function sentence(...parts) {
  return el("p", {}, ...parts);
}

function quote(text) {
  return el("q", { class: "reason", text });
}

/** Plain sentences for one beat, from its events, the round log, and the snapshot. */
function narrate(stage, beat) {
  const frames = stage.frames.slice(beat.first, beat.last + 1);
  const end = frames[frames.length - 1].snapshot;
  const before = beat.first > 0 ? stage.frames[beat.first - 1].snapshot : null;
  const events = frames.flatMap((f) => f.events);
  // Log lines stamped with frame f happened after it: this beat's are first-1 .. last-1.
  const logs = stage.logs.filter((l) => l.frame >= beat.first - 1 && l.frame < beat.last);
  const out = [];

  if (beat.phase === "setup") {
    const clause = events.find((e) => e.type === "ClauseTabled");
    const seated = parties(end);
    out.push(sentence(`${seated.length === 2 ? "Two" : seated.length} negotiators, ${seated.map(the).join(" and ")}, sit down to bargain over one clause: ${clause ? clause.payload.title : "the clause"}.`));
    const wants = seated.map((p) => [p, phrase(about(scope(end, p, "held"), "position", p))]).filter(([, w]) => w);
    if (wants.length) {
      out.push(sentence(`In private, ${wants.map(([p, w]) => `${the(p)} wants to ${w}`).join(", and ")}. Neither knows what the other wants.`));
    }
    return out;
  }

  for (const e of events) {
    const p = e.payload;
    if (e.type === "PositionStated") {
      const offer = logs.map((l) => OFFER.exec(l.message)).find((m) => m && m[2] === p.party);
      out.push(
        sentence(
          `${The(p.party)} says, out loud, that it will ${phrase(p.position)}.`,
          offer ? el("span", { class: "why" }, " Its reasoning: ", quote(offer[4])) : null,
        ),
      );
    } else if (e.type === "SideRemarkOverheard") {
      out.push(sentence(el("strong", { text: `${The(p.speaker)} lets slip that it might ${phrase(p.suggests)}` }), `, and ${the(p.overheard_by)} overhears it.`));
      const label = `#${e.seq} SideRemarkOverheard`;
      const really = new Set([p.speaker, p.overheard_by]);
      for (const party of parties(end)) {
        const thinks = believedWitnesses(end, party, label);
        // The speaker knows what it said; whether it counts itself a witness is the
        // story's business, not a mistake worth flagging. The listener is the question.
        const missed = [...really].filter((r) => r !== p.speaker && !thinks.includes(r));
        const extra = thinks.filter((t) => !really.has(t));
        const flag = missed.length
          ? ` That's wrong: ${missed.map(the).join(" and ")} heard it.`
          : extra.length
            ? ` That's wrong: ${extra.map(the).join(" and ")} didn't.`
            : "";
        out.push(sentence(`${The(party)} believes ${who(thinks)} heard it.`, flag ? el("span", { class: "flag", text: flag }) : null));
      }
    } else if (e.type === "RoundClosed") {
      out.push(sentence(`Round ${p.round_number} is over.`));
    }
  }

  if (beat.phase.endsWith(": read")) {
    for (const party of parties(end)) {
      const other = otherThan(end, party);
      const now = about(scope(end, party, "held"), "red_line", other);
      const was = before ? about(scope(before, party, "held"), "red_line", other) : null;
      if (now === null || now === was) continue;
      const reading = logs.map((l) => CONCLUSION.exec(l.message)).find((m) => m && m[2] === party);
      out.push(
        sentence(
          `${The(party)} decides ${the(other)}'s bottom line is to ${phrase(now)}${was ? ` (it thought "${phrase(was)}" before)` : ""}.`,
          reading && reading[5] ? el("span", { class: "why" }, " Its reasoning: ", quote(reading[5])) : null,
        ),
      );
    }
  }

  if (beat.phase.endsWith(": close") && Array.isArray(end.chronicle)) {
    const had = before && Array.isArray(before.chronicle) ? before.chronicle.length : 0;
    for (const summary of end.chronicle.slice(had)) {
      out.push(sentence("The round is written up for the record: ", quote(summary)));
    }
  }

  if (!out.length) out.push(sentence("Nothing either side can see happens here."));
  return out;
}

// ---- the panels ------------------------------------------------------------------------------

function mindCard(snapshot, party, previous) {
  const other = otherThan(snapshot, party);
  const P = name(party);
  const O = name(other);
  const rows = [
    ["Secretly wants to", about(scope(snapshot, party, "held"), "position", party), "held"],
    ["Has said it will", about(scope(snapshot, party, "stated"), "position", party), "stated"],
    [`Thinks the ${O} really wants to`, about(model(snapshot, party, [other]), "position", other), "model"],
    [`Thinks the ${O}'s bottom line is to`, about(scope(snapshot, party, "held"), "red_line", other), "held"],
    [`Thinks the ${O} believes the ${P} wants to`, about(model(snapshot, party, [other, party]), "position", party), "model"],
  ];
  const old = previous ? mindValues(previous, party) : null;
  return el(
    "div",
    { class: "mind" },
    el("h3", { text: `The ${P}` }),
    el(
      "dl",
      {},
      ...rows.flatMap(([label, value, kind], i) => {
        const changed = old !== null && old[i] !== value;
        return [
          el("dt", { class: `kind-${kind}`, text: label }),
          el(
            "dd",
            { class: [changed ? "changed" : "", value !== null && phrase(value).length > LONG ? "long" : ""].join(" ") },
            value === null ? el("span", { class: "unknown", text: "no idea yet" }) : phrase(value),
          ),
        ];
      }),
    ),
  );
}

function mindValues(snapshot, party) {
  const other = otherThan(snapshot, party);
  return [
    about(scope(snapshot, party, "held"), "position", party),
    about(scope(snapshot, party, "stated"), "position", party),
    about(model(snapshot, party, [other]), "position", other),
    about(scope(snapshot, party, "held"), "red_line", other),
    about(model(snapshot, party, [other, party]), "position", party),
  ];
}

/**
 * Each side's read of the other, against what the other imagines it to be -- in words.
 *
 * Every direction is checked and reported, not the one the scripted story stages: with
 * real models either side may misjudge what the other has concluded, both may, or
 * neither will. A wrong guess here is about a mind, not about the facts.
 */
function twist(snapshot) {
  const checks = secondOrder(snapshot).filter((e) => e.reads !== null || e.imagined !== null);
  if (!checks.length) {
    return el("div", { class: "twist calm" }, el("strong", { text: "Nothing to compare yet. " }), "Neither side has formed a view of the other.");
  }
  const wrong = checks.filter((e) => e.wrong);
  if (!wrong.length) {
    return el(
      "div",
      { class: "twist calm" },
      el("strong", { text: "The pictures line up. " }),
      ...checks.map((e) => `${The(e.reader)} thinks ${the(e.subject)} will ${phrase(e.reads)}, and ${the(e.subject)} correctly guesses that's what ${the(e.reader)} thinks. `),
    );
  }
  return el(
    "div",
    { class: "twist" },
    el("strong", { text: wrong.length === 1 ? "A mistake about a mind: " : "Both sides are wrong about the other: " }),
    ...wrong.map(
      (e) =>
        (e.reads === null
          ? `${The(e.subject)} thinks ${the(e.reader)} believes it will ${phrase(e.imagined)}, but ${the(e.reader)} has no read on it yet. `
          : `${The(e.reader)} has worked out that ${the(e.subject)} will ${phrase(e.reads)}, but ${the(e.subject)} thinks ${the(e.reader)} believes it will ${e.imagined === null ? "do something else" : phrase(e.imagined)}. `) +
        `That's a mistake about what ${the(e.reader)} knows, and nothing in ${the(e.subject)}'s own records can show it. `,
    ),
  );
}

function behindTheScenes(stage, beat) {
  const logs = stage.logs.filter((l) => l.frame >= beat.first - 1 && l.frame < beat.last);
  const calls = logs.filter((l) => l.detail && l.detail.request);
  const drawn = calls.filter((l) => l.detail.drawn).length;
  const events = stage.frames.slice(beat.first, beat.last + 1).reduce((n, f) => n + f.events.length, 0);
  const parts = [];
  if (calls.length) {
    parts.push(
      `${calls.length} AI call${calls.length === 1 ? "" : "s"}` +
        (drawn === calls.length ? "" : drawn === 0 ? ", all answered from the saved log rather than a model" : `, ${calls.length - drawn} answered from the saved log`),
    );
  }
  parts.push(`${events} entr${events === 1 ? "y" : "ies"} written to the log`);
  return el(
    "p",
    { class: "behind" },
    `Behind the scenes: ${parts.join("; ")}. `,
    el("button", { class: "link", text: "Show the workings", onclick: () => setMode("workings") }),
  );
}

function renderPlain() {
  const stage = state.stages[state.stage];
  const root = $("plain-body");
  if (!stage.frames.length) {
    root.replaceChildren(el("p", { class: "empty", text: state.run === null ? "Press Run to start the negotiation." : "Starting…" }));
    return;
  }
  const list = beats(stage);
  const pos = Math.min(state.pos[state.stage], stage.frames.length - 1);
  const { beat, index } = beatAt(list, pos);
  const end = stage.frames[beat.last].snapshot;
  const previous = beat.first > 0 ? stage.frames[beat.first - 1].snapshot : null;

  $("frame-label").textContent =
    `${state.stage === "replay" ? "Replay · " : ""}step ${index + 1} of ${list.length}: ${beatTitle(beat.phase)}` +
    (index === list.length - 1 && state.status === "running" ? " · live" : "");

  root.replaceChildren(
    el("section", { class: "happened" }, el("h2", { text: beatTitle(beat.phase) }), ...narrate(stage, beat)),
    twist(end),
    el("div", { class: "minds" }, ...parties(end).map((p) => mindCard(end, p, previous))),
    behindTheScenes(stage, beat),
  );
}

/** Move by whole beats, landing on each beat's last frame: the state once it is done. */
function stepBeat(delta) {
  const stage = state.stages[state.stage];
  if (!stage.frames.length) return;
  const list = beats(stage);
  const { index } = beatAt(list, state.pos[state.stage]);
  const target = list[Math.max(0, Math.min(list.length - 1, index + delta))];
  go(target.last);
}

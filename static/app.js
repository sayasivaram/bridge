// Closer frontend. Plain JS, no build step.
//
// No real login: this is a local demo. The chosen partner is remembered in localStorage and
// sent to the server as the X-User-Id header on every API call.
//
// User-written text is always put on the page with textContent, never innerHTML.

const MOOD_EMOJI = { funny: "😂", sweet: "🥰", stressful: "😣", proud: "🌟", random: "🎲" };

const state = {
  userId: Number(localStorage.getItem("closer.userId")) || null,
  me: null,
  weekOf: null, // ISO date inside the week being shown; null = this week
  photo: null, // data URL of the resized photo waiting to be added
  imageUrls: [], // object URLs to free when the list re-renders
};

const $ = (id) => document.getElementById(id);

// ---------- API ----------

async function api(path, options = {}) {
  const headers = { ...(options.headers || {}) };
  if (state.userId) headers["X-User-Id"] = String(state.userId);
  if (options.body && typeof options.body !== "string") {
    headers["Content-Type"] = "application/json";
    options = { ...options, body: JSON.stringify(options.body) };
  }
  const res = await fetch(path, { ...options, headers });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const data = await res.json();
      detail = typeof data.detail === "string" ? data.detail : "Please check the form and try again.";
    } catch {}
    throw new Error(detail);
  }
  if (res.status === 204) return null;
  return res.json();
}

async function apiBlobUrl(path) {
  const res = await fetch(path, { headers: { "X-User-Id": String(state.userId) } });
  if (!res.ok) return null;
  const url = URL.createObjectURL(await res.blob());
  state.imageUrls.push(url);
  return url;
}

// ---------- setup status (footer) ----------

async function showSetupStatus() {
  const box = $("status");
  try {
    const data = await api("/api/health");
    if (data.ai_enabled) {
      box.textContent = `AI ready · ${data.model} via ${data.provider} (key from ${data.key_source})`;
      box.className = "status status--ok";
    } else {
      box.textContent = "No API key found. Add PORTKEY_API_KEY to .env, then restart.";
      box.className = "status status--warn";
    }
  } catch {
    box.textContent = "Could not reach the Closer server.";
    box.className = "status status--warn";
  }
}

// ---------- switch user ----------

async function setupUserSwitch() {
  const select = $("user-select");
  const users = await api("/api/users");
  select.innerHTML = "";
  for (const u of users) {
    const opt = document.createElement("option");
    opt.value = u.id;
    opt.textContent = `${u.name} (Partner ${u.role})`;
    select.appendChild(opt);
  }
  if (!users.some((u) => u.id === state.userId)) state.userId = users[0]?.id ?? null;
  select.value = state.userId;
  select.addEventListener("change", () => switchUser(Number(select.value)));
  await loadMe();
}

async function switchUser(userId) {
  state.userId = userId;
  localStorage.setItem("closer.userId", String(userId));
  resetForm();
  clearCallPrep(); // starters were written for the other partner
  setPulseMsg("");
  await loadMe();
}

async function loadMe() {
  state.me = await api("/api/me");
  if (state.page === "pulse") loadPulse();
  const partner = state.me.partner?.name || "partner";
  document.querySelectorAll(".partner-name").forEach((el) => (el.textContent = partner));
  $("add-hint").textContent =
    `Little things from your day, so your next call with ${partner} has something real to start with.`;
  await loadMoments();
}

// ---------- quick add ----------

function setupAddForm() {
  const text = $("m-text");
  text.addEventListener("input", () => ($("m-count").textContent = text.value.length));

  $("btn-song").addEventListener("click", () => {
    const show = $("song-field").hidden;
    if (show) clearPhoto();
    $("song-field").hidden = !show;
    $("btn-song").classList.toggle("chip-btn--on", show);
    if (show) $("m-song").focus();
    else $("m-song").value = "";
  });

  $("btn-photo").addEventListener("click", () => $("m-photo").click());
  $("m-photo").addEventListener("change", async (e) => {
    const file = e.target.files[0];
    e.target.value = "";
    if (!file) return;
    try {
      state.photo = await resizeImage(file, 1280);
      $("photo-img").src = state.photo;
      $("photo-preview").hidden = false;
      $("btn-photo").classList.add("chip-btn--on");
      $("song-field").hidden = true; // one attachment: photo OR song
      $("m-song").value = "";
      $("btn-song").classList.remove("chip-btn--on");
    } catch {
      showMsg("Couldn't read that photo. Try a JPG or PNG.", true);
    }
  });
  $("photo-remove").addEventListener("click", clearPhoto);

  $("add-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const body = {
      text: text.value.trim(),
      mood: document.querySelector("input[name=mood]:checked").value,
      visibility: document.querySelector("input[name=visibility]:checked").value,
      song: $("m-song").value.trim() || null,
      image: state.photo,
    };
    if (!body.text) return showMsg("Write a line or two first.", true);
    $("add-btn").disabled = true;
    try {
      await api("/api/moments", { method: "POST", body });
      const privateNote = body.visibility === "private" ? " (private, only you can see it)" : "";
      resetForm();
      showMsg(`Added to the jar${privateNote}.`);
      state.weekOf = null; // jump to this week, where the new moment is
      await loadMoments();
    } catch (err) {
      showMsg(err.message, true);
    } finally {
      $("add-btn").disabled = false;
    }
  });
}

function clearPhoto() {
  state.photo = null;
  $("photo-preview").hidden = true;
  $("photo-img").removeAttribute("src");
  $("btn-photo").classList.remove("chip-btn--on");
}

function resetForm() {
  $("add-form").reset();
  $("m-count").textContent = "0";
  $("song-field").hidden = true;
  $("btn-song").classList.remove("chip-btn--on");
  clearPhoto();
  showMsg("");
}

function showMsg(text, isError = false) {
  const el = $("add-msg");
  el.textContent = text;
  el.className = "form-msg " + (text ? (isError ? "form-msg--error" : "form-msg--ok") : "");
}

// Shrink photos in the browser so uploads stay small (max 1280px, JPEG).
function resizeImage(file, maxSide) {
  return new Promise((resolve, reject) => {
    const img = new Image();
    const url = URL.createObjectURL(file);
    img.onload = () => {
      const scale = Math.min(1, maxSide / Math.max(img.width, img.height));
      const canvas = document.createElement("canvas");
      canvas.width = Math.round(img.width * scale);
      canvas.height = Math.round(img.height * scale);
      canvas.getContext("2d").drawImage(img, 0, 0, canvas.width, canvas.height);
      URL.revokeObjectURL(url);
      resolve(canvas.toDataURL("image/jpeg", 0.85));
    };
    img.onerror = () => {
      URL.revokeObjectURL(url);
      reject(new Error("unreadable image"));
    };
    img.src = url;
  });
}

// ---------- this week's moments ----------

function setupWeekNav() {
  $("week-prev").addEventListener("click", () => shiftWeek(-7));
  $("week-next").addEventListener("click", () => shiftWeek(7));
}

function shiftWeek(days) {
  const base = state.weekOf ? new Date(state.weekOf + "T12:00:00") : new Date();
  base.setDate(base.getDate() + days);
  state.weekOf = isoDate(base);
  loadMoments();
}

async function loadMoments() {
  const query = state.weekOf ? `?week_of=${state.weekOf}` : "";
  const data = await api(`/api/moments${query}`);
  state.weekOf = data.week_start;

  const start = new Date(data.week_start + "T12:00:00");
  const end = new Date(start);
  end.setDate(end.getDate() + 6);
  const fmt = (d) => d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
  $("week-title").textContent = data.is_current_week ? "This week" : `Week of ${fmt(start)}`;
  const mine = data.moments.filter((m) => m.is_mine).length;
  const theirs = data.moments.length - mine;
  $("week-sub").textContent =
    `${fmt(start)} – ${fmt(end)} · ${data.moments.length} moments ` +
    `(you ${mine}, ${state.me.partner?.name || "partner"} ${theirs}) · ${state.me.user.city} time`;
  $("week-next").disabled = data.is_current_week;

  renderMoments(data.moments);
}

function renderMoments(moments) {
  state.imageUrls.forEach((u) => URL.revokeObjectURL(u));
  state.imageUrls = [];
  const list = $("moment-list");
  list.innerHTML = "";
  $("empty").hidden = moments.length > 0;
  for (const m of moments) list.appendChild(momentCard(m));
}

function momentCard(m) {
  const li = document.createElement("li");
  li.className = `moment moment--${m.mood}`;

  const emoji = el("div", "emoji", MOOD_EMOJI[m.mood] || "•");
  emoji.title = m.mood;
  const body = el("div", "body");

  const meta = el("div", "meta");
  meta.append(el("span", "who", m.is_mine ? "You" : m.author_name), el("span", "", formatTime(m.created_at)));
  if (m.visibility === "private") meta.append(el("span", "badge badge--private", "🔒 Only you"));
  if (m.is_mine) {
    const del = el("button", "delete", "Delete");
    del.type = "button";
    del.addEventListener("click", () => deleteMoment(m.id));
    meta.append(del);
  }

  body.append(meta, el("p", "text", m.text));
  if (m.song) body.append(el("div", "song", `🎵 ${m.song}`));
  if (m.has_image) {
    const img = document.createElement("img");
    img.alt = "Photo from this moment";
    apiBlobUrl(`/api/moments/${m.id}/image`).then((url) => url && (img.src = url));
    body.append(img);
  }

  li.append(emoji, body);
  return li;
}

async function deleteMoment(id) {
  if (!confirm("Delete this moment?")) return;
  await api(`/api/moments/${id}`, { method: "DELETE" });
  await loadMoments();
}

// ---------- call prep (Call-Topic Agent) ----------

function setupCallPrep() {
  $("prep-btn").addEventListener("click", runCallPrep);
}

function clearCallPrep() {
  $("prep-result").hidden = true;
  setPrepMsg("");
}

function setPrepMsg(text, isError = false) {
  const el = $("prep-msg");
  el.textContent = text;
  el.className = "form-msg " + (text ? (isError ? "form-msg--error" : "muted") : "");
}

async function runCallPrep() {
  const btn = $("prep-btn");
  btn.disabled = true;
  btn.classList.add("loading");
  btn.textContent = "Reading your jar";
  clearCallPrep();
  try {
    const data = await api("/api/call-prep", { method: "POST" });
    renderCallPrep(data);
  } catch (err) {
    setPrepMsg(err.message, true);
  } finally {
    btn.disabled = false;
    btn.classList.remove("loading");
    btn.textContent = "Prep for our call";
  }
}

function renderCallPrep(data) {
  const since = new Date(data.window.since + "T12:00:00");
  const until = new Date(data.window.until + "T12:00:00");
  const fmt = (d) => d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
  $("prep-window").textContent =
    `From ${data.moment_count} shared moments, ${fmt(since)} – ${fmt(until)}`;

  if (!data.starters.length) {
    setPrepMsg(data.moment_count
      ? "Couldn't build starters that pass the safety checks this time. Please try again."
      : "No shared moments in the last 7 days yet. Drop a few in the jar first.");
  }

  const u = data.usage;
  const badge = $("token-badge");
  badge.textContent =
    `${u.calls} call${u.calls === 1 ? "" : "s"} · ${u.input_tokens.toLocaleString()} in ` +
    `(${u.cached_tokens.toLocaleString()} cached) · ${u.output_tokens.toLocaleString()} out · ` +
    `$${u.cost_usd.toFixed(5)}` +
    (u.gateway_cache_hits ? ` · ⚡ ${u.gateway_cache_hits === u.calls ? "" : u.gateway_cache_hits + " "}Portkey cache hit` : "");
  badge.title =
    `Input ${u.input_tokens} = cached ${u.cached_tokens} + cache write ${u.cache_write_tokens} + plain ` +
    `${u.input_tokens - u.cached_tokens - u.cache_write_tokens}\nOutput ${u.output_tokens} ` +
    `(reasoning ${u.reasoning_tokens})\nModel ${data.model || "none"}` +
    (u.gateway_cache_hits ? `\nPortkey answered ${u.gateway_cache_hits} call(s) from its response cache: no model call, $0` : "");

  const list = $("starter-list");
  list.innerHTML = "";
  for (const s of data.starters) {
    const li = el("li", "starter");
    li.append(el("p", "topic", s.topic), el("p", "opener", s.opener), sourceList(s.moments));
    list.appendChild(li);
  }

  const deeper = $("deeper");
  deeper.innerHTML = "";
  deeper.hidden = !data.deeper_question;
  if (data.deeper_question) {
    const d = data.deeper_question;
    deeper.append(el("div", "label", "When you have time: one deeper question"),
      el("p", "question", d.question), el("p", "why", d.why), sourceList(d.moments));
  }

  const trace = $("trace-list");
  trace.innerHTML = "";
  for (const line of data.trace) {
    trace.appendChild(el("li", line.trim().startsWith("Dropped:") ? "dropped" : "", line.trim()));
  }
  $("prep-result").hidden = false;
}

function sourceList(moments) {
  const box = el("div", "sources");
  for (const m of moments) {
    const src = el("div", `source moment--${m.mood}`);
    const who = m.author_name === state.me.user.name ? "You" : m.author_name;
    const text = m.text.length > 110 ? m.text.slice(0, 107) + "…" : m.text;
    src.append(el("i", "", MOOD_EMOJI[m.mood] || "•"), el("b", "", `${who} · ${formatTime(m.created_at)}`), ` — ${text}`);
    if (m.song) src.append(` 🎵 ${m.song}`);
    box.appendChild(src);
  }
  return box;
}

// ---------- tabs ----------

function setupTabs() {
  document.querySelectorAll(".tab[data-page]").forEach((tab) =>
    tab.addEventListener("click", () => showPage(tab.dataset.page)));
}

function showPage(page) {
  state.page = page;
  document.querySelectorAll(".tab[data-page]").forEach((t) => t.classList.toggle("tab--active", t.dataset.page === page));
  document.querySelectorAll("main [data-page]").forEach((s) => (s.hidden = s.dataset.page !== page));
  if (page === "pulse") loadPulse();
}

// ---------- Connection Pulse ----------

const SERIES = { A: "var(--series-a)", B: "var(--series-b)" }; // color follows the partner, not the viewer
const CONTACT_LABEL = { too_little: "too little", right: "about right", too_much: "too much" };

function setupPulseForm() {
  $("pulse-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const score = document.querySelector("input[name=score]:checked");
    const contact = document.querySelector("input[name=contact]:checked");
    if (!score || !contact) return setPulseMsg("Pick a score and how the contact felt.", true);
    $("pulse-btn").disabled = true;
    try {
      const res = await api("/api/pulse", { method: "POST", body: {
        score: Number(score.value), contact: contact.value,
        note: $("pulse-note").value.trim() || null, note_shared: $("pulse-share").checked } });
      setPulseMsg("Saved. You can change it any time this week.");
      if (res.support) renderNudge({ kind: "support", support: res.support });
      await loadPulse(!res.support);
    } catch (err) {
      setPulseMsg(err.message, true);
    } finally {
      $("pulse-btn").disabled = false;
    }
  });
}

function setPulseMsg(text, isError = false) {
  $("pulse-msg").textContent = text;
  $("pulse-msg").className = "form-msg " + (text ? (isError ? "form-msg--error" : "form-msg--ok") : "");
}

async function loadPulse(withNudge = true) {
  const data = await api("/api/pulse");
  const start = new Date(data.week_start + "T12:00:00");
  $("pulse-week").textContent =
    `Week of ${start.toLocaleDateString(undefined, { month: "short", day: "numeric" })} · takes about 30 seconds.`;
  const mine = data.this_week.me;
  document.querySelectorAll("input[name=score]").forEach((i) => (i.checked = mine && Number(i.value) === mine.score));
  document.querySelectorAll("input[name=contact]").forEach((i) => (i.checked = mine && i.value === mine.contact));
  $("pulse-note").value = mine?.note || "";
  $("pulse-share").checked = !!mine?.note_shared;
  $("pulse-btn").textContent = mine ? "Update check-in" : "Save check-in";
  renderChart(data);
  if (withNudge) loadNudge();
}

async function loadNudge() {
  $("nudge-body").textContent = "Checking this week's signals…";
  try {
    renderNudge(await api("/api/pulse/nudge"));
  } catch (err) {
    $("nudge-body").textContent = err.message;
  }
}

function renderNudge(n) {
  const box = $("nudge-body");
  box.innerHTML = "";
  box.className = "";
  if (n.kind === "support") {
    const s = n.support;
    const card = el("div", "support");
    card.append(el("h3", "", s.title), el("p", "", s.body));
    const ul = document.createElement("ul");
    for (const r of s.resources) {
      const li = document.createElement("li");
      li.append(el("b", "", r.name), `: ${r.contact}`);
      ul.appendChild(li);
    }
    card.append(ul, el("p", "", s.emergency), el("p", "footnote", s.footnote));
    box.appendChild(card);
    return;
  }
  if (n.kind === "steady") {
    const row = el("div", "steady");
    const text = el("div");
    text.append(el("b", "", n.title), el("p", "muted small", n.body));
    row.append(el("span", "icon", "🌿"), text);
    box.append(row, traceList(n.trace));
    return;
  }
  box.append(el("p", "nudge-suggestion", n.suggestion), el("p", "nudge-why", n.explanation));
  const chips = el("div", "flag-chips");
  for (const f of n.flags) chips.appendChild(el("span", "flag-chip", f.detail));
  const meta = el("div", "nudge-meta");
  const label = n.source === "fallback" ? "From the rules (no AI needed)" : n.cached ? "Saved from earlier this week" : "Written by the Drift Agent";
  meta.append(el("span", "source-label", label), usageBadge(n.usage, n.cached ? n.first_cost_usd : null));
  box.append(chips, meta, traceList(n.trace));
}

function usageBadge(u, firstCost) {
  const badge = el("span", "token-badge");
  badge.textContent = u.calls
    ? `${u.calls} call${u.calls === 1 ? "" : "s"} · ${u.input_tokens.toLocaleString()} in (${u.cached_tokens.toLocaleString()} cached) · ${u.output_tokens.toLocaleString()} out · $${u.cost_usd.toFixed(5)}` +
      (u.gateway_cache_hits ? " · ⚡ Portkey cache hit" : "")
    : firstCost ? `0 calls now · $0 (first made for $${firstCost.toFixed(5)})` : "0 calls · $0";
  return badge;
}

function traceList(lines) {
  const d = document.createElement("details");
  d.className = "trace";
  d.append(el("summary", "", "How this was decided"));
  const ol = document.createElement("ol");
  for (const line of lines || []) ol.appendChild(el("li", "", line));
  d.appendChild(ol);
  return d;
}

function renderChart(data) {
  const me = state.me.user, partner = state.me.partner;
  const partnerRole = me.role === "A" ? "B" : "A";
  const series = [
    { name: me.name, role: me.role, values: data.history.map((w) => w.me?.score ?? null) },
    { name: data.partner, role: partnerRole, values: data.history.map((w) => w.partner?.score ?? null) },
  ].sort((a, b) => a.role.localeCompare(b.role)); // fixed order: Partner A, then B
  const weeks = data.history.map((w) => new Date(w.week_start + "T12:00:00"));
  const fmt = (d) => d.toLocaleDateString(undefined, { month: "short", day: "numeric" });

  const W = 640, H = 240, L = 36, R = 64, T = 16, B = 34;
  const x = (i) => L + (i * (W - L - R)) / (weeks.length - 1);
  const y = (v) => T + ((5 - v) * (H - T - B)) / 4;
  const svg = $("pulse-chart");
  const NS = "http://www.w3.org/2000/svg";
  const node = (tag, attrs, text) => {
    const n = document.createElementNS(NS, tag);
    for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
    if (text !== undefined) n.textContent = text;
    return n;
  };
  svg.innerHTML = "";
  for (let v = 1; v <= 5; v++) {
    svg.append(node("line", { class: "grid", x1: L, x2: W - R, y1: y(v), y2: y(v) }),
      node("text", { class: "axis-label", x: L - 12, y: y(v) + 4, "text-anchor": "end" }, v));
  }
  weeks.forEach((d, i) => svg.append(node("text", { class: "axis-label", x: x(i), y: H - 10, "text-anchor": "middle" },
    i === weeks.length - 1 ? "This week" : fmt(d))));

  for (const s of series) {
    let path = "", pen = false;
    s.values.forEach((v, i) => {
      if (v == null) { pen = false; return; }
      path += `${pen ? "L" : "M"}${x(i)},${y(v)} `;
      pen = true;
    });
    if (path) svg.append(node("path", { class: "series-line", d: path, stroke: SERIES[s.role] }));
    // When both partners gave the same score, shift the two dots apart so neither hides the other.
    const tie = (i) => series.every((o) => o.values[i] === s.values[i]);
    const dx = (i) => (tie(i) ? (s.role === "A" ? -4 : 4) : 0);
    s.values.forEach((v, i) => v != null && svg.append(node("circle", { class: "point", cx: x(i) + dx(i), cy: y(v), r: 5, fill: SERIES[s.role] })));
    const lastI = s.values.map((v) => v != null).lastIndexOf(true);
    if (lastI >= 0) svg.append(node("text", { class: "direct-label", x: x(lastI) + 10, y: y(s.values[lastI]) + 4 }, s.name));
  }

  // hover: crosshair + tooltip per week column
  const cross = node("line", { class: "crosshair", y1: T, y2: H - B, visibility: "hidden" });
  svg.appendChild(cross);
  const tip = $("chart-tip");
  weeks.forEach((d, i) => {
    const colW = (W - L - R) / (weeks.length - 1);
    const hit = node("rect", { x: x(i) - colW / 2, y: 0, width: colW, height: H, fill: "transparent" });
    hit.addEventListener("mouseenter", () => {
      cross.setAttribute("x1", x(i)); cross.setAttribute("x2", x(i)); cross.setAttribute("visibility", "visible");
      tip.innerHTML = "";
      tip.append(`Week of ${fmt(d)}`);
      for (const s of series) {
        const row = document.createElement("div");
        const dot = document.createElement("i");
        dot.style.background = SERIES[s.role];
        const status = data.history[i].partner_status;
        const hidden = s.name !== me.name && s.values[i] == null && status === "waiting_for_partner";
        row.append(dot, `${s.name}: ${s.values[i] ?? (hidden ? "not yet" : "—")}`);
        tip.appendChild(row);
      }
      const rect = svg.getBoundingClientRect();
      tip.style.left = `${(x(i) / W) * rect.width}px`;
      tip.style.top = `${(T / H) * rect.height + 8}px`;
      tip.hidden = false;
    });
    hit.addEventListener("mouseleave", () => { cross.setAttribute("visibility", "hidden"); tip.hidden = true; });
    svg.appendChild(hit);
  });

  $("chart-legend").innerHTML = "";
  for (const s of series) {
    const item = el("span");
    const swatch = document.createElement("i");
    swatch.style.background = SERIES[s.role];
    item.append(swatch, s.name);
    $("chart-legend").appendChild(item);
  }
  const status = data.this_week.partner_status;
  $("chart-desc").textContent = {
    waiting_for_partner: `${data.partner} hasn't checked in yet this week. You'll see each other's scores once you both have.`,
    waiting_for_you: `${data.partner} has checked in this week. Check in to see each other's scores.`,
    none: "Neither of you has checked in this week yet.",
    shown: "You've both checked in this week.",
  }[status];

  $("th-a").textContent = series[0].name;
  $("th-b").textContent = series[1].name;
  const tbody = $("chart-tbody");
  tbody.innerHTML = "";
  data.history.forEach((w, i) => {
    const tr = document.createElement("tr");
    tr.append(el("td", "", fmt(weeks[i])), el("td", "", String(series[0].values[i] ?? "—")),
      el("td", "", String(series[1].values[i] ?? "—")));
    tbody.appendChild(tr);
  });
}

// ---------- helpers ----------

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text; // never innerHTML: user text is untrusted
  return node;
}

// Times are stored in UTC and shown in the viewing partner's own time zone (not the browser's).
function formatTime(utc) {
  const d = new Date(utc.replace(" ", "T") + "Z");
  const options = { weekday: "short", hour: "numeric", minute: "2-digit" };
  try {
    return d.toLocaleString(undefined, { ...options, timeZone: state.me?.user?.timezone || undefined });
  } catch {
    return d.toLocaleString(undefined, options); // unknown zone name: fall back to the browser's
  }
}

function isoDate(d) {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

setupAddForm();
setupWeekNav();
setupCallPrep();
setupTabs();
setupPulseForm();
showSetupStatus();
setupUserSwitch();

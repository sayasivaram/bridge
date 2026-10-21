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
  await loadMe();
}

async function loadMe() {
  state.me = await api("/api/me");
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
    `(you ${mine}, ${state.me.partner?.name || "partner"} ${theirs})`;
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

// ---------- helpers ----------

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text; // never innerHTML: user text is untrusted
  return node;
}

function formatTime(utc) {
  const d = new Date(utc.replace(" ", "T") + "Z");
  return d.toLocaleString(undefined, { weekday: "short", hour: "numeric", minute: "2-digit" });
}

function isoDate(d) {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

setupAddForm();
setupWeekNav();
showSetupStatus();
setupUserSwitch();

// Closer frontend. Plain JS, no build step.
//
// No real login: this is a local demo. The chosen partner is remembered in localStorage and
// sent to the server as the X-User-Id header on every API call.

const state = { userId: Number(localStorage.getItem("closer.userId")) || null };

async function api(path, options = {}) {
  const headers = { ...(options.headers || {}) };
  if (state.userId) headers["X-User-Id"] = String(state.userId);
  const res = await fetch(path, { ...options, headers });
  if (!res.ok) throw new Error((await res.json()).detail || res.statusText);
  return res.json();
}

async function showSetupStatus() {
  const box = document.getElementById("status");
  try {
    const data = await api("/api/health");
    if (data.ai_enabled) {
      box.textContent =
        `AI ready: ${data.provider} key from ${data.key_source}, model ${data.model}.`;
      box.className = "status status--ok";
    } else {
      box.textContent =
        "No API key found. Add PORTKEY_API_KEY to a .env file in this folder or the folder above it, " +
        "then restart the server.";
      box.className = "status status--warn";
    }
  } catch (err) {
    box.textContent = "Could not reach the Closer server.";
    box.className = "status status--warn";
  }
}

async function setupUserSwitch() {
  const select = document.getElementById("user-select");
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
  await renderMe();
}

async function switchUser(userId) {
  state.userId = userId;
  localStorage.setItem("closer.userId", String(userId));
  await renderMe();
}

async function renderMe() {
  const { user, partner, couple } = await api("/api/me");
  document.getElementById("greeting").textContent = `Hi, ${user.name}`;
  document.getElementById("couple-line").textContent = partner
    ? `${couple} · you're in ${user.city}, ${partner.name} is in ${partner.city}.`
    : couple;
}

showSetupStatus();
setupUserSwitch();

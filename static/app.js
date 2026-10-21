// Closer frontend. Plain JS, no build step.

async function showSetupStatus() {
  const box = document.getElementById("status");
  try {
    const res = await fetch("/api/health");
    const data = await res.json();
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

showSetupStatus();

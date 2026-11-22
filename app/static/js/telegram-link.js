(() => {
  const form = document.querySelector("[data-telegram-link]");
  if (!form) return;
  const status = document.querySelector("[data-telegram-link-status]");
  const button = form.querySelector("button");
  let busy = false;
  let started = false;
  window.addEventListener("focus", () => {
    if (started && !busy && !document.hidden) window.location.reload();
  });
  form.addEventListener("submit", async event => {
    event.preventDefault();
    if (busy) return;
    busy = true;
    button.disabled = true;
    const popup = window.open("about:blank", "_blank");
    if (popup) popup.opener = null;
    try {
      const response = await fetch(form.action, {
        method: "POST", body: new URLSearchParams(new FormData(form)),
        headers: {Accept: "application/json"},
      });
      const data = await response.json();
      if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : "Не вдалося почати прив’язку.");
      const url = new URL(data.url);
      if (url.origin !== "https://t.me") throw new Error("Не вдалося відкрити бота.");
      started = true;
      status.textContent = "Підтвердьте прив’язку в боті — доступ відкриється автоматично.";
      status.hidden = false;
      if (popup && !popup.closed) popup.location.replace(url.href);
      else window.location.assign(url.href);
    } catch (error) {
      if (popup && !popup.closed) popup.close();
      status.textContent = error.message || "Перевірте з’єднання та спробуйте ще раз.";
      status.hidden = false;
    } finally {
      busy = false;
      button.disabled = false;
    }
  });
})();

(() => {
  const status = document.getElementById("telegram-status");
  const form = document.getElementById("telegram-finish");
  const expires = Date.now() + 10 * 60 * 1000;
  let busy = false;
  let done = false;
  form.addEventListener("submit", async event => {
    event.preventDefault();
    const button = form.querySelector("button");
    button.disabled = true;
    try {
      const response = await fetch(form.action, {
        method: "POST", body: new URLSearchParams(new FormData(form)),
      });
      if (response.ok) {
        window.location.assign("/cabinet");
      } else {
        const error = await response.json();
        status.textContent = typeof error.detail === "string" ? error.detail : "Не вдалося завершити дію. Спробуйте ще раз у кабінеті.";
      }
    } catch (_) {
      status.textContent = "З’єднання перерване. Спробуйте ще раз.";
    } finally { button.disabled = false; }
  });
  async function poll() {
    if (busy || done || document.hidden) return;
    if (Date.now() > expires) {
      status.textContent = "Час очікування минув. Почніть ще раз у кабінеті.";
      done = true;
      return;
    }
    busy = true;
    try {
      const response = await fetch("/auth/telegram/status", {cache: "no-store"});
      if (response.status === 410 || response.status === 409 || response.status === 403) {
        status.textContent = "Посилання більше не діє. Почніть ще раз у кабінеті.";
        done = true;
      } else if (response.ok) {
        const data = await response.json();
        if (data.approved) {
          status.textContent = `Telegram підтверджено: ${data.name}.`;
          form.hidden = false;
          done = true;
        }
      }
    } catch (_) {
      status.textContent = "Очікуємо з’єднання. Перевірка повториться автоматично.";
    } finally { busy = false; }
  }
  window.setInterval(poll, 2500);
  document.addEventListener("visibilitychange", poll);
  poll();
})();

const csrfToken = document.querySelector('meta[name="csrf-token"]').content;

async function errorDetail(response, fallback) {
  try {
    const body = await response.json();
    return typeof body.detail === "string" ? body.detail : fallback;
  } catch {
    return fallback;
  }
}

function showFormError(form, message) {
  let feedback = form.querySelector(".admin-feedback");
  if (!feedback) {
    feedback = document.createElement("p");
    feedback.className = "admin-feedback";
    feedback.setAttribute("role", "alert");
    feedback.dataset.error = "";
    form.append(feedback);
  }
  feedback.textContent = message;
}

document.querySelectorAll("[data-admin-form]").forEach((form) => {
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (form.getAttribute("aria-busy") === "true") return;
    if (form.dataset.confirm && !window.confirm(form.dataset.confirm)) return;

    const button = event.submitter;
    const data = new URLSearchParams(new FormData(form));
    form.querySelector(".admin-feedback")?.remove();
    form.setAttribute("aria-busy", "true");
    if (button) button.disabled = true;

    const fallback = "Не вдалося зберегти зміни. Спробуйте ще раз.";
    try {
      const response = await fetch(form.action, { method: "POST", body: data });
      if (!response.ok || !response.redirected) {
        showFormError(form, await errorDetail(response, fallback));
        return;
      }
      const target = new URL(response.url);
      if (form.dataset.section) target.hash = form.dataset.section;
      if (target.pathname === location.pathname && target.search === location.search) {
        history.replaceState(null, "", target);
        location.reload();
      } else {
        location.assign(target);
      }
    } catch {
      showFormError(form, fallback);
    } finally {
      form.removeAttribute("aria-busy");
      if (button) button.disabled = false;
    }
  });
});

function rowsIn(list) {
  return [...list.querySelectorAll("[data-id]")];
}

function syncOrderButtons(list) {
  const rows = rowsIn(list);
  rows.forEach((row, index) => {
    row.querySelector('[data-move="-1"]').disabled = index === 0;
    row.querySelector('[data-move="1"]').disabled = index === rows.length - 1;
  });
}

document.querySelectorAll("[data-reorder-list]").forEach((list) => {
  syncOrderButtons(list);
  list.addEventListener("click", async (event) => {
    const button = event.target.closest("[data-move]");
    if (!button || list.getAttribute("aria-busy") === "true") return;

    const rows = rowsIn(list);
    const row = button.closest("[data-id]");
    const index = rows.indexOf(row);
    const direction = Number(button.dataset.move);
    const neighbor = rows[index + direction];
    if (!neighbor) return;

    const ids = rows.map((item) => Number(item.dataset.id));
    [ids[index], ids[index + direction]] = [ids[index + direction], ids[index]];
    const feedback = list.querySelector("[data-reorder-status]");
    feedback.textContent = "Зберігаємо порядок…";
    delete feedback.dataset.error;
    list.setAttribute("aria-busy", "true");

    const fallback = "Не вдалося змінити порядок. Оновіть сторінку й повторіть дію.";
    try {
      const response = await fetch(list.dataset.reorderList, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CSRF-Token": csrfToken },
        body: JSON.stringify({ ids }),
      });
      if (!response.ok) {
        feedback.dataset.error = "";
        feedback.textContent = await errorDetail(response, fallback);
        return;
      }
      const restoreFocus = row.contains(document.activeElement);
      if (direction < 0) list.insertBefore(row, neighbor);
      else list.insertBefore(neighbor, row);
      syncOrderButtons(list);
      if (restoreFocus) {
        (button.disabled ? row.querySelector("[data-move]:not(:disabled)") : button)?.focus();
      }
      feedback.textContent = "Порядок збережено.";
    } catch {
      feedback.dataset.error = "";
      feedback.textContent = fallback;
    } finally {
      list.removeAttribute("aria-busy");
    }
  });
});

if (location.pathname === "/admin") {
  const links = document.querySelectorAll(".admin-nav a");
  const markCurrentSection = () => {
    links.forEach((link) => {
      const hash = link.hash;
      if (hash && hash === location.hash) link.setAttribute("aria-current", "location");
      else if (!hash && link.pathname === location.pathname && !location.hash) {
        link.setAttribute("aria-current", "page");
      } else link.removeAttribute("aria-current");
    });
  };
  markCurrentSection();
  window.addEventListener("hashchange", markCurrentSection);
}

const csrfToken = document.querySelector('meta[name="csrf-token"]').content;
const saveButton = document.querySelector("[data-admin-save]");
const discardButton = document.querySelector("[data-admin-discard]");
const saveMessage = document.querySelector("[data-save-message]");
const storageKey = "optika.admin.changes.v2";
const blankDraft = () => ({ edits: {}, additions: [], forms: {}, orders: {}, reopened: {} });
let draft = blankDraft();
let saving = false;

try {
  draft = JSON.parse(sessionStorage.getItem(storageKey)) ?? draft;
} catch {
  // Editing still works within this page if browser storage is unavailable.
}

function editableFields(form) {
  return [...form.querySelectorAll('input[name]:not([type="hidden"]), select[name]')];
}

function valuesOf(form) {
  return Object.fromEntries(editableFields(form).map((field) => [
    field.name, field.type === "checkbox" ? field.checked : field.value,
  ]));
}

function fillFields(form, values) {
  editableFields(form).forEach((field) => {
    if (field.type === "checkbox") field.checked = values[field.name];
    else field.value = values[field.name];
  });
}

function matches(left, right) {
  return JSON.stringify(left) === JSON.stringify(right);
}

function updateValidity(form, kind, values) {
  if (kind === "closed_periods") {
    const days = (Date.parse(values.end) - Date.parse(values.start)) / 86400000;
    let message = "";
    if (days < 0) message = "Кінець періоду має бути в день початку або пізніше.";
    else if (days >= 366) message = "Один період може містити до 366 днів.";
    form.elements.end.setCustomValidity(message);
  }
  if (kind === "time_slots") {
    const invalid = values.start_time && values.end_time && values.end_time <= values.start_time;
    form.elements.end_time.setCustomValidity(invalid ? "Кінець має бути пізніше за початок." : "");
  }
  return form.checkValidity();
}

function activeEdits() {
  return Object.values(draft.edits);
}

function numberOfChanges() {
  return activeEdits().length + draft.additions.length + Object.keys(draft.forms).length
    + Object.keys(draft.orders).length + Object.keys(draft.reopened).length;
}

function showSaveState(message, error = false) {
  const count = numberOfChanges();
  saveButton.disabled = saving || !count;
  discardButton.hidden = !count;
  discardButton.disabled = saving;
  saveMessage.toggleAttribute("data-error", error);
  saveMessage.textContent = message ?? (count ? "Незбережених змін: " + count : "Усі зміни збережено.");
}

function persistDraft() {
  try {
    sessionStorage.setItem(storageKey, JSON.stringify(draft));
  } catch {
    // The current page retains the draft in memory.
  }
  showSaveState();
}

document.querySelectorAll("[data-admin-edit]").forEach((form) => {
  const kind = form.dataset.adminEdit;
  const key = kind + ":" + form.dataset.id;
  const initial = valuesOf(form);
  const closedDays = kind === "closed_periods" ? Object.entries(JSON.parse(form.dataset.closedDays)) : [];
  const identity = closedDays.length
    ? { previous: Object.fromEntries(closedDays.map(([, day]) => [day.id, day.version])) }
    : { id: Number(form.dataset.id), version: form.dataset.version };

  const existing = draft.reopened[key] ?? draft.edits[key];
  if (existing) {
    fillFields(form, existing.values);
    form.dataset.dirty = "";
  }

  const rememberEdit = () => {
    const values = valuesOf(form);
    const valid = updateValidity(form, kind, values);
    if (matches(initial, values)) delete draft.edits[key];
    else draft.edits[key] = { kind, identity: draft.edits[key]?.identity ?? identity, values, valid };
    form.toggleAttribute("data-dirty", Boolean(draft.edits[key]));
    persistDraft();
  };
  form.addEventListener("input", rememberEdit);
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    saveButton.focus();
  });

  const reopen = form.querySelector("[data-reopen]");
  if (!reopen) return;
  const renderReopen = () => {
    const willReopen = Boolean(draft.reopened[key]);
    form.toggleAttribute("data-reopening", willReopen);
    editableFields(form).forEach((field) => { field.disabled = willReopen; });
    reopen.textContent = willReopen ? "Залишити закритим" : "Відкрити вибрані дати";
  };
  renderReopen();
  reopen.addEventListener("click", () => {
    if (draft.reopened[key]) {
      delete draft.reopened[key];
      fillFields(form, initial);
      updateValidity(form, kind, initial);
      form.removeAttribute("data-dirty");
    } else {
      const values = valuesOf(form);
      if (!updateValidity(form, kind, values)) {
        form.reportValidity();
        return;
      }
      const selected = closedDays.filter(([day]) => values.start <= day && day <= (values.end || values.start));
      if (!selected.length) {
        showSaveState("У вибраному проміжку немає дат цього закритого періоду.", true);
        return;
      }
      const versions = draft.edits[key]?.identity.previous ?? identity.previous;
      const previous = Object.fromEntries(selected.map(([, day]) => [day.id, versions[day.id] ?? day.version]));
      draft.reopened[key] = { identity: { previous }, values };
      delete draft.edits[key];
    }
    renderReopen();
    persistDraft();
  });
});

function additionLabel(kind, values) {
  if (kind === "services") return values.name;
  if (kind === "time_slots") return values.start_time + " – " + values.end_time;
  const formatDate = (value) => value.split("-").reverse().join(".");
  const dates = values.end && values.end !== values.start
    ? formatDate(values.start) + " – " + formatDate(values.end)
    : formatDate(values.start);
  return values.reason ? dates + " · " + values.reason : dates;
}

function renderAdditions() {
  document.querySelectorAll("[data-pending-for]").forEach((list) => {
    const items = draft.additions.filter((item) => item.kind === list.dataset.pendingFor);
    list.hidden = !items.length;
    list.replaceChildren(...items.map((item) => {
      const row = document.createElement("li");
      const label = document.createElement("span");
      label.textContent = additionLabel(item.kind, item.values);
      const remove = document.createElement("button");
      remove.className = "admin-button admin-button-small";
      remove.type = "button";
      remove.textContent = "Прибрати";
      remove.setAttribute("aria-label", "Прибрати зі змін: " + label.textContent);
      remove.addEventListener("click", () => {
        draft.additions = draft.additions.filter((entry) => entry !== item);
        renderAdditions();
        persistDraft();
      });
      row.append(label, remove);
      return row;
    }));
  });
}

document.querySelectorAll("[data-admin-add]").forEach((form) => {
  const kind = form.dataset.adminAdd;
  const initial = valuesOf(form);
  if (draft.forms[kind]) {
    fillFields(form, draft.forms[kind].values);
    form.closest("details").open = true;
  }

  form.addEventListener("input", () => {
    const values = valuesOf(form);
    const valid = updateValidity(form, kind, values);
    if (matches(initial, values)) delete draft.forms[kind];
    else draft.forms[kind] = { kind, values, valid };
    persistDraft();
  });
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    const values = valuesOf(form);
    if (!updateValidity(form, kind, values)) {
      form.reportValidity();
      return;
    }
    draft.additions.push({ kind, values });
    delete draft.forms[kind];
    form.reset();
    renderAdditions();
    persistDraft();
  });
});

document.querySelectorAll("[data-order]").forEach((list) => {
  const orderKey = list.dataset.order;
  const getRows = () => [...list.querySelectorAll("[data-id]")];
  const idsOf = (rows) => rows.map((row) => Number(row.dataset.id));
  const initial = idsOf(getRows());
  const pending = draft.orders[orderKey];
  if (pending?.length === initial.length && pending.every((id) => initial.includes(id))) {
    const rows = new Map(getRows().map((row) => [Number(row.dataset.id), row]));
    list.append(...pending.map((id) => rows.get(id)));
  }

  const syncButtons = () => {
    const rows = getRows();
    rows.forEach((row, index) => {
      row.querySelector('[data-move="-1"]').disabled = index === 0;
      row.querySelector('[data-move="1"]').disabled = index === rows.length - 1;
    });
  };
  syncButtons();
  list.addEventListener("click", (event) => {
    const button = event.target.closest("[data-move]");
    if (!button) return;
    const row = button.closest("[data-id]");
    const up = button.dataset.move === "-1";
    const neighbor = up ? row.previousElementSibling : row.nextElementSibling;
    if (!neighbor) return;

    if (up) list.insertBefore(row, neighbor);
    else list.insertBefore(neighbor, row);
    syncButtons();
    (button.disabled ? row.querySelector("[data-move]:not(:disabled)") : button)?.focus();

    const order = idsOf(getRows());
    if (matches(initial, order)) delete draft.orders[orderKey];
    else draft.orders[orderKey] = order;
    persistDraft();
  });
});

function payloadOfDraft() {
  const payload = { appointments: [], services: [], time_slots: [], closed_periods: [], ...draft.orders };
  const entries = [...activeEdits(), ...draft.additions, ...Object.values(draft.forms)];
  entries.forEach(({ kind, identity, values }) => {
    const item = { ...identity, ...values };
    if (kind === "closed_periods" && !item.end) item.end = null;
    payload[kind].push(item);
  });
  payload.closed_periods.push(...Object.values(draft.reopened).map((entry) => entry.identity));
  return payload;
}

function clearDraft() {
  draft = blankDraft();
  try {
    sessionStorage.removeItem(storageKey);
  } catch {
    // Storage can be disabled by the browser.
  }
}

saveButton.addEventListener("click", async () => {
  if (saving || !numberOfChanges()) return;
  const invalid = [...activeEdits(), ...Object.values(draft.forms)]
    .find((entry) => entry.valid === false);
  if (invalid) {
    const forms = [...document.querySelectorAll("[data-admin-edit], [data-admin-add]")];
    const form = forms.find((candidate) => (
      (candidate.dataset.adminEdit ?? candidate.dataset.adminAdd) === invalid.kind
      && matches(valuesOf(candidate), invalid.values)
    ));
    if (form) {
      const details = form.closest("details");
      if (details) details.open = true;
      updateValidity(form, invalid.kind, invalid.values);
      form.reportValidity();
    }
    showSaveState("У чернетці є неповні або некоректні поля.", true);
    return;
  }

  const main = document.querySelector(".admin-main");
  const sidebar = document.querySelector(".admin-sidebar");
  saving = true;
  main.inert = true;
  sidebar.inert = true;
  showSaveState("Зберігаємо зміни…");
  const fallback = "Не вдалося зберегти зміни. Чернетка залишилася в цій вкладці.";
  try {
    const response = await fetch("/admin/changes", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": csrfToken },
      body: JSON.stringify(payloadOfDraft()),
    });
    if (!response.ok) {
      const body = await response.json().catch(() => null);
      showSaveState(typeof body?.detail === "string" ? body.detail : fallback, true);
      return;
    }
    clearDraft();
    location.reload();
  } catch {
    showSaveState(fallback, true);
  } finally {
    saving = false;
    main.inert = false;
    sidebar.inert = false;
    saveButton.disabled = !numberOfChanges();
    discardButton.disabled = false;
  }
});

discardButton.addEventListener("click", () => {
  if (!window.confirm("Скинути всі незбережені зміни?")) return;
  clearDraft();
  location.reload();
});

renderAdditions();
showSaveState();

window.addEventListener("pageshow", (event) => {
  if (event.persisted) location.reload();
});

if (location.pathname === "/admin") {
  const links = document.querySelectorAll(".admin-nav a");
  const markCurrentSection = () => {
    links.forEach((link) => {
      if (link.hash && link.hash === location.hash) link.setAttribute("aria-current", "location");
      else if (!link.hash && link.pathname === location.pathname && !location.hash) {
        link.setAttribute("aria-current", "page");
      } else link.removeAttribute("aria-current");
    });
  };
  markCurrentSection();
  window.addEventListener("hashchange", markCurrentSection);
}

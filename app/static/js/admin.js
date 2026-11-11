const csrfToken = document.querySelector('meta[name="csrf-token"]').content;
const saveButton = document.querySelector("[data-admin-save]");
const discardButton = document.querySelector("[data-admin-discard]");
const saveMessage = document.querySelector("[data-save-message]");
const storageKey = "optika.admin.changes.v3";
const blankDraft = (month = null) => ({
  edits: {}, additions: [], forms: {}, orders: {}, removals: {}, days: {}, reason: "", month,
});
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
  return Object.fromEntries(editableFields(form).map((field) => [field.name, field.value]));
}

function fillFields(form, values) {
  editableFields(form).forEach((field) => {
    field.value = values[field.name];
  });
}

function matches(left, right) {
  return JSON.stringify(left) === JSON.stringify(right);
}

function updateValidity(form, kind, values) {
  if (kind === "time_slots") {
    const invalid = values.start_time && values.end_time && values.end_time <= values.start_time;
    form.elements.end_time.setCustomValidity(invalid ? "Кінець має бути пізніше за початок." : "");
  }
  return form.checkValidity();
}

function activeEdits() {
  return Object.entries(draft.edits).filter(([key]) => !draft.removals[key]).map(([, edit]) => edit);
}

function numberOfChanges() {
  return activeEdits().length + draft.additions.length + Object.keys(draft.forms).length
    + Object.keys(draft.orders).length + Object.keys(draft.removals).length
    + Object.keys(draft.days).length;
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
  const identity = { id: Number(form.dataset.id), version: form.dataset.version };

  const existing = draft.edits[key];
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

  const remove = form.querySelector("[data-remove]");
  if (!remove) return;
  const renderRemoval = () => {
    const pending = Boolean(draft.removals[key]);
    form.toggleAttribute("data-removing", pending);
    editableFields(form).forEach((field) => { field.disabled = pending; });
    remove.textContent = pending ? "Скасувати" : "Видалити";
    remove.setAttribute("aria-label", (pending ? "Скасувати видалення: " : "Видалити: ") + remove.dataset.label);
    form.parentElement.dispatchEvent(new Event("admin-removals"));
  };
  renderRemoval();
  remove.addEventListener("click", () => {
    if (draft.removals[key]) delete draft.removals[key];
    else draft.removals[key] = { kind, identity: draft.edits[key]?.identity ?? identity };
    renderRemoval();
    persistDraft();
  });
});

function additionLabel(kind, values) {
  return kind === "services" ? values.name : values.start_time + " – " + values.end_time;
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
  const movableRows = () => getRows().filter((row) => !row.hasAttribute("data-removing"));
  const idsOf = (rows) => rows.map((row) => Number(row.dataset.id));
  const initial = idsOf(getRows());
  const pending = draft.orders[orderKey];
  if (pending?.length === initial.length && pending.every((id) => initial.includes(id))) {
    const rows = new Map(getRows().map((row) => [Number(row.dataset.id), row]));
    list.append(...pending.map((id) => rows.get(id)));
  }

  const syncButtons = () => {
    const movable = movableRows();
    getRows().forEach((row) => {
      const index = movable.indexOf(row);
      row.querySelector('[data-move="-1"]').disabled = index <= 0;
      row.querySelector('[data-move="1"]').disabled = index < 0 || index === movable.length - 1;
    });
  };
  syncButtons();
  list.addEventListener("admin-removals", syncButtons);
  list.addEventListener("click", (event) => {
    const button = event.target.closest("[data-move]");
    if (!button) return;
    const row = button.closest("[data-id]");
    const up = button.dataset.move === "-1";
    const movable = movableRows();
    const neighbor = movable[movable.indexOf(row) + (up ? -1 : 1)];
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
  const payload = {
    appointments: [], services: [], time_slots: [],
    remove_services: [], remove_time_slots: [],
    closed_days: { close: [], reopen: {}, reason: draft.reason },
    ...draft.orders,
  };
  const entries = [...activeEdits(), ...draft.additions, ...Object.values(draft.forms)];
  entries.forEach(({ kind, identity, values }) => {
    payload[kind].push({ ...identity, ...values });
  });
  Object.values(draft.removals).forEach(({ kind, identity }) => payload["remove_" + kind].push(identity));
  Object.entries(draft.days).forEach(([day, reopening]) => {
    if (reopening) payload.closed_days.reopen[reopening.id] = reopening.version;
    else payload.closed_days.close.push(day);
  });
  return payload;
}

function clearDraft() {
  draft = blankDraft(draft.month);
  persistDraft();
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

const calendar = document.querySelector("[data-admin-calendar]");
if (calendar) {
  const closed = JSON.parse(calendar.dataset.closedDays);
  const today = calendar.dataset.today;
  const monthLabel = calendar.querySelector("[data-calendar-month]");
  const daysGrid = calendar.querySelector("[data-calendar-days]");
  const count = calendar.querySelector("[data-calendar-count]");
  const reason = calendar.querySelector("[data-calendar-reason]");
  const reasonField = calendar.querySelector("[data-calendar-reason-field]");
  const monthFormat = new Intl.DateTimeFormat("uk-UA", { month: "long", year: "numeric", timeZone: "UTC" });
  const dateFormat = new Intl.DateTimeFormat("uk-UA", { dateStyle: "full", timeZone: "UTC" });
  const dateAt = (year, month, day = 1) => {
    const date = new Date(0);
    date.setUTCFullYear(year, month, day);
    return date;
  };
  const fromISO = (iso) => new Date(iso + "T00:00:00Z");
  const dateKey = (date) => date.toISOString().slice(0, 10);
  const start = fromISO((draft.month ?? today.slice(0, 7)) + "-01");
  let month = Number.isNaN(start.getTime()) ? fromISO(today.slice(0, 7) + "-01") : start;
  reason.value = draft.reason;

  function renderCalendar() {
    const title = monthFormat.format(month);
    if (monthLabel.textContent !== title) monthLabel.textContent = title;
    const year = month.getUTCFullYear();
    const monthIndex = month.getUTCMonth();
    const offset = (month.getUTCDay() + 6) % 7;
    const lastDay = dateAt(year, monthIndex + 1, 0).getUTCDate();
    const cells = Array.from({ length: offset }, () => {
      const empty = document.createElement("span");
      empty.setAttribute("aria-hidden", "true");
      return empty;
    });
    for (let day = 1; day <= lastDay; day++) {
      const date = dateAt(year, monthIndex, day);
      const iso = dateKey(date);
      const current = closed[iso];
      const pending = Object.hasOwn(draft.days, iso);
      const state = pending ? (draft.days[iso] ? "opening" : "closing") : (current ? "closed" : "");
      const label = { closed: "Закрито", closing: "Буде закрито", opening: "Буде відкрито" }[state];
      const button = document.createElement("button");
      button.type = "button";
      button.className = "admin-calendar-day";
      button.dataset.calendarDay = iso;
      button.dataset.state = state;
      button.textContent = String(day);
      button.setAttribute("aria-pressed", String(state === "closed" || state === "closing"));
      if (iso === today) button.setAttribute("aria-current", "date");
      button.setAttribute("aria-label", [dateFormat.format(date), label, current?.reason].filter(Boolean).join(". "));
      if (current?.reason) button.title = current.reason;
      cells.push(button);
    }
    daysGrid.replaceChildren(...cells);
    const pending = Object.values(draft.days);
    const opening = pending.filter(Boolean).length;
    const closing = pending.length - opening;
    count.textContent = [
      closing ? "До закриття: " + closing : "",
      opening ? "До відкриття: " + opening : "",
    ].filter(Boolean).join(" · ");
    reasonField.hidden = !closing;
  }

  function goTo(target) {
    const year = target.getUTCFullYear();
    if (year < 1 || year > 9999) return false;
    month = dateAt(year, target.getUTCMonth());
    draft.month = dateKey(month).slice(0, 7);
    persistDraft();
    renderCalendar();
    return true;
  }

  calendar.querySelectorAll("[data-calendar-move]").forEach((button) => {
    button.addEventListener("click", () => {
      goTo(dateAt(month.getUTCFullYear(), month.getUTCMonth() + Number(button.dataset.calendarMove)));
    });
  });
  const focusDate = (iso) => daysGrid.querySelector('[data-calendar-day="' + iso + '"]')?.focus();
  daysGrid.addEventListener("click", (event) => {
    const iso = event.target.closest("[data-calendar-day]")?.dataset.calendarDay;
    if (!iso) return;
    if (Object.hasOwn(draft.days, iso)) delete draft.days[iso];
    else if (closed[iso]) {
      const { id, version } = closed[iso];
      draft.days[iso] = { id, version };
    } else draft.days[iso] = null;
    persistDraft();
    renderCalendar();
    focusDate(iso);
  });
  daysGrid.addEventListener("keydown", (event) => {
    const step = { ArrowLeft: -1, ArrowRight: 1, ArrowUp: -7, ArrowDown: 7 }[event.key];
    const iso = event.target.dataset.calendarDay;
    if (!step || !iso) return;
    event.preventDefault();
    const target = fromISO(iso);
    target.setUTCDate(target.getUTCDate() + step);
    if (target.getUTCMonth() !== month.getUTCMonth() && !goTo(target)) return;
    focusDate(dateKey(target));
  });
  reason.addEventListener("input", () => {
    draft.reason = reason.value;
    persistDraft();
  });
  renderCalendar();
}

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

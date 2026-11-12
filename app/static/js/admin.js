const csrfToken = document.querySelector('meta[name="csrf-token"]').content;
const saveButton = document.querySelector("[data-admin-save]");
const discardButton = document.querySelector("[data-admin-discard]");
const saveMessage = document.querySelector("[data-save-message]");
const storageKey = "optika.admin.changes.v4";
const blankDraft = (month = null) => ({
  edits: {}, additions: [], forms: {}, orders: {}, removals: {}, days: {}, reason: "",
  schedule: null, month,
});
let draft = blankDraft();
let saving = false;

try {
  const current = sessionStorage.getItem(storageKey);
  draft = { ...draft, ...JSON.parse(current ?? sessionStorage.getItem("optika.admin.changes.v3")) };
  if (!current) {
    delete draft.forms.time_slots;
    delete draft.orders.time_slot_order;
    draft.additions = draft.additions.filter((item) => item.kind !== "time_slots");
    for (const entries of [draft.edits, draft.removals]) {
      for (const [key, item] of Object.entries(entries)) {
        if (item.kind === "time_slots") delete entries[key];
      }
    }
  }
} catch {
  // Editing still works within this page if browser storage is unavailable.
}

function editableFields(form) {
  return [...form.querySelectorAll('input[name]:not([type="hidden"]), select[name], textarea[name]')];
}

function weekdayFields(form) {
  return [...form.querySelectorAll("[data-weekday]")];
}

function valuesOf(form) {
  const values = Object.fromEntries(editableFields(form).map((field) => [field.name, field.value]));
  const days = weekdayFields(form);
  if (days.length) {
    values.weekdays_mask = days.reduce((mask, day) => mask | (day.checked ? Number(day.value) : 0), 0);
  }
  return values;
}

function fillFields(form, values) {
  editableFields(form).forEach((field) => {
    if (field.name in values) field.value = values[field.name];
  });
  if ("weekdays_mask" in values) {
    weekdayFields(form).forEach((day) => {
      day.checked = Boolean(values.weekdays_mask & Number(day.value));
    });
  }
}

function matches(left, right) {
  return JSON.stringify(left) === JSON.stringify(right);
}

function activeEdits() {
  return Object.entries(draft.edits).filter(([key]) => !draft.removals[key]).map(([, edit]) => edit);
}

function numberOfChanges() {
  return activeEdits().length + draft.additions.length + Object.keys(draft.forms).length
    + Object.keys(draft.orders).length + Object.keys(draft.removals).length
    + Object.keys(draft.days).length + Number(Boolean(draft.schedule));
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
  const identity = {
    id: kind === "categories" ? form.dataset.id : Number(form.dataset.id),
    version: form.dataset.version,
  };

  const existing = draft.edits[key];
  if (existing) {
    fillFields(form, existing.values);
    form.dataset.dirty = "";
  }

  const rememberEdit = () => {
    const values = valuesOf(form);
    const valid = form.checkValidity();
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
    const weekdays = form.querySelector("[data-weekdays]");
    if (weekdays) weekdays.disabled = pending;
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

function renderAdditions() {
  document.querySelectorAll("[data-pending-for]").forEach((list) => {
    const items = draft.additions.filter((item) => item.kind === list.dataset.pendingFor);
    list.hidden = !items.length;
    list.replaceChildren(...items.map((item) => {
      const row = document.createElement("li");
      const label = document.createElement("span");
      label.textContent = item.values.name;
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
    const valid = form.checkValidity();
    if (matches(initial, values)) delete draft.forms[kind];
    else draft.forms[kind] = { kind, values, valid };
    persistDraft();
  });
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    const values = valuesOf(form);
    if (!form.checkValidity()) {
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

const scheduleForm = document.querySelector("[data-work-schedule]");
if (scheduleForm) {
  const settings = JSON.parse(scheduleForm.dataset.workSchedule);
  const { version, start_time, end_time } = settings;
  const initial = { start_time, end_time, breaks: settings.breaks };
  const start = scheduleForm.elements.start_time;
  const end = scheduleForm.elements.end_time;
  const breaks = scheduleForm.querySelector("[data-work-breaks]");
  const template = scheduleForm.querySelector("[data-work-break]");
  const clear = scheduleForm.querySelector("[data-clear-schedule]");

  function addBreak(values) {
    const row = template.content.firstElementChild.cloneNode(true);
    row.querySelector("[name=break_start]").value = values?.start_time ?? "";
    row.querySelector("[name=break_end]").value = values?.end_time ?? "";
    breaks.append(row);
    return row;
  }

  const shown = draft.schedule?.values ?? initial;
  start.value = shown.start_time;
  end.value = shown.end_time;
  shown.breaks.forEach(addBreak);

  function readSchedule() {
    const pauses = [...breaks.children].map((row) => ({
      start: row.querySelector("[name=break_start]"),
      end: row.querySelector("[name=break_end]"),
    }));
    start.required = end.required = Boolean(start.value || end.value || pauses.length);
    clear.disabled = !start.required;
    end.setCustomValidity(start.value && end.value && end.value <= start.value
      ? "Кінець робочого дня має бути пізніше за початок." : "");
    let previousEnd = start.value;
    for (const pause of [...pauses].sort((a, b) => a.start.value.localeCompare(b.start.value))) {
      const from = pause.start.value;
      const to = pause.end.value;
      const invalid = from && to && (
        from <= start.value || to >= end.value || from >= to || from < previousEnd
      );
      pause.end.setCustomValidity(invalid ? "Перерва має бути всередині робочого дня й не перетинатися з іншими." : "");
      if (to > previousEnd) previousEnd = to;
    }
    return {
      start_time: start.value,
      end_time: end.value,
      breaks: pauses.map((pause) => ({ start_time: pause.start.value, end_time: pause.end.value })),
    };
  }

  function rememberSchedule() {
    const values = readSchedule();
    draft.schedule = matches(initial, values) ? null : {
      version: draft.schedule?.version ?? version, values, valid: scheduleForm.checkValidity(),
    };
    persistDraft();
  }

  readSchedule();
  scheduleForm.addEventListener("input", rememberSchedule);
  scheduleForm.addEventListener("submit", (event) => {
    event.preventDefault();
    saveButton.focus();
  });
  scheduleForm.querySelector("[data-add-break]").addEventListener("click", () => {
    const row = addBreak();
    rememberSchedule();
    row.querySelector("input").focus();
  });
  breaks.addEventListener("click", (event) => {
    const button = event.target.closest("[data-remove-break]");
    if (!button) return;
    button.closest(".admin-work-break").remove();
    rememberSchedule();
    scheduleForm.querySelector("[data-add-break]").focus();
  });
  clear.addEventListener("click", () => {
    start.value = end.value = "";
    breaks.replaceChildren();
    rememberSchedule();
    start.focus();
  });
}

function payloadOfDraft() {
  const payload = {
    appointments: [], services: [], remove_services: [],
    categories: [], products: [], remove_products: [],
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
  if (draft.schedule) {
    const { start_time, end_time, breaks } = draft.schedule.values;
    payload.work_schedule = {
      version: draft.schedule.version, workday: start_time ? { start_time, end_time } : null, breaks,
    };
  }
  return payload;
}

function clearDraft() {
  draft = blankDraft(draft.month);
  persistDraft();
}

saveButton.addEventListener("click", async () => {
  if (saving || !numberOfChanges()) return;
  if (draft.schedule?.valid === false) {
    scheduleForm?.reportValidity();
    showSaveState("Перевірте початок, кінець і перерви робочого дня.", true);
    return;
  }
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

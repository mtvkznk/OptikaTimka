const csrfToken = document.querySelector('meta[name="csrf-token"]').content;
const saveButton = document.querySelector("[data-admin-save]");
const discardButton = document.querySelector("[data-admin-discard]");
const saveMessage = document.querySelector("[data-save-message]");
const storageKey = "optika.admin.changes.v4";
const blankDraft = (month = null) => ({
  edits: {}, additions: [], forms: {}, orders: {}, removals: {}, restorations: {}, days: {}, reason: "",
  schedule: null, serviceAvailability: {}, availabilityScope: "", month,
});
let draft = blankDraft();
let saving = false;
const processingPhotos = new Set();
const adminViews = [...document.querySelectorAll("[data-admin-view]")];
const adminLinks = [...document.querySelectorAll("[data-admin-nav]")];
let activeAdminView = adminViews[0];

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
  return [...form.querySelectorAll(
    'input[name]:not([type="hidden"]), select[name], textarea[name], [data-admin-value]'
  )];
}

function selectAdminView(view) {
  activeAdminView = view;
  adminViews.forEach((panel) => { panel.hidden = panel !== view; });
  adminLinks.forEach((link) => {
    if (link.dataset.adminNav === view.dataset.adminView) link.setAttribute("aria-current", "page");
    else link.removeAttribute("aria-current");
  });
}

function syncAdminView() {
  const target = document.getElementById(location.hash.slice(1));
  if (location.hash === "#admin-content") {
    selectAdminView(activeAdminView);
    return;
  }
  const view = target?.closest("[data-admin-view]") ?? adminViews[0];
  selectAdminView(view);
  if (location.hash) {
    requestAnimationFrame(() => {
      if (target && location.hash !== view.dataset.adminAnchor) target.scrollIntoView();
      else window.scrollTo(0, 0);
    });
  }
}

if (adminViews.length) {
  syncAdminView();
  window.addEventListener("hashchange", syncAdminView);
}

function revealEditor(form) {
  const view = form.closest("[data-admin-view]");
  if (view && view.hidden) {
    history.replaceState(null, "", view.dataset.adminAnchor);
    selectAdminView(view);
  }
  const details = form.closest("details");
  if (details) details.open = true;
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

function draftEntries() {
  return [...activeEdits(), ...draft.additions, ...Object.values(draft.forms)];
}

function renderCategoryOptions() {
  const additions = draft.additions.filter((item) => item.kind === "create_categories");
  document.querySelectorAll("[data-product-category]").forEach((select) => {
    const selected = select.value;
    select.querySelectorAll("[data-pending-category]").forEach((option) => option.remove());
    additions.forEach(({ identity, values }) => {
      const option = new Option(values.name, identity.id);
      option.dataset.pendingCategory = "";
      select.add(option);
    });
    select.value = selected;
  });
  const addProduct = document.querySelector("[data-add-product]");
  if (addProduct) {
    const select = addProduct.querySelector("[data-product-category]");
    addProduct.hidden = ![...select.options].some((option) => option.value);
  }
}

renderCategoryOptions();

function numberOfChanges() {
  return activeEdits().length + draft.additions.length + Object.keys(draft.forms).length
    + Object.keys(draft.orders).length + Object.keys(draft.removals).length
    + Object.keys(draft.restorations).length
    + Object.keys(draft.days).length + Number(Boolean(draft.schedule))
    + activeServiceAvailability().reduce(
      (count, [, entry]) => count + Number(Boolean(entry.schedule)) + Object.keys(entry.days).length,
      0,
    );
}

function activeServiceAvailability() {
  return Object.entries(draft.serviceAvailability).filter(([id]) => !draft.removals["services:" + id]);
}

function showSaveState(message, error = false) {
  const count = numberOfChanges();
  saveButton.disabled = saving || processingPhotos.size > 0 || !count;
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
    const card = form.closest(".admin-product-card");
    if (card) card.open = true;
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
    const photo = form.querySelector("[data-admin-photo]");
    if (photo) photo.disabled = pending;
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
  renderCategoryOptions();
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
        if (item.kind === "create_categories" && draftEntries().some(
          (entry) => entry.kind === "products" && entry.values.category === item.identity.id
        )) {
          showSaveState("Спершу змініть категорію в товарах, які її використовують.", true);
          return;
        }
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
  const identity = () => kind === "create_categories"
    ? draft.forms[kind]?.identity ?? { id: "category-" + crypto.randomUUID().replaceAll("-", "") }
    : undefined;
  if (draft.forms[kind]) {
    fillFields(form, draft.forms[kind].values);
    form.closest("details").open = true;
  }

  form.addEventListener("input", () => {
    const values = valuesOf(form);
    const valid = form.checkValidity();
    if (matches(initial, values)) delete draft.forms[kind];
    else draft.forms[kind] = { kind, identity: identity(), values, valid };
    persistDraft();
  });
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    if (processingPhotos.has(form)) return;
    const values = valuesOf(form);
    if (!form.checkValidity()) {
      form.reportValidity();
      return;
    }
    draft.additions.push({ kind, identity: identity(), values });
    delete draft.forms[kind];
    form.reset();
    renderAdditions();
    persistDraft();
  });
});

document.querySelectorAll("[data-restore-category]").forEach((button) => {
  const id = button.dataset.restoreCategory;
  const update = () => {
    const pending = Boolean(draft.restorations[id]);
    button.textContent = pending ? "Скасувати" : "Відновити";
    button.setAttribute("aria-label",
      (pending ? "Скасувати відновлення: " : "Відновити: ") + button.dataset.label);
    if (pending) button.closest("details").open = true;
  };
  update();
  button.addEventListener("click", () => {
    if (draft.restorations[id]) delete draft.restorations[id];
    else draft.restorations[id] = { id, version: button.dataset.version };
    update();
    persistDraft();
  });
});

async function encodeProductPhoto(file) {
  const bitmap = await createImageBitmap(file);
  try {
    const scale = Math.min(1, 1100 / Math.max(bitmap.width, bitmap.height));
    const canvas = document.createElement("canvas");
    canvas.width = Math.max(1, Math.round(bitmap.width * scale));
    canvas.height = Math.max(1, Math.round(bitmap.height * scale));
    const ctx = canvas.getContext("2d", { alpha: false });
    ctx.fillStyle = "#ffffff";
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    ctx.drawImage(bitmap, 0, 0, canvas.width, canvas.height);
    for (const quality of [0.82, 0.68, 0.52, 0.38]) {
      const url = canvas.toDataURL("image/jpeg", quality);
      const payload = url.split(",")[1];
      if (payload && payload.length <= 192 * 1024) return payload;
    }
    throw new Error("Photo too large");
  } finally {
    bitmap.close();
  }
}

document.querySelectorAll("[data-admin-photo]").forEach((editor) => {
  const form = editor.closest("form");
  const file = editor.querySelector("[data-photo-file]");
  const value = editor.querySelector("[data-admin-value]");
  const preview = editor.querySelector("[data-photo-preview]");
  const remove = editor.querySelector("[data-photo-remove]");
  const message = editor.querySelector("[data-photo-message]");
  const original = preview.getAttribute("src");
  let generation = 0;

  const render = () => {
    const photo = value.value === "remove" ? "" : value.value;
    const url = photo ? "data:image/jpeg;base64," + photo : value.value === "remove" ? "" : original;
    if (url) preview.src = url;
    else preview.removeAttribute("src");
    preview.hidden = !url;
    remove.hidden = !url;
    message.textContent = value.value === "remove" ? "Фото буде прибрано після збереження." : "";
  };
  const change = (payload) => {
    value.value = payload;
    value.dispatchEvent(new Event("input", { bubbles: true }));
    render();
  };

  render();
  form.addEventListener("reset", () => queueMicrotask(render));
  remove.addEventListener("click", () => change(original ? "remove" : ""));
  file.addEventListener("change", async () => {
    const selected = file.files[0];
    if (!selected) return;
    const request = ++generation;
    processingPhotos.add(form);
    remove.disabled = true;
    showSaveState();
    message.textContent = "Готуємо фото…";
    try {
      if (selected.size > 12 * 1024 * 1024) throw new Error("File too large");
      const payload = await encodeProductPhoto(selected);
      if (request === generation) change(payload);
    } catch {
      if (request === generation) message.textContent = "Виберіть менше або інше фото JPG, PNG чи WebP.";
    } finally {
      if (request === generation) {
        processingPhotos.delete(form);
        remove.disabled = false;
        file.value = "";
        showSaveState();
      }
    }
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
const scopePicker = document.querySelector("[data-availability-scope]");
const serviceAvailability = scheduleForm ? JSON.parse(scheduleForm.dataset.serviceAvailability) : {};
const availabilityPanel = scheduleForm?.closest(".admin-panel");
const globalSchedule = scheduleForm ? JSON.parse(scheduleForm.dataset.workSchedule) : null;
const globalDays = document.querySelector("[data-admin-calendar]");
const globalClosedDays = globalDays ? JSON.parse(globalDays.dataset.closedDays) : {};

function availabilityDraft() {
  const id = scopePicker.value;
  if (!id) return draft;
  return draft.serviceAvailability[id] ??= { schedule: null, days: {}, reason: "" };
}

function chooseAvailability(id) {
  scopePicker.value = id;
  if (scopePicker.selectedIndex < 0 || scopePicker.selectedOptions[0].disabled) scopePicker.value = "";
  draft.availabilityScope = scopePicker.value;
  availabilityPanel.dispatchEvent(new Event("availability-scope"));
  persistDraft();
}

if (scopePicker) {
  const weekdaysPanel = availabilityPanel.querySelector("[data-service-weekdays]");
  const serviceList = document.querySelector('[data-order="service_order"]');
  const updateChoices = () => {
    for (const option of scopePicker.options) {
      const form = option.value && serviceList.querySelector('[data-id="' + option.value + '"]');
      if (!form) continue;
      option.disabled = form.hasAttribute("data-removing");
      option.textContent = form.elements.name.value;
    }
    if (scopePicker.selectedOptions[0]?.disabled) chooseAvailability("");
  };
  serviceList.addEventListener("admin-removals", updateChoices);
  serviceList.addEventListener("input", updateChoices);
  serviceList.querySelectorAll("[data-service-schedule]").forEach((link) => {
    link.addEventListener("click", () => {
      chooseAvailability(link.dataset.serviceSchedule);
      scopePicker.focus({ preventScroll: true });
    });
  });
  availabilityPanel.addEventListener("availability-scope", () => {
    const form = scopePicker.value && serviceList.querySelector('[data-id="' + scopePicker.value + '"]');
    weekdaysPanel.replaceChildren();
    if (!form) return;
    const source = form.querySelector("[data-weekdays]");
    const copy = source.cloneNode(true);
    copy.hidden = false;
    const sourceDays = weekdayFields(source);
    weekdayFields(copy).forEach((field, index) => {
      field.checked = sourceDays[index].checked;
      field.addEventListener("input", () => {
        sourceDays[index].checked = field.checked;
        sourceDays[index].dispatchEvent(new Event("input", { bubbles: true }));
      });
    });
    weekdaysPanel.append(copy);
  });
  scopePicker.addEventListener("change", () => chooseAvailability(scopePicker.value));
  serviceList.querySelectorAll("[data-weekdays]").forEach((fieldset) => { fieldset.hidden = true; });
  queueMicrotask(updateChoices);
}

if (scheduleForm) {
  const start = scheduleForm.elements.start_time;
  const end = scheduleForm.elements.end_time;
  const breaks = scheduleForm.querySelector("[data-work-breaks]");
  const template = scheduleForm.querySelector("[data-work-break]");
  const clear = scheduleForm.querySelector("[data-clear-schedule]");
  const custom = scheduleForm.querySelector("[data-custom-schedule]");
  const fields = scheduleForm.querySelector("[data-schedule-fields]");

  function addBreak(values) {
    const row = template.content.firstElementChild.cloneNode(true);
    row.querySelector("[name=break_start]").value = values?.start_time ?? "";
    row.querySelector("[name=break_end]").value = values?.end_time ?? "";
    breaks.append(row);
    return row;
  }

  function hoursOf({ start_time, end_time, breaks }) {
    return { start_time, end_time, breaks };
  }

  function showHours(hours) {
    start.value = hours.start_time;
    end.value = hours.end_time;
    breaks.replaceChildren();
    hours.breaks.forEach(addBreak);
  }

  function showScope() {
    const saved = serviceAvailability[scopePicker.value];
    const pending = availabilityDraft().schedule;
    const inherited = draft.schedule?.values ?? hoursOf(globalSchedule);
    custom.closest("label").hidden = !saved;
    custom.checked = Boolean(pending?.custom ?? saved?.custom);
    fields.disabled = Boolean(saved && !custom.checked);
    showHours(fields.disabled ? inherited : pending?.values ?? hoursOf(saved?.schedule ?? globalSchedule));
    readSchedule();
  }

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
    const saved = serviceAvailability[scopePicker.value];
    const state = availabilityDraft();
    const values = readSchedule();
    const same = saved
      ? custom.checked === saved.custom && (!custom.checked || matches(hoursOf(saved.schedule), values))
      : matches(hoursOf(globalSchedule), values);
    state.schedule = same ? null : {
      version: state.schedule?.version ?? saved?.version ?? globalSchedule.version,
      custom: custom.checked, values, valid: scheduleForm.checkValidity(),
    };
    persistDraft();
  }

  availabilityPanel.addEventListener("availability-scope", showScope);
  scheduleForm.addEventListener("input", (event) => {
    if (event.target === custom) {
      fields.disabled = !custom.checked;
      const saved = serviceAvailability[scopePicker.value];
      if (custom.checked && saved.custom) showHours(hoursOf(saved.schedule));
      if (!custom.checked) showHours(draft.schedule?.values ?? hoursOf(globalSchedule));
    }
    rememberSchedule();
  });
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

function closedDaysPayload({ days, reason }) {
  const payload = { close: [], reopen: {}, reason };
  Object.entries(days).forEach(([day, reopening]) => {
    if (reopening) payload.reopen[reopening.id] = reopening.version;
    else payload.close.push(day);
  });
  return payload;
}

function hoursPayload({ start_time, end_time, breaks }) {
  return { workday: start_time ? { start_time, end_time } : null, breaks };
}

function payloadOfDraft() {
  const payload = {
    appointments: [], services: [], remove_services: [],
    categories: [], create_categories: [], remove_categories: [], products: [], remove_products: [],
    restore_categories: Object.values(draft.restorations),
    closed_days: closedDaysPayload(draft),
    service_availability: activeServiceAvailability()
      .filter(([, entry]) => entry.schedule || Object.keys(entry.days).length)
      .map(([id, entry]) => ({
        id: Number(id),
        closed_days: closedDaysPayload(entry),
        ...(entry.schedule && {
          schedule_version: entry.schedule.version,
          schedule: entry.schedule.custom ? hoursPayload(entry.schedule.values) : null,
        }),
      })),
    ...draft.orders,
  };
  draftEntries().forEach(({ kind, identity, values }) => {
    payload[kind].push({ ...identity, ...values });
  });
  Object.values(draft.removals).forEach(({ kind, identity }) => payload["remove_" + kind].push(identity));
  if (draft.schedule) {
    payload.work_schedule = { version: draft.schedule.version, ...hoursPayload(draft.schedule.values) };
  }
  return payload;
}

function clearDraft() {
  const scope = draft.availabilityScope;
  draft = blankDraft(draft.month);
  draft.availabilityScope = scope;
  persistDraft();
}

saveButton.addEventListener("click", async () => {
  if (saving || processingPhotos.size || !numberOfChanges()) return;
  const invalidSchedule = activeServiceAvailability().find(([, entry]) => entry.schedule?.valid === false);
  if (draft.schedule?.valid === false || invalidSchedule) {
    if (scheduleForm) {
      chooseAvailability(draft.schedule?.valid === false ? "" : invalidSchedule[0]);
      revealEditor(scheduleForm);
    }
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
      revealEditor(form);
      form.reportValidity();
    }
    showSaveState("У чернетці є неповні або некоректні поля.", true);
    return;
  }

  const main = document.querySelector(".admin-main");
  const header = document.querySelector(".admin-header");
  saving = true;
  main.inert = true;
  header.inert = true;
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
    header.inert = false;
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

  function renderCalendar() {
    const id = scopePicker.value;
    const closed = serviceAvailability[id]?.closed_days ?? globalClosedDays;
    const { days } = availabilityDraft();
    calendar.querySelector('[data-calendar-key="global"]').hidden = !id;
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
      const pending = Object.hasOwn(days, iso);
      const global = id && (Object.hasOwn(draft.days, iso) ? !draft.days[iso] : globalClosedDays[iso]);
      const state = pending ? (days[iso] ? "opening" : "closing")
        : current ? "closed" : global ? "global" : "";
      const label = { closed: "Закрито", closing: "Буде закрито", opening: "Буде відкрито",
        global: "Загальне закриття" }[state];
      const button = document.createElement("button");
      button.type = "button";
      button.className = "admin-calendar-day";
      button.dataset.calendarDay = iso;
      button.dataset.state = state;
      button.textContent = String(day);
      button.disabled = state === "global";
      button.setAttribute("aria-pressed", String(state === "closed" || state === "closing"));
      if (iso === today) button.setAttribute("aria-current", "date");
      button.setAttribute("aria-label", [
        dateFormat.format(date), label, global && state !== "global" ? "Загальне закриття" : "",
        current?.reason,
      ].filter(Boolean).join(". "));
      if (current?.reason) button.title = current.reason;
      cells.push(button);
    }
    daysGrid.replaceChildren(...cells);
    const pending = Object.values(days);
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
    const iso = event.target.closest("[data-calendar-day]:not(:disabled)")?.dataset.calendarDay;
    if (!iso) return;
    const { days } = availabilityDraft();
    const closed = serviceAvailability[scopePicker.value]?.closed_days ?? globalClosedDays;
    if (Object.hasOwn(days, iso)) delete days[iso];
    else if (closed[iso]) {
      const { id, version } = closed[iso];
      days[iso] = { id, version };
    } else days[iso] = null;
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
    availabilityDraft().reason = reason.value;
    persistDraft();
  });
  availabilityPanel.addEventListener("availability-scope", () => {
    reason.value = availabilityDraft().reason;
    renderCalendar();
  });
  chooseAvailability(draft.availabilityScope);
}

renderAdditions();
showSaveState();

window.addEventListener("pageshow", (event) => {
  if (event.persisted) location.reload();
});

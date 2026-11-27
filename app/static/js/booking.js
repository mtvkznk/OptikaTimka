const form = document.querySelector("[data-appointment-form]");
const statusNode = form.querySelector("[data-form-status]");
const submitButton = form.querySelector('button[type="submit"]');
const calendarMonths = [...form.querySelectorAll("[data-calendar-month]")];
const calendarTitle = form.querySelector("[data-calendar-title]");
const calendarPrev = form.querySelector("[data-calendar-prev]");
const calendarNext = form.querySelector("[data-calendar-next]");
const dateRadios = [...form.querySelectorAll('input[name="preferred_date"]')];
const serviceRadios = [...form.querySelectorAll('input[name="service"]')];
const timeGrid = form.querySelector("[data-time-slots]");
const timeOption = form.querySelector("[data-time-option]");
const availabilityStatus = form.querySelector("[data-availability-status]");
const availabilityRetry = form.querySelector("[data-availability-retry]");
const selection = form.querySelector("[data-booking-selection]");
const reviewHint = form.querySelector("[data-booking-confirmation-hint]");
const confirmation = document.querySelector("[data-booking-confirmation]");
const confirmationTitle = confirmation.querySelector("h2");
const confirmationHint = confirmation.querySelector("[data-confirmation-hint]");
const dateFormat = new Intl.DateTimeFormat("uk-UA", {
  day: "numeric",
  month: "long",
  timeZone: "UTC",
});

let calendarIndex = 0;
let availabilityRequest;
let displayedSlots = "[]";

function checked(name) {
  return form.querySelector('input[name="' + name + '"]:checked');
}

function formatDate(value) {
  return dateFormat.format(new Date(value + "T00:00:00Z"));
}

function timeInMinutes(value) {
  const [hours, minutes] = value.split(":").map(Number);
  return hours * 60 + minutes;
}

function bookingDetails() {
  const date = checked("preferred_date");
  const time = checked("preferred_time");
  return [
    checked("service")?.value,
    date && formatDate(date.value),
    time && time.closest("label").textContent.trim(),
  ].filter(Boolean).join(" · ");
}

function updateSelection() {
  const requiresConfirmation = checked("service")?.dataset.requiresConfirmation === "true";
  selection.textContent = bookingDetails();
  selection.hidden = !selection.textContent;
  reviewHint.hidden = !requiresConfirmation;
  submitButton.textContent = requiresConfirmation ? "Надіслати заявку" : "Записатися";
}

function setStatus(message, state = "") {
  statusNode.textContent = message;
  statusNode.dataset.state = state;
}

function showMonth(index) {
  calendarIndex = Math.max(0, Math.min(index, calendarMonths.length - 1));
  calendarMonths.forEach((month, position) => {
    month.hidden = position !== calendarIndex;
  });
  calendarTitle.textContent = calendarMonths[calendarIndex].dataset.monthTitle;
  calendarPrev.disabled = calendarIndex === 0;
  calendarNext.disabled = calendarIndex === calendarMonths.length - 1;
}

function setAvailableDates(dates) {
  const available = new Set(dates);
  for (const day of dateRadios) {
    day.disabled = !available.has(day.value);
    if (day.disabled) day.checked = false;
  }
}

function showTimeSlots(slots) {
  const signature = JSON.stringify(slots);
  if (displayedSlots === signature) return;
  displayedSlots = signature;
  const selected = checked("preferred_time");
  const options = slots.map((slot) => {
    const label = timeOption.content.firstElementChild.cloneNode(true);
    const input = label.querySelector("input");
    input.value = slot.start_time;
    input.dataset.duration = timeInMinutes(slot.end_time) - timeInMinutes(slot.start_time);
    input.checked = input.value === selected?.value && input.dataset.duration === selected?.dataset.duration;
    label.querySelector("span").textContent =
      slot.start_time.slice(0, 5) + " – " + slot.end_time.slice(0, 5);
    return label;
  });
  const middle = Math.ceil(options.length / 2);
  const columns = [options.slice(0, middle), options.slice(middle)]
    .filter((cells) => cells.length)
    .map((cells) => {
      const column = document.createElement("div");
      column.className = "time-column";
      column.append(...cells);
      return column;
    });
  timeGrid.replaceChildren(...columns);
  timeGrid.hidden = !options.length;
}

async function refreshAvailability(background = false) {
  if (background && availabilityRequest) return;
  availabilityRequest?.abort();
  const service = checked("service");
  const date = checked("preferred_date");
  if (!service) {
    availabilityRequest = undefined;
    setAvailableDates([]);
    showTimeSlots([]);
    availabilityRetry.hidden = true;
    availabilityStatus.textContent = serviceRadios.length && dateRadios.length
      ? "Оберіть послугу й дату, щоб побачити вільний час."
      : "Зараз немає доступних дат для запису.";
    updateSelection();
    return;
  }
  if (!background) {
    showTimeSlots([]);
    availabilityRetry.hidden = true;
    availabilityStatus.textContent = date ? "Завантажуємо вільні години…" : "Перевіряємо доступні дати…";
    updateSelection();
  }

  const request = new AbortController();
  availabilityRequest = request;
  try {
    const query = new URLSearchParams({ service: service.value });
    if (date) query.set("preferred_date", date.value);
    const response = await fetch(
      "/api/availability/calendar?" + query,
      { signal: request.signal },
    );
    if (!response.ok) throw new Error("Availability request failed");
    const { dates, slots } = await response.json();
    if (request.signal.aborted) return;
    setAvailableDates(dates);
    showTimeSlots(slots);
    availabilityRetry.hidden = true;
    let message = "Оберіть доступну дату, щоб побачити вільний час.";
    if (date && !date.disabled) {
      message = date.closest(".calendar-day").classList.contains("is-today")
        ? formatDate(date.value) + " · Вільні години, що залишилися на сьогодні"
        : formatDate(date.value);
    } else if (!dateRadios.some((day) => !day.disabled)) {
      message = "У доступному періоді немає дат для цієї послуги.";
    } else if (date) {
      message = "На цю дату вже немає вільного часу. Оберіть іншу дату.";
    }
    if (availabilityStatus.textContent !== message) availabilityStatus.textContent = message;
    updateSelection();
  } catch {
    if (request.signal.aborted || background) return;
    setAvailableDates([]);
    availabilityStatus.textContent = "Не вдалося перевірити вільні дати й години.";
    availabilityRetry.hidden = false;
    updateSelection();
  } finally {
    if (availabilityRequest === request) availabilityRequest = undefined;
  }
}

function refreshVisibleCalendar() {
  if (!document.hidden && !form.hidden && !submitButton.disabled && checked("service")) {
    refreshAvailability(true);
  }
}

calendarPrev.addEventListener("click", () => showMonth(calendarIndex - 1));
calendarNext.addEventListener("click", () => showMonth(calendarIndex + 1));
availabilityRetry.addEventListener("click", () => refreshAvailability());
document.addEventListener("visibilitychange", refreshVisibleCalendar);
window.addEventListener("focus", refreshVisibleCalendar);
setInterval(refreshVisibleCalendar, 15000);

form.addEventListener("change", (event) => {
  setStatus("");
  if (event.target.name === "service") {
    dateRadios.forEach((day) => { day.disabled = true; });
  }
  if (event.target.name === "preferred_date" || event.target.name === "service") {
    refreshAvailability();
  } else {
    updateSelection();
  }
});

function showError(message, target = statusNode) {
  setStatus(message, "error");
  target.focus();
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (submitButton.disabled) return;

  const service = checked("service");
  const date = checked("preferred_date");
  const time = checked("preferred_time");
  if (!service) {
    showError("Оберіть послугу.", serviceRadios[0]);
    return;
  }
  if (!date || date.disabled) {
    showError(
      "Оберіть доступну дату прийому.",
      calendarMonths[calendarIndex].querySelector("input:not(:disabled)") || statusNode,
    );
    return;
  }
  if (!time) {
    const firstTime = timeGrid.querySelector("input");
    showError(firstTime ? "Оберіть час прийому." : availabilityStatus.textContent, firstTime || statusNode);
    return;
  }
  if (!form.reportValidity()) return;

  const formData = new FormData(form);
  const name = formData.get("name").trim();
  const phone = formData.get("phone").trim();
  if (!name) {
    showError("Введіть ім’я та прізвище.", form.elements.namedItem("name"));
    return;
  }
  const details = bookingDetails();
  const fieldsets = [...form.querySelectorAll("fieldset")];
  fieldsets.forEach((fieldset) => { fieldset.disabled = true; });
  submitButton.disabled = true;
  setStatus("Зберігаємо запис…");

  try {
    const response = await fetch(form.action, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-CSRF-Token": formData.get("csrf_token"),
      },
      body: JSON.stringify({
        name,
        phone,
        service: service.value,
        preferred_date: date.value,
        preferred_time: time.value,
        expected_duration_minutes: Number(time.dataset.duration),
      }),
    });

    if (!response.ok) {
      const payload = await response.json().catch(() => null);
      if (response.status === 400 || response.status === 409) refreshAvailability();
      if (Array.isArray(payload?.detail) && payload.detail.some((error) => error.loc?.includes("phone"))) {
        fieldsets.forEach((fieldset) => { fieldset.disabled = false; });
        showError("Введіть номер у форматі 067 123 45 67 або +380 67 123 45 67.", form.elements.namedItem("phone"));
        return;
      }
      showError(typeof payload?.detail === "string"
        ? payload.detail : "Перевірте введені дані та повторіть спробу.");
      return;
    }

    const appointment = await response.json();
    const confirmed = appointment.status === "confirmed";
    confirmationTitle.textContent = confirmed ? "Запис підтверджено" : "Заявку прийнято";
    confirmationHint.hidden = confirmed;
    confirmation.querySelector("[data-confirmation-details]").textContent = details;
    form.hidden = true;
    confirmation.hidden = false;
    confirmationTitle.focus();
    form.reset();
    showMonth(0);
    refreshAvailability();
    setStatus("");
  } catch {
    showError("Не вдалося зберегти запис. Перевірте з’єднання та спробуйте ще раз.");
  } finally {
    fieldsets.forEach((fieldset) => { fieldset.disabled = false; });
    submitButton.disabled = false;
  }
});

confirmation.querySelector("[data-new-booking]").addEventListener("click", () => {
  confirmation.hidden = true;
  form.hidden = false;
  serviceRadios[0]?.focus();
});

const restoredMonth = checked("preferred_date")?.closest("[data-calendar-month]");
showMonth(calendarMonths.indexOf(restoredMonth));
submitButton.disabled = !serviceRadios.length || !dateRadios.length;
refreshAvailability();

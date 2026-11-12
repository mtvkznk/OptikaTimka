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
const confirmation = document.querySelector("[data-booking-confirmation]");
const dateFormat = new Intl.DateTimeFormat("uk-UA", {
  day: "numeric",
  month: "long",
  timeZone: "UTC",
});

let calendarIndex = 0;
let availabilityRequest;

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
  selection.textContent = bookingDetails();
  selection.hidden = !selection.textContent;
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

async function refreshAvailability() {
  availabilityRequest?.abort();
  const service = checked("service");
  for (const day of dateRadios) {
    day.disabled = Boolean(service && !(Number(service.dataset.weekdays) & Number(day.dataset.weekday)));
    if (day.disabled) day.checked = false;
  }
  const date = checked("preferred_date");
  timeGrid.replaceChildren();
  timeGrid.hidden = true;
  availabilityRetry.hidden = true;
  updateSelection();

  if (!date || !service) {
    if (service && !dateRadios.some((day) => !day.disabled)) {
      availabilityStatus.textContent = "У доступному періоді немає дат для цієї послуги.";
    } else {
      availabilityStatus.textContent = dateRadios.length
        ? "Оберіть послугу й дату, щоб побачити вільний час."
        : "Зараз немає доступних дат для запису.";
    }
    return;
  }

  const request = new AbortController();
  availabilityRequest = request;
  availabilityStatus.textContent = "Завантажуємо вільні години…";
  try {
    const query = new URLSearchParams({ preferred_date: date.value, service: service.value });
    const response = await fetch(
      "/api/availability?" + query,
      { signal: request.signal },
    );
    if (!response.ok) throw new Error("Availability request failed");
    const slots = await response.json();
    if (request.signal.aborted) return;
    if (!slots.length) {
      availabilityStatus.textContent = "На цю дату немає вільного часу для цієї послуги.";
      return;
    }

    const options = slots.map((slot) => {
      const label = timeOption.content.firstElementChild.cloneNode(true);
      const input = label.querySelector("input");
      input.value = slot.start_time;
      input.dataset.duration = timeInMinutes(slot.end_time) - timeInMinutes(slot.start_time);
      label.querySelector("span").textContent =
        slot.start_time.slice(0, 5) + " – " + slot.end_time.slice(0, 5);
      return label;
    });
    timeGrid.replaceChildren(...options);
    timeGrid.hidden = false;
    availabilityStatus.textContent = formatDate(date.value);
  } catch {
    if (request.signal.aborted) return;
    availabilityStatus.textContent = "Не вдалося завантажити вільні години.";
    availabilityRetry.hidden = false;
  }
}

calendarPrev.addEventListener("click", () => showMonth(calendarIndex - 1));
calendarNext.addEventListener("click", () => showMonth(calendarIndex + 1));
availabilityRetry.addEventListener("click", refreshAvailability);

form.addEventListener("change", (event) => {
  setStatus("");
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
  if (!date) {
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
  setStatus("Надсилаємо заявку…");

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

    if (response.status === 409) {
      const payload = await response.json().catch(() => null);
      refreshAvailability();
      showError(payload?.detail ?? "Цей час уже зайнятий. Оберіть інший час.");
      return;
    }
    if (!response.ok) {
      const payload = await response.json().catch(() => null);
      if (Array.isArray(payload?.detail) && payload.detail.some((error) => error.loc?.includes("phone"))) {
        fieldsets.forEach((fieldset) => { fieldset.disabled = false; });
        showError("Введіть номер із кодом країни, від 8 до 15 цифр.", form.elements.namedItem("phone"));
        return;
      }
      showError(typeof payload?.detail === "string"
        ? payload.detail : "Перевірте введені дані та повторіть спробу.");
      return;
    }

    confirmation.querySelector("[data-confirmation-details]").textContent = details;
    form.hidden = true;
    confirmation.hidden = false;
    confirmation.querySelector("h2").focus();
    form.reset();
    showMonth(0);
    refreshAvailability();
    setStatus("");
  } catch {
    showError("Не вдалося надіслати заявку. Перевірте з’єднання та спробуйте ще раз.");
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

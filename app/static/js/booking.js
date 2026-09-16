const form = document.querySelector("[data-appointment-form]");
const statusNode = document.querySelector("[data-form-status]");
const calendarCarousel = document.querySelector("[data-calendar-carousel]");
const calendarMonths = Array.from(document.querySelectorAll("[data-calendar-month]"));
const calendarPrev = document.querySelector("[data-calendar-prev]");
const calendarNext = document.querySelector("[data-calendar-next]");
const calendarCounter = document.querySelector("[data-calendar-counter]");
const servicePicker = document.querySelector("[data-service-picker]");
const serviceTrigger = document.querySelector("[data-service-trigger]");
const serviceSelected = document.querySelector("[data-service-selected]");
const serviceSelectedPrice = document.querySelector("[data-service-selected-price]");
const serviceOptions = Array.from(document.querySelectorAll("[data-service-option]"));
const serviceRadios = Array.from(document.querySelectorAll("[data-service-radio]"));

let activeCalendarMonth = 0;

function setStatus(message, type = "") {
  if (!statusNode) {
    return;
  }

  statusNode.textContent = message;
  statusNode.className = `form-status ${type}`.trim();
}

function setServiceMenu(open) {
  if (!servicePicker || !serviceTrigger) {
    return;
  }

  servicePicker.classList.toggle("is-open", open);
  serviceTrigger.setAttribute("aria-expanded", String(open));
}

function clearServiceSelection() {
  if (!serviceSelected || !serviceSelectedPrice) {
    return;
  }

  serviceSelected.textContent = "Оберіть послугу";
  serviceSelected.classList.add("is-placeholder");
  serviceSelectedPrice.textContent = "";
  serviceRadios.forEach((radio) => {
    radio.checked = false;
  });
  serviceOptions.forEach((option) => {
    option.classList.remove("is-selected");
    option.setAttribute("aria-selected", "false");
  });
}

function selectService(option) {
  if (!serviceSelected || !serviceSelectedPrice) {
    return;
  }

  const radio = option.querySelector("[data-service-radio]");
  if (radio) {
    radio.checked = true;
  }

  serviceSelected.textContent = option.dataset.serviceName || "";
  serviceSelected.classList.remove("is-placeholder");
  serviceSelectedPrice.textContent = option.dataset.servicePrice || "";

  serviceOptions.forEach((item) => {
    const isSelected = item === option;
    item.classList.toggle("is-selected", isSelected);
    item.setAttribute("aria-selected", String(isSelected));
  });

  setStatus("");
  setServiceMenu(false);
  serviceTrigger?.focus();
}

function setCalendarMonth(nextIndex) {
  if (!calendarCarousel || calendarMonths.length === 0) {
    return;
  }

  activeCalendarMonth = Math.min(Math.max(nextIndex, 0), calendarMonths.length - 1);

  calendarMonths.forEach((month, index) => {
    month.hidden = index !== activeCalendarMonth;
  });

  if (calendarPrev) {
    calendarPrev.disabled = activeCalendarMonth === 0;
  }

  if (calendarNext) {
    calendarNext.disabled = activeCalendarMonth === calendarMonths.length - 1;
  }

  if (calendarCounter) {
    calendarCounter.textContent = `${activeCalendarMonth + 1} / ${calendarMonths.length}`;
  }
}

calendarPrev?.addEventListener("click", () => {
  setCalendarMonth(activeCalendarMonth - 1);
});

calendarNext?.addEventListener("click", () => {
  setCalendarMonth(activeCalendarMonth + 1);
});

setCalendarMonth(0);
clearServiceSelection();

serviceTrigger?.addEventListener("click", () => {
  setServiceMenu(!servicePicker?.classList.contains("is-open"));
});

serviceOptions.forEach((option) => {
  option.addEventListener("click", () => {
    selectService(option);
  });

  option.addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      selectService(option);
    }
  });
});

document.addEventListener("click", (event) => {
  if (!servicePicker?.contains(event.target)) {
    setServiceMenu(false);
  }
});

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") {
    setServiceMenu(false);
    serviceTrigger?.focus();
  }
});

form?.addEventListener("submit", async (event) => {
  event.preventDefault();

  if (serviceRadios.length > 0 && !serviceRadios.some((radio) => radio.checked)) {
    setStatus("Оберіть послугу.", "is-error");
    setServiceMenu(true);
    serviceTrigger?.focus();
    return;
  }

  if (!form.reportValidity()) {
    return;
  }

  const submitButton = form.querySelector("button[type='submit']");
  const formData = new FormData(form);
  const payload = Object.fromEntries(formData.entries());

  submitButton.disabled = true;
  setStatus("Надсилаємо запис...");

  try {
    const response = await fetch(form.dataset.endpoint, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        ...payload,
        email: null,
        message: "",
      }),
    });

    if (!response.ok) {
      const errorPayload = await response.json().catch(() => null);
      throw new Error(errorPayload?.detail || "Request failed");
    }

    form.reset();
    clearServiceSelection();
    setCalendarMonth(0);
    setStatus("Запис прийнято. Ми зв'яжемося з вами для підтвердження.", "is-success");
  } catch (error) {
    setStatus(error.message || "Не вдалося створити запис. Спробуйте ще раз.", "is-error");
  } finally {
    submitButton.disabled = false;
  }
});

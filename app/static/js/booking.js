const form = document.querySelector("[data-appointment-form]");
const statusNode = document.querySelector("[data-form-status]");
const calendarCarousel = document.querySelector("[data-calendar-carousel]");
const calendarMonths = Array.from(document.querySelectorAll("[data-calendar-month]"));
const calendarPrev = document.querySelector("[data-calendar-prev]");
const calendarNext = document.querySelector("[data-calendar-next]");
const calendarCounter = document.querySelector("[data-calendar-counter]");

let activeCalendarMonth = 0;

function setStatus(message, type = "") {
  if (!statusNode) {
    return;
  }

  statusNode.textContent = message;
  statusNode.className = `form-status ${type}`.trim();
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

form?.addEventListener("submit", async (event) => {
  event.preventDefault();

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
    setCalendarMonth(0);
    setStatus("Запис прийнято. Ми зв'яжемося з вами для підтвердження.", "is-success");
  } catch (error) {
    setStatus(error.message || "Не вдалося створити запис. Спробуйте ще раз.", "is-error");
  } finally {
    submitButton.disabled = false;
  }
});

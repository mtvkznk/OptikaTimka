const form = document.querySelector("[data-appointment-form]");
const statusNode = document.querySelector("[data-form-status]");
const configNode = document.querySelector("#booking-config");
const bookingConfig = configNode ? JSON.parse(configNode.textContent || "{}") : {};
const closedDates = new Set(bookingConfig.closedDates || []);
const dateInput = form?.querySelector("input[name='preferred_date']");

function setStatus(message, type = "") {
  if (!statusNode) {
    return;
  }

  statusNode.textContent = message;
  statusNode.className = `form-status ${type}`.trim();
}

function validateDate() {
  if (!dateInput || !dateInput.value) {
    return true;
  }

  if (closedDates.has(dateInput.value)) {
    dateInput.setCustomValidity("На цю дату запис закритий.");
    setStatus("На цю дату запис закритий. Оберіть інший день.", "is-error");
    return false;
  }

  dateInput.setCustomValidity("");
  return true;
}

dateInput?.addEventListener("change", validateDate);

form?.addEventListener("submit", async (event) => {
  event.preventDefault();

  validateDate();
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
    setStatus("Запис прийнято. Ми зв'яжемося з вами для підтвердження.", "is-success");
  } catch (error) {
    setStatus(error.message || "Не вдалося створити запис. Спробуйте ще раз.", "is-error");
  } finally {
    submitButton.disabled = false;
  }
});

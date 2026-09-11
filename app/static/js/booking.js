const form = document.querySelector("[data-appointment-form]");
const statusNode = document.querySelector("[data-form-status]");

function setStatus(message, type = "") {
  statusNode.textContent = message;
  statusNode.className = `form-status ${type}`.trim();
}

form?.addEventListener("submit", async (event) => {
  event.preventDefault();

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
      throw new Error("Request failed");
    }

    form.reset();
    setStatus("Запис прийнято. Ми зв'яжемося з вами для підтвердження.", "is-success");
  } catch {
    setStatus("Не вдалося створити запис. Спробуйте ще раз.", "is-error");
  } finally {
    submitButton.disabled = false;
  }
});

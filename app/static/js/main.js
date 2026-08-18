const header = document.querySelector(".site-header");
const menuButton = document.querySelector(".menu-button");
const form = document.querySelector("[data-appointment-form]");
const statusNode = document.querySelector(".form-status");

menuButton?.addEventListener("click", () => {
  const isOpen = header.classList.toggle("menu-open");
  menuButton.setAttribute("aria-expanded", String(isOpen));
});

document.querySelectorAll(".main-nav a, .header-cta").forEach((link) => {
  link.addEventListener("click", () => {
    header.classList.remove("menu-open");
    menuButton?.setAttribute("aria-expanded", "false");
  });
});

form?.addEventListener("submit", async (event) => {
  event.preventDefault();
  statusNode.textContent = "Надсилаємо...";

  const formData = new FormData(form);
  const payload = {
    name: formData.get("name"),
    phone: formData.get("phone"),
    service_id: formData.get("service_id") ? Number(formData.get("service_id")) : null,
    message: formData.get("message") || null,
  };

  try {
    const response = await fetch("/api/appointments", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });

    if (!response.ok) {
      throw new Error("Request failed");
    }

    form.reset();
    statusNode.textContent = "Дякуємо! Заявку прийнято.";
  } catch {
    statusNode.textContent = "Не вдалося надіслати заявку. Спробуйте ще раз.";
  }
});

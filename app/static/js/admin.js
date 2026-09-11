const reorderLists = document.querySelectorAll("[data-reorder-list]");

function getDragAfterElement(list, pointerY) {
  const draggableItems = [
    ...list.querySelectorAll("[data-reorder-item]:not(.is-dragging)"),
  ];

  return draggableItems.reduce(
    (closest, child) => {
      const box = child.getBoundingClientRect();
      const offset = pointerY - box.top - box.height / 2;

      if (offset < 0 && offset > closest.offset) {
        return { offset, element: child };
      }

      return closest;
    },
    { offset: Number.NEGATIVE_INFINITY, element: null },
  ).element;
}

function setReorderStatus(list, message, type = "") {
  const statusKey = list.dataset.reorderStatus;
  const statusNode = document.querySelector(`[data-reorder-status-for="${statusKey}"]`);

  if (!statusNode) {
    return;
  }

  statusNode.textContent = message;
  statusNode.className = `reorder-status ${type}`.trim();
}

function updateHiddenOrder(list) {
  list.querySelectorAll("[data-order-input]").forEach((input, index) => {
    input.value = String(index + 1);
  });
}

async function persistOrder(list) {
  updateHiddenOrder(list);
  setReorderStatus(list, "Зберігаємо порядок...");

  const ids = [...list.querySelectorAll("[data-reorder-item]")].map((item) =>
    Number(item.dataset.id),
  );

  try {
    const response = await fetch(list.dataset.reorderEndpoint, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ ids }),
    });

    if (!response.ok) {
      throw new Error("Order request failed");
    }

    setReorderStatus(list, "Порядок збережено.", "is-success");
  } catch {
    setReorderStatus(list, "Не вдалося зберегти порядок. Оновіть сторінку.", "is-error");
  }
}

reorderLists.forEach((list) => {
  list.addEventListener("dragstart", (event) => {
    const row = event.target.closest("[data-reorder-item]");
    if (!row || !event.target.closest(".drag-handle")) {
      event.preventDefault();
      return;
    }

    row.classList.add("is-dragging");
    event.dataTransfer.effectAllowed = "move";
    event.dataTransfer.setData("text/plain", row.dataset.id);
  });

  list.addEventListener("dragover", (event) => {
    event.preventDefault();
    const draggingRow = list.querySelector(".is-dragging");
    if (!draggingRow) {
      return;
    }

    const afterElement = getDragAfterElement(list, event.clientY);
    if (afterElement) {
      list.insertBefore(draggingRow, afterElement);
    } else {
      list.appendChild(draggingRow);
    }
  });

  list.addEventListener("dragend", (event) => {
    const row = event.target.closest("[data-reorder-item]");
    if (!row) {
      return;
    }

    row.classList.remove("is-dragging");
    persistOrder(list);
  });
});

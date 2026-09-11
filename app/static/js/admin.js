const reorderLists = document.querySelectorAll("[data-reorder-list]");
let activeDrag = null;

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

function moveRow(list, row, pointerY) {
  const afterElement = getDragAfterElement(list, pointerY);
  if (afterElement) {
    list.insertBefore(row, afterElement);
  } else {
    list.appendChild(row);
  }
}

function startDrag(event) {
  const handle = event.target.closest(".drag-handle");
  const row = handle?.closest("[data-reorder-item]");
  const list = row?.closest("[data-reorder-list]");

  if (!handle || !row || !list) {
    return;
  }

  event.preventDefault();
  handle.setPointerCapture(event.pointerId);
  activeDrag = {
    handle,
    list,
    row,
    pointerId: event.pointerId,
    startY: event.clientY,
    moved: false,
  };
  document.body.classList.add("is-reordering");
  row.classList.add("is-dragging");
}

function updateDrag(event) {
  if (!activeDrag || event.pointerId !== activeDrag.pointerId) {
    return;
  }

  event.preventDefault();
  if (Math.abs(event.clientY - activeDrag.startY) > 4) {
    activeDrag.moved = true;
  }

  moveRow(activeDrag.list, activeDrag.row, event.clientY);
}

function finishDrag(event) {
  if (!activeDrag || event.pointerId !== activeDrag.pointerId) {
    return;
  }

  event.preventDefault();
  const finishedDrag = activeDrag;
  activeDrag = null;
  finishedDrag.row.classList.remove("is-dragging");
  document.body.classList.remove("is-reordering");

  if (finishedDrag.handle.hasPointerCapture(event.pointerId)) {
    finishedDrag.handle.releasePointerCapture(event.pointerId);
  }

  if (finishedDrag.moved) {
    persistOrder(finishedDrag.list);
  }
}

function moveWithKeyboard(event) {
  if (event.key !== "ArrowUp" && event.key !== "ArrowDown") {
    return;
  }

  const handle = event.target.closest(".drag-handle");
  const row = handle?.closest("[data-reorder-item]");
  const list = row?.closest("[data-reorder-list]");

  if (!handle || !row || !list) {
    return;
  }

  event.preventDefault();
  if (event.key === "ArrowUp" && row.previousElementSibling) {
    list.insertBefore(row, row.previousElementSibling);
    persistOrder(list);
  }

  if (event.key === "ArrowDown" && row.nextElementSibling) {
    list.insertBefore(row.nextElementSibling, row);
    persistOrder(list);
  }
}

reorderLists.forEach((list) => {
  list.addEventListener("pointerdown", startDrag);
  list.addEventListener("keydown", moveWithKeyboard);
});

document.addEventListener("pointermove", updateDrag);
document.addEventListener("pointerup", finishDrag);
document.addEventListener("pointercancel", finishDrag);

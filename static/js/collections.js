(() => {
  document.addEventListener("click", (event) => {
    const visibilityButton = event.target.closest("[data-toggle-pickup-pin]");
    if (visibilityButton) {
      const input = document.getElementById(visibilityButton.dataset.togglePickupPin);
      if (input) {
        const reveal = input.type === "password";
        input.type = reveal ? "text" : "password";
        visibilityButton.textContent = reveal ? "Hide PIN" : "Show PIN";
        visibilityButton.setAttribute("aria-pressed", String(reveal));
        input.focus();
      }
      return;
    }

    const openButton = event.target.closest("[data-open-pickup-pin]");
    if (openButton) {
      const dialog = document.getElementById(openButton.dataset.openPickupPin);
      if (dialog?.showModal) dialog.showModal();
      else if (dialog) dialog.setAttribute("open", "");
      dialog?.querySelector("input[name=pickup_pin]")?.focus();
      return;
    }

    const closeButton = event.target.closest("[data-close-pickup-pin]");
    if (closeButton) {
      const dialog = closeButton.closest("dialog");
      if (dialog?.close) dialog.close();
      else dialog?.removeAttribute("open");
    }
  });

  document.querySelectorAll(".pickup-pin-dialog").forEach((dialog) => {
    dialog.addEventListener("close", () => {
      dialog.querySelector("form")?.reset();
      const pinInput = dialog.querySelector("input[name=pickup_pin]");
      const visibilityButton = dialog.querySelector("[data-toggle-pickup-pin]");
      if (pinInput) pinInput.type = "password";
      if (visibilityButton) {
        visibilityButton.textContent = "Show PIN";
        visibilityButton.setAttribute("aria-pressed", "false");
      }
    });
  });
})();

(() => {
  const notificationLink = document.querySelector(".notification-link");
  const feedback = document.querySelector("[data-notification-feedback]");

  function updateUnreadCount(value) {
    const count = Math.max(0, Number(value) || 0);
    if (!notificationLink) return;
    notificationLink.dataset.unreadCount = String(count);
    notificationLink.setAttribute("aria-label", count ? `Open notifications, ${count} unread` : "Open notifications");
    let badge = notificationLink.querySelector(".notification-badge");
    if (count) {
      if (!badge) {
        badge = document.createElement("span");
        badge.className = "notification-badge";
        badge.setAttribute("aria-hidden", "true");
        notificationLink.append(badge);
      }
      badge.textContent = count < 100 ? String(count) : "99+";
    } else {
      badge?.remove();
    }

    let headingCount = document.querySelector(".unread-heading-count");
    if (count) {
      if (!headingCount) {
        headingCount = document.createElement("span");
        headingCount.className = "unread-heading-count";
        document.querySelector(".page-heading h1")?.append(" ", headingCount);
      }
      headingCount.textContent = `${count} unread`;
    } else {
      headingCount?.remove();
      document.querySelector("[data-notification-read-all]")?.remove();
    }
  }

  function markCardRead(card) {
    if (!card) return;
    card.classList.remove("is-unread");
    const status = card.querySelector("[data-notification-status]");
    if (status) {
      status.textContent = "Read";
      status.classList.remove("status-pending");
      status.classList.add("status-active");
    }
    card.querySelector("[data-notification-read]")?.remove();
  }

  document.addEventListener("submit", async (event) => {
    const form = event.target;
    if (!(form instanceof HTMLFormElement) || !form.matches("[data-notification-read], [data-notification-read-all]")) return;
    event.preventDefault();
    const button = form.querySelector("button[type=submit]");
    if (button) button.disabled = true;

    try {
      const response = await fetch(form.action, {
        method: "POST",
        headers: { "X-Requested-With": "XMLHttpRequest", "Accept": "application/json" },
        credentials: "same-origin",
      });
      const result = await response.json();
      if (!response.ok || !result.ok) throw new Error(result.message || "Notification update failed.");

      if (form.matches("[data-notification-read-all]")) {
        document.querySelectorAll("[data-notification-card].is-unread").forEach(markCardRead);
        updateUnreadCount(0);
      } else {
        const card = form.closest("[data-notification-card]");
        const wasUnread = card?.classList.contains("is-unread");
        markCardRead(card);
        if (wasUnread && result.changed) {
          const current = Number(notificationLink?.dataset.unreadCount || 0);
          updateUnreadCount(Math.max(0, current - 1));
        }
      }
      if (feedback) feedback.textContent = result.message || "Notification updated.";
    } catch (error) {
      form.submit();
    }
  });
})();

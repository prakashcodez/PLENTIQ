(() => {
  const toApplicationServerKey = (value) => {
    const padding = "=".repeat((4 - (value.length % 4)) % 4);
    const base64 = (value + padding).replace(/-/g, "+").replace(/_/g, "/");
    const raw = window.atob(base64);
    return Uint8Array.from(raw, (character) => character.charCodeAt(0));
  };

  document.addEventListener("DOMContentLoaded", async () => {
    const panel = document.querySelector("[data-push-notifications]");
    if (!panel) return;
    const status = panel.querySelector("[data-push-status]");
    const enableButton = panel.querySelector("[data-push-enable]");
    const disableButton = panel.querySelector("[data-push-disable]");
    const setStatus = (message) => { if (status) status.textContent = message; };

    if (!("serviceWorker" in navigator) || !("PushManager" in window) || !("Notification" in window)) {
      setStatus("This browser does not support web push notifications.");
      enableButton.disabled = true;
      return;
    }

    let configuration;
    let registration;
    try {
      const response = await fetch("/api/push/public-key", { credentials: "same-origin" });
      configuration = await response.json();
      if (!response.ok || !configuration.enabled) {
        setStatus(configuration.message || "Browser notifications are not configured yet.");
        enableButton.disabled = true;
        return;
      }
      registration = await navigator.serviceWorker.register("/service-worker.js", { scope: "/" });
      await navigator.serviceWorker.ready;
      const existing = await registration.pushManager.getSubscription();
      if (existing) {
        enableButton.hidden = true;
        disableButton.hidden = false;
        setStatus("Browser notifications are enabled on this device.");
      } else {
        setStatus("Enable alerts on this device to receive Plentiq updates.");
      }
    } catch (_error) {
      setStatus("Could not prepare browser notifications. Check the server and try again.");
      enableButton.disabled = true;
      return;
    }

    enableButton.addEventListener("click", async () => {
      enableButton.disabled = true;
      try {
        const permission = await Notification.requestPermission();
        if (permission !== "granted") {
          setStatus("Permission was not granted. You can enable it in this browser's site settings.");
          return;
        }
        const subscription = await registration.pushManager.subscribe({
          userVisibleOnly: true,
          applicationServerKey: toApplicationServerKey(configuration.public_key),
        });
        const response = await fetch("/api/push/subscribe", {
          method: "POST",
          credentials: "same-origin",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ subscription: subscription.toJSON() }),
        });
        const result = await response.json();
        if (!response.ok || !result.ok) throw new Error(result.message || "Subscription could not be saved.");
        enableButton.hidden = true;
        disableButton.hidden = false;
        setStatus(result.message);
      } catch (error) {
        setStatus(error.message || "Could not enable browser notifications.");
      } finally {
        enableButton.disabled = false;
      }
    });

    disableButton.addEventListener("click", async () => {
      disableButton.disabled = true;
      try {
        const subscription = await registration.pushManager.getSubscription();
        if (subscription) {
          const response = await fetch("/api/push/unsubscribe", {
            method: "POST",
            credentials: "same-origin",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ endpoint: subscription.endpoint }),
          });
          const result = await response.json();
          if (!response.ok || !result.ok) throw new Error(result.message || "Could not remove this device.");
          await subscription.unsubscribe();
        }
        disableButton.hidden = true;
        enableButton.hidden = false;
        setStatus("Browser notifications are disabled on this device.");
      } catch (error) {
        setStatus(error.message || "Could not disable browser notifications.");
      } finally {
        disableButton.disabled = false;
      }
    });
  });
})();

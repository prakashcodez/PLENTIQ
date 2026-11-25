document.addEventListener("DOMContentLoaded", () => {
  document.querySelectorAll("[data-location-picker]").forEach((picker) => {
    const latitudeInput = picker.querySelector("[data-location-latitude]");
    const longitudeInput = picker.querySelector("[data-location-longitude]");
    const button = picker.querySelector("[data-current-location]");
    const status = picker.querySelector("[data-location-status]");
    const frame = picker.querySelector("[data-map-frame]");
    const empty = picker.querySelector("[data-map-empty]");
    const mapLink = picker.querySelector("[data-location-map-link]");

    const getAddress = () => [...picker.querySelectorAll("[data-location-search]")]
      .map((input) => input.value.trim())
      .filter(Boolean)
      .join(", ");

    const updateMap = () => {
      const latitude = Number(latitudeInput?.value);
      const longitude = Number(longitudeInput?.value);
      const hasCoordinates = latitudeInput?.value.trim() !== ""
        && longitudeInput?.value.trim() !== ""
        && Number.isFinite(latitude) && Number.isFinite(longitude)
        && latitude >= -90 && latitude <= 90
        && longitude >= -180 && longitude <= 180;

      if (mapLink) {
        if (hasCoordinates) {
          mapLink.href = `https://www.openstreetmap.org/?mlat=${latitude}&mlon=${longitude}#map=16/${latitude}/${longitude}`;
          mapLink.setAttribute("aria-label", "Open the selected location in OpenStreetMap");
        } else {
          const query = getAddress();
          mapLink.href = query
            ? `https://www.openstreetmap.org/search?query=${encodeURIComponent(query)}`
            : "https://www.openstreetmap.org/";
          mapLink.setAttribute("aria-label", query ? "Search this address in OpenStreetMap" : "Open OpenStreetMap");
        }
      }

      if (!frame || !empty) return;
      if (!hasCoordinates) {
        frame.hidden = true;
        frame.removeAttribute("src");
        empty.hidden = false;
        return;
      }

      const padding = 0.012;
      const mapUrl = new URL("https://www.openstreetmap.org/export/embed.html");
      mapUrl.searchParams.set("bbox", [
        Math.max(-180, longitude - padding),
        Math.max(-90, latitude - padding),
        Math.min(180, longitude + padding),
        Math.min(90, latitude + padding),
      ].map((value) => value.toFixed(6)).join(","));
      mapUrl.searchParams.set("layer", "mapnik");
      mapUrl.searchParams.set("marker", `${latitude.toFixed(6)},${longitude.toFixed(6)}`);
      const url = mapUrl.toString();
      if (frame.getAttribute("src") !== url) frame.src = url;
      frame.hidden = false;
      empty.hidden = true;
    };

    [latitudeInput, longitudeInput, ...picker.querySelectorAll("[data-location-search]")]
      .filter(Boolean)
      .forEach((input) => {
        input.addEventListener("input", updateMap);
        input.addEventListener("change", updateMap);
      });

    button?.addEventListener("click", () => {
      if (!navigator.geolocation) {
        if (status) status.textContent = "This browser cannot read device location. Enter both coordinates manually.";
        return;
      }
      button.disabled = true;
      if (status) status.textContent = "Waiting for location permission…";
      navigator.geolocation.getCurrentPosition((position) => {
        latitudeInput.value = position.coords.latitude.toFixed(6);
        longitudeInput.value = position.coords.longitude.toFixed(6);
        [latitudeInput, longitudeInput].forEach((input) => {
          input.dispatchEvent(new Event("input", { bubbles: true }));
          input.dispatchEvent(new Event("change", { bubbles: true }));
        });
        if (status) status.textContent = "Current location selected. Check the map, then save the form.";
        button.disabled = false;
        updateMap();
      }, (error) => {
        const messages = {
          1: "Location access was denied. Allow it in the browser or enter both coordinates manually.",
          2: "Device location is unavailable. Try again or enter both coordinates manually.",
          3: "Location lookup timed out. Try again or enter both coordinates manually.",
        };
        if (status) status.textContent = messages[error.code] || "Location lookup failed. Enter both coordinates manually.";
        button.disabled = false;
      }, { enableHighAccuracy: true, timeout: 15000, maximumAge: 30000 });
    });

    updateMap();
  });
});

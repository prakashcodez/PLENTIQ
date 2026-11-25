(() => {
  document.addEventListener("submit", (event) => {
    const submittedForm = event.target;
    if (!(submittedForm instanceof HTMLFormElement) || !submittedForm.matches("[data-confirm-decline]")) return;
    if (!window.confirm("Decline this request? It cannot be changed after submission.")) event.preventDefault();
  });

  const formatDate = (value) => {
    if (!value) return "Not entered";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return value;
    return new Intl.DateTimeFormat(undefined, {
      dateStyle: "medium",
      timeStyle: "short",
    }).format(date);
  };

  document.addEventListener("error", (event) => {
    const image = event.target;
    if (!(image instanceof HTMLImageElement) || !image.matches("[data-food-image], [data-food-preview]")) return;
    const fallback = image.dataset.fallback || image.dataset.defaultSrc;
    if (fallback && image.src !== new URL(fallback, window.location.href).href) image.src = fallback;
  }, true);

  const form = document.querySelector("[data-donation-form]");
  const wizard = document.querySelector("[data-create-wizard]");
  if (!form || !wizard) return;

  const steps = ["food", "pickup", "contact", "review"];
  const categories = [
    "Main Course", "Snacks", "Bakery Items", "Fruits and Vegetables", "Beverages",
    "Dairy Products", "Sweets and Desserts", "Packaged Foods", "Other",
  ];
  const units = ["Portions", "Packets", "Pieces", "Kilograms", "Grams", "Boxes", "Litres"];
  const feedback = form.querySelector("[data-wizard-feedback]");
  const itemList = form.querySelector("[data-item-list]");
  const addItemButton = form.querySelector("[data-add-item]");
  let currentStep = wizard.dataset.currentStep || "food";
  let submissionInProgress = false;

  function controlFor(field) {
    return Array.from(form.querySelectorAll("[data-field]")).find((control) => control.dataset.field === field);
  }

  function clearErrors() {
    form.querySelectorAll("[data-field-error], .field-error[data-error-for]").forEach((element) => {
      element.textContent = "";
    });
    form.querySelectorAll("[data-field][aria-invalid]").forEach((control) => control.setAttribute("aria-invalid", "false"));
    feedback.querySelector("[data-client-errors]")?.remove();
    feedback.querySelector("[data-server-errors]")?.remove();
  }

  function showErrors(errors) {
    clearErrors();
    if (!errors.length) return;

    const summary = document.createElement("div");
    summary.className = "form-errors";
    summary.dataset.clientErrors = "";
    summary.setAttribute("role", "alert");
    const title = document.createElement("strong");
    title.textContent = "Please review these details:";
    const list = document.createElement("ul");
    const seen = new Set();

    errors.forEach((error) => {
      const message = error.message || "Review this field.";
      if (!seen.has(message)) {
        seen.add(message);
        const item = document.createElement("li");
        item.textContent = message;
        list.append(item);
      }
      const fieldMessage = form.querySelector(`[data-error-for="${CSS.escape(error.field)}"]`);
      if (fieldMessage) fieldMessage.textContent = message;
      form.querySelectorAll(`[data-field="${CSS.escape(error.field)}"]`).forEach((control) => control.setAttribute("aria-invalid", "true"));
    });

    summary.append(title, list);
    feedback.prepend(summary);
  }

  function focusFirstError(errors) {
    if (!errors.length) return;
    const control = controlFor(errors[0].field);
    const collapsedBody = control?.closest("[data-item-body][hidden]");
    if (collapsedBody) {
      collapsedBody.hidden = false;
      const toggle = collapsedBody.closest("[data-food-item]")?.querySelector("[data-toggle-item]");
      if (toggle) toggle.setAttribute("aria-expanded", "true");
    }
    if (control) {
      control.scrollIntoView({ block: "center", behavior: "smooth" });
      control.focus({ preventScroll: true });
    }
  }

  function setStep(step) {
    if (!steps.includes(step)) return;
    currentStep = step;
    wizard.dataset.currentStep = step;
    const stepIndex = steps.indexOf(step);

    form.querySelectorAll("[data-step-panel]").forEach((panel) => {
      panel.hidden = panel.dataset.stepPanel !== step;
    });
    wizard.querySelectorAll("[data-step-aside]").forEach((panel) => {
      panel.hidden = panel.dataset.stepAside !== step;
    });
    wizard.querySelectorAll("[data-progress-step]").forEach((item) => {
      const index = steps.indexOf(item.dataset.progressStep);
      item.classList.toggle("is-current", index === stepIndex);
      item.classList.toggle("is-complete", index < stepIndex);
      const button = item.querySelector("button");
      if (index === stepIndex) button.setAttribute("aria-current", "step");
      else button.removeAttribute("aria-current");
      button.disabled = index > stepIndex;
      const number = item.querySelector(".progress-number");
      if (number) number.textContent = String(index + 1);
    });

    if (step === "review") renderReview();
    updateSubmitState();
  }

  function fieldError(field, message, step) {
    return { field, message, step };
  }

  function collectNativeErrors(step) {
    const panel = form.querySelector(`[data-step-panel="${step}"]`);
    const errors = [];
    if (!panel) return errors;

    panel.querySelectorAll("input, select, textarea").forEach((control) => {
      if (!control.willValidate || control.disabled || control.type === "file" || control.validity.valid) return;
      const field = control.dataset.field || (control.matches("[data-confirm-accuracy]") ? "confirm_accuracy" : "form");
      let message = control.validationMessage || "Check this value.";
      if (control.validity.valueMissing) message = "Complete this required field.";
      if (control.type === "number" && (control.validity.rangeUnderflow || control.validity.badInput)) {
        message = "Enter a valid number greater than zero.";
      }
      if (control.type === "email" && control.validity.typeMismatch) message = "Enter a valid email address or leave it blank.";
      errors.push(fieldError(field, message, step));
    });
    return errors;
  }

  function parseLocalDate(value) {
    if (!value) return null;
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? null : date;
  }

  function toLocalDateTime(date) {
    const pad = (value) => String(value).padStart(2, "0");
    return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
  }

  function expiryLimitDays(category) {
    return category === "Fruits and Vegetables" ? 7 : 30;
  }

  function syncCategoryRules(card) {
    const category = card.querySelector('[name$="[category]"]')?.value || "";
    const isProduce = category === "Fruits and Vegetables";
    card.querySelectorAll("[data-item-prepared-part]").forEach((input) => {
      input.required = false;
    });
    const optionalLabel = card.querySelector("[data-prepared-optional]");
    const requiredMark = card.querySelector("[data-prepared-required]");
    const preparedHelp = card.querySelector("[data-prepared-help]");
    if (optionalLabel) optionalLabel.hidden = !isProduce;
    if (requiredMark) requiredMark.hidden = isProduce;
    if (preparedHelp) preparedHelp.textContent = isProduce
      ? "Optional for fruits and vegetables when the harvest or packing time is unknown."
      : "Required: when this food item was prepared or packed. Any minute is accepted.";
    const expiryHelp = card.querySelector("[data-expiry-help]");
    if (expiryHelp) expiryHelp.textContent = `Required for each item. ${isProduce ? "Fruits and vegetables" : category || "Other categories"} listings may be up to ${expiryLimitDays(category)} days from preparation (or today if produce preparation is unknown). The earliest expiry sets pickup cutoff one hour earlier. Follow label/storage guidance; this limit does not certify food safety.`;
  }

  function syncItemDeadlineParts(card) {
    const datePart = card.querySelector('[data-item-deadline-part="date"]');
    const timePart = card.querySelector('[data-item-deadline-part="time"]');
    const combined = card.querySelector('[name$="[item_deadline]"]');
    if (!datePart || !timePart || !combined) return;
    combined.value = datePart.value && timePart.value ? `${datePart.value}T${timePart.value}` : "";
  }

  function syncItemPreparedParts(card) {
    const datePart = card.querySelector('[data-item-prepared-part="date"]');
    const timePart = card.querySelector('[data-item-prepared-part="time"]');
    const combined = card.querySelector('[name$="[prepared_at]"]');
    if (!datePart || !timePart || !combined) return;
    combined.value = datePart.value && timePart.value ? `${datePart.value}T${timePart.value}` : "";
  }

  function loadItemDeadlineParts(card) {
    const combined = card.querySelector('[name$="[item_deadline]"]');
    const [dateValue, timeValue] = (combined?.value || "").split("T");
    const datePart = card.querySelector('[data-item-deadline-part="date"]');
    const timePart = card.querySelector('[data-item-deadline-part="time"]');
    if (datePart) datePart.value = dateValue || "";
    if (timePart) timePart.value = timeValue ? timeValue.slice(0, 5) : "";
  }

  function loadItemPreparedParts(card) {
    const combined = card.querySelector('[name$="[prepared_at]"]');
    const [dateValue, timeValue] = (combined?.value || "").split("T");
    const datePart = card.querySelector('[data-item-prepared-part="date"]');
    const timePart = card.querySelector('[data-item-prepared-part="time"]');
    if (datePart) datePart.value = dateValue || "";
    if (timePart) timePart.value = timeValue ? timeValue.slice(0, 5) : "";
  }

  function getItemDeadlines() {
    return Array.from(itemList.querySelectorAll('[name$="[item_deadline]"]'))
      .map((input) => ({ value: input.value, parsed: parseLocalDate(input.value), field: input.dataset.field }))
      .filter((entry) => entry.parsed)
      .sort((left, right) => left.parsed - right.parsed);
  }

  function syncAutomaticWindow() {
    const start = form.querySelector('[name="pickup[pickup_start]"]');
    const end = form.querySelector('[name="pickup[pickup_end]"]');
    const earliestDeadline = getItemDeadlines()[0]?.parsed || null;
    if (start) start.value = toLocalDateTime(new Date());
    if (end) {
      end.value = earliestDeadline ? toLocalDateTime(new Date(earliestDeadline.getTime() - 60 * 60 * 1000)) : "";
      const endLabel = wizard.querySelector("[data-auto-window-end]");
      if (endLabel) endLabel.textContent = end.value ? formatDate(end.value) : "Add an approximate expiry in Food Items";
    }
  }

  function collectFoodErrors() {
    itemList.querySelectorAll("[data-food-item]").forEach(syncCategoryRules);
    const errors = collectNativeErrors("food");
    itemList.querySelectorAll("[data-food-item]").forEach((card, index) => {
      syncItemPreparedParts(card);
      const prepared = card.querySelector('[name$="[prepared_at]"]');
      const preparedAt = parseLocalDate(prepared.value);
      const category = card.querySelector('[name$="[category]"]')?.value || "";
      const isProduce = category === "Fruits and Vegetables";
      const preparedParts = Array.from(card.querySelectorAll("[data-item-prepared-part]"));
      const partialPrepared = preparedParts.some((part) => part.value) && preparedParts.some((part) => !part.value);
      if (partialPrepared) {
        errors.push(fieldError(`items.${index}.prepared_at`, "Choose both the prepared date and time, or leave both blank for fruits and vegetables.", "food"));
      }
      if (!isProduce && !preparedAt && !partialPrepared) {
        errors.push(fieldError(`items.${index}.prepared_at`, "Enter when this food item was prepared or packed.", "food"));
      }
      if (preparedAt && preparedAt > new Date()) {
        errors.push(fieldError(`items.${index}.prepared_at`, "Prepared date and time cannot be in the future.", "food"));
      }
      const datePart = card.querySelector('[data-item-deadline-part="date"]');
      const timePart = card.querySelector('[data-item-deadline-part="time"]');
      const partialExpiry = Boolean(datePart.value) !== Boolean(timePart.value);
      if (partialExpiry) {
        errors.push(fieldError(`items.${index}.item_deadline`, "Choose both the approximate expiry date and time.", "food"));
      }
      syncItemDeadlineParts(card);
      const deadline = card.querySelector('[name$="[item_deadline]"]');
      if (partialExpiry) {
        // The paired-field message above is the single useful error here.
      } else if (!deadline.value) {
        errors.push(fieldError(`items.${index}.item_deadline`, "Enter an approximate expiry for this item.", "food"));
      } else {
        const parsed = parseLocalDate(deadline.value);
        const maxDays = expiryLimitDays(category);
        const expiryBase = preparedAt || new Date();
        const maxExpiry = new Date(expiryBase.getTime() + maxDays * 24 * 60 * 60 * 1000);
        if (!parsed) errors.push(fieldError(`items.${index}.item_deadline`, "Enter a valid approximate expiry.", "food"));
        else if (parsed <= new Date()) errors.push(fieldError(`items.${index}.item_deadline`, "Approximate expiry must be in the future.", "food"));
        else if (parsed > maxExpiry) errors.push(fieldError(`items.${index}.item_deadline`, `Approximate expiry must be within ${maxDays} days of its prepared time (or today if the produce time is unknown). Follow the food label or storage guidance.`, "food"));
        else if (preparedAt && parsed <= preparedAt) errors.push(fieldError(`items.${index}.item_deadline`, "Approximate expiry must be after this item's prepared date and time.", "food"));
      }
    });
    const itemDeadlines = getItemDeadlines();
    if (itemDeadlines.length && itemDeadlines[0].parsed > new Date() && itemDeadlines[0].parsed <= new Date(Date.now() + 60 * 60 * 1000)) {
      errors.push(fieldError(itemDeadlines[0].field, "Approximate expiry must be more than one hour from now so pickup can end one hour earlier.", "food"));
    }
    syncAutomaticWindow();
    return errors;
  }

  function collectPickupErrors() {
    syncAutomaticWindow();
    const errors = collectNativeErrors("pickup");
    const latitude = form.querySelector('[name="pickup[latitude]"]');
    const longitude = form.querySelector('[name="pickup[longitude]"]');
    if (Boolean(latitude.value) !== Boolean(longitude.value)) {
      errors.push(fieldError("pickup.latitude", "Enter both latitude and longitude, or leave both blank.", "pickup"));
    } else if (!latitude.value || !longitude.value) {
      errors.push(fieldError("pickup.latitude", "Use your current location or enter both coordinates so nearby acceptors can find this donation.", "pickup"));
    }

    const start = parseLocalDate(form.querySelector('[name="pickup[pickup_start]"]').value);
    const end = parseLocalDate(form.querySelector('[name="pickup[pickup_end]"]').value);
    const now = new Date();
    const earliestDeadline = getItemDeadlines()[0]?.parsed || null;
    if (!earliestDeadline) errors.push(fieldError("pickup.expiry", "Add an approximate expiry in Food Items to calculate the pickup cutoff.", "pickup"));
    else if (earliestDeadline <= now) errors.push(fieldError("pickup.expiry", "The earliest item expiry has passed. Update it in Food Items.", "pickup"));
    else if (earliestDeadline <= new Date(now.getTime() + 60 * 60 * 1000)) {
      errors.push(fieldError("pickup.expiry", "The earliest approximate expiry must be more than one hour from now.", "pickup"));
    }
    if (start && end && start >= end) errors.push(fieldError("pickup.pickup_end", "Pickup window end must be after its start.", "pickup"));
    if (earliestDeadline && end && end.getTime() !== earliestDeadline.getTime() - 60 * 60 * 1000) {
      errors.push(fieldError("pickup.pickup_end", "Pickup closes automatically one hour before the earliest item expiry.", "pickup"));
    }
    return errors;
  }

  function validPhone(number) {
    const raw = number || "";
    if (!/^\+?[0-9\s().-]+$/.test(raw)) return false;
    const digits = raw.replace(/[^0-9]/g, "");
    return digits.length >= 7 && digits.length <= 15;
  }

  function collectContactErrors() {
    const errors = collectNativeErrors("contact");
    const phone = form.querySelector('[name="contact[phone]"]').value;
    if (!validPhone(phone)) errors.push(fieldError("contact.phone", "Enter a valid phone number using 7 to 15 digits.", "contact"));
    return errors;
  }

  function collectReviewErrors() {
    return collectNativeErrors("review");
  }

  function collectErrorsForStep(step) {
    if (step === "food") return collectFoodErrors();
    if (step === "pickup") return collectPickupErrors();
    if (step === "contact") return collectContactErrors();
    return collectReviewErrors();
  }

  function collectAllErrors() {
    return steps.flatMap(collectErrorsForStep);
  }

  function validateStep(step) {
    const errors = collectErrorsForStep(step);
    showErrors(errors);
    if (errors.length) {
      focusFirstError(errors);
      return false;
    }
    return true;
  }

  function setFieldText(selector, value, fallback) {
    const element = form.querySelector(`[data-review="${selector}"]`);
    if (element) element.textContent = value || fallback || "Not entered";
  }

  function selectedDiet(card) {
    return card.querySelector('[name$="[dietary_type]"]:checked')?.value || "";
  }

  function renderReview() {
    const target = form.querySelector("[data-review-items]");
    target.replaceChildren();
    const cards = Array.from(itemList.querySelectorAll("[data-food-item]"));
    if (!cards.length) {
      const empty = document.createElement("p");
      empty.className = "review-empty";
      empty.textContent = "No food items entered.";
      target.append(empty);
    }

    cards.forEach((card, index) => {
      const read = (suffix) => card.querySelector(`[name$="[${suffix}]"]`)?.value.trim() || "";
      const image = card.querySelector("[data-food-preview]");
      const row = document.createElement("article");
      row.className = "review-food-item";
      const photo = document.createElement("img");
      photo.src = image?.src || image?.dataset.defaultSrc || "";
      photo.alt = read("food_name") ? `Photo preview for ${read("food_name")}` : `Food item ${index + 1} photo preview`;
      const copy = document.createElement("div");
      copy.className = "review-food-copy";
      const title = document.createElement("h4");
      title.textContent = read("food_name") || `Food Item ${index + 1}`;
      const tags = document.createElement("p");
      tags.className = "review-tags";
      tags.textContent = [read("category"), selectedDiet(card)].filter(Boolean).join(" · ") || "Category not selected";
      const description = document.createElement("p");
      description.textContent = read("description") || "No description entered.";
      const quantity = document.createElement("p");
      quantity.className = "review-quantity";
      quantity.textContent = `${read("quantity") || "—"} ${read("unit")}`.trim();
      const servingSize = read("serving_size");
      if (servingSize) quantity.textContent += ` · Serving size ${servingSize} ${read("serving_unit")}`;
      const preparedAt = read("prepared_at");
      if (preparedAt) quantity.textContent += ` · Prepared ${formatDate(preparedAt)}`;
      const itemDeadline = read("item_deadline");
      if (itemDeadline) quantity.textContent += ` · Approximate expiry ${formatDate(itemDeadline)}`;
      copy.append(title, tags, description, quantity);
      row.append(photo, copy);
      target.append(row);
    });

    const pickupRead = (key) => form.querySelector(`[name="pickup[${key}]"]`)?.value.trim() || "";
    const address = [pickupRead("pickup_address"), pickupRead("landmark"), pickupRead("area"), pickupRead("city")]
      .filter(Boolean).join(", ");
    setFieldText("pickup.location", address, "Not entered");
    const latitude = pickupRead("latitude");
    const longitude = pickupRead("longitude");
    setFieldText("pickup.coordinates", latitude && longitude ? `Coordinates: ${latitude}, ${longitude}` : "");
    const earliestDeadline = getItemDeadlines()[0]?.value || "";
    setFieldText("pickup.expiry", formatDate(earliestDeadline), "Not entered");
    const start = pickupRead("pickup_start");
    const end = pickupRead("pickup_end");
    setFieldText("pickup.window", start && end ? `${formatDate(start)} to ${formatDate(end)}` : "Not entered");

    const contactRead = (key) => form.querySelector(`[name="contact[${key}]"]`)?.value.trim() || "";
    setFieldText("contact.contact_name", contactRead("contact_name"), "Not entered");
    const phone = contactRead("phone");
    setFieldText("contact.phone", phone || "Not entered");
    setFieldText("contact.email", contactRead("email"), "Not provided");
  }

  function updateEarliestDeadline() {
    const deadlines = getItemDeadlines();
    const note = wizard.querySelector("[data-earliest-deadline]");
    if (!deadlines.length) {
      if (note) note.textContent = "Enter an approximate expiry for at least one item. Pickup closes automatically one hour before the earliest entered expiry.";
      syncAutomaticWindow();
      return;
    }
    if (note) note.textContent = `Earliest item approximate expiry: ${formatDate(deadlines[0].value)}. Pickup closes one hour before this time.`;
    syncAutomaticWindow();
  }

  function updateSubmitState() {
    const submit = form.querySelector("[data-submit-donation]");
    const confirmation = form.querySelector("[data-confirm-accuracy]");
    if (!submit || submissionInProgress) return;
    submit.disabled = !confirmation.checked || collectAllErrors().length > 0;
  }

  function valueFor(control) {
    if (!control) return "";
    if (control.type === "checkbox") return control.checked ? "1" : "";
    return control.value.trim();
  }

  function buildPayload() {
    const items = Array.from(itemList.querySelectorAll("[data-food-item]")).map((card) => {
      const read = (field) => valueFor(card.querySelector(`[name$="[${field}]"]`));
      return {
        food_name: read("food_name"),
        category: read("category"),
        description: read("description"),
        dietary_type: card.querySelector('[name$="[dietary_type]"]:checked')?.value || "",
        quantity: read("quantity"),
        unit: read("unit"),
        serving_size: read("serving_size"),
        serving_unit: read("serving_unit"),
        prepared_at: read("prepared_at"),
        item_deadline: read("item_deadline"),
      };
    });
    const grouped = (prefix, names) => Object.fromEntries(names.map((name) => [
      name,
      valueFor(form.querySelector(`[name="${prefix}[${name}]"]`)),
    ]));
    return {
      items,
      pickup: grouped("pickup", ["city", "area", "landmark", "pickup_address", "latitude", "longitude", "pickup_start", "pickup_end"]),
      contact: grouped("contact", ["contact_name", "phone", "email", "use_profile", "save_to_profile"]),
      confirm_accuracy: form.querySelector("[data-confirm-accuracy]").checked,
    };
  }

  function reindexItems() {
    itemList.querySelectorAll("[data-food-item]").forEach((card, index) => {
      syncCategoryRules(card);
      const oldIndex = card.dataset.index;
      card.dataset.index = String(index);
      const title = card.querySelector(".item-collapse > span:last-child");
      const number = card.querySelector(".item-index");
      const body = card.querySelector("[data-item-body]");
      const toggle = card.querySelector("[data-toggle-item]");
      if (title) title.textContent = `Food Item ${index + 1}`;
      if (number) number.textContent = String(index + 1);
      if (body) body.id = `food-item-body-${index}`;
      if (toggle) toggle.setAttribute("aria-controls", `food-item-body-${index}`);
      card.querySelectorAll("[id]").forEach((element) => {
        element.id = element.id.replace(/-\d+$/, `-${index}`);
      });
      card.querySelectorAll("label[for]").forEach((label) => {
        label.htmlFor = label.htmlFor.replace(/-\d+$/, `-${index}`);
      });
      card.querySelectorAll("[name]").forEach((control) => {
        control.name = control.name.replace(/items\[\d+\]/, `items[${index}]`);
      });
      card.querySelectorAll("[data-field]").forEach((control) => {
        control.dataset.field = control.dataset.field.replace(/^items\.\d+\./, `items.${index}.`);
      });
      card.querySelectorAll("[data-error-for]").forEach((element) => {
        element.dataset.errorFor = element.dataset.errorFor.replace(/^items\.\d+\./, `items.${index}.`);
      });
      const image = card.querySelector("[data-food-preview]");
      if (image) image.alt = `Food item ${index + 1} photo preview`;
      const remove = card.querySelector("[data-remove-item]");
      if (remove) remove.disabled = itemList.querySelectorAll("[data-food-item]").length === 1;
      if (oldIndex !== String(index)) card.dataset.index = String(index);
    });
    updateEarliestDeadline();
  }

  function clearClonedItem(card) {
    card.querySelectorAll("input, textarea").forEach((control) => {
      if (control.type === "radio" || control.type === "checkbox") control.checked = false;
      else control.value = "";
      control.setCustomValidity("");
      control.removeAttribute("aria-invalid");
    });
    card.querySelectorAll("select").forEach((control) => {
      control.selectedIndex = 0;
      control.removeAttribute("aria-invalid");
    });
    card.querySelectorAll(".field-error").forEach((element) => { element.textContent = ""; });
    card.querySelectorAll("[data-food-preview]").forEach((image) => {
      image.src = image.dataset.defaultSrc;
      image.dataset.objectUrl = "";
    });
    card.querySelector("[data-item-body]").hidden = false;
    card.querySelector("[data-toggle-item]").setAttribute("aria-expanded", "true");
  }

  addItemButton.addEventListener("click", () => {
    const templateCard = itemList.querySelector("[data-food-item]");
    if (!templateCard) return;
    const clone = templateCard.cloneNode(true);
    clearClonedItem(clone);
    syncCategoryRules(clone);
    itemList.append(clone);
    reindexItems();
    clone.querySelector('[name$="[food_name]"]').focus();
    clearErrors();
    updateReview();
    updateSubmitState();
  });

  itemList.addEventListener("click", (event) => {
    const toggle = event.target.closest("[data-toggle-item]");
    if (toggle) {
      const body = toggle.closest("[data-food-item]").querySelector("[data-item-body]");
      body.hidden = !body.hidden;
      toggle.setAttribute("aria-expanded", String(!body.hidden));
      return;
    }
    const remove = event.target.closest("[data-remove-item]");
    if (!remove || itemList.querySelectorAll("[data-food-item]").length <= 1) return;
    const card = remove.closest("[data-food-item]");
    const image = card.querySelector("[data-food-preview]");
    if (image?.dataset.objectUrl) URL.revokeObjectURL(image.dataset.objectUrl);
    card.remove();
    reindexItems();
    clearErrors();
    updateReview();
    updateSubmitState();
  });

  itemList.addEventListener("change", (event) => {
    const input = event.target;
    if (input.matches('[name$="[category]"]')) syncCategoryRules(input.closest("[data-food-item]"));
    if (!input.matches("[data-image-input]")) {
      updateEarliestDeadline();
      if (currentStep === "review") renderReview();
      updateSubmitState();
      return;
    }
    const card = input.closest("[data-food-item]");
    const image = card.querySelector("[data-food-preview]");
    if (image.dataset.objectUrl) URL.revokeObjectURL(image.dataset.objectUrl);
    const file = input.files?.[0];
    if (!file) {
      input.setCustomValidity("");
      image.src = image.dataset.defaultSrc;
      image.dataset.objectUrl = "";
      return;
    }
    if (!["image/jpeg", "image/png", "image/webp"].includes(file.type)) {
      input.setCustomValidity("Choose an image file.");
      input.reportValidity();
      input.value = "";
      input.setCustomValidity("");
      image.src = image.dataset.defaultSrc;
      return;
    }
    if (file.size > 5 * 1024 * 1024) {
      input.setCustomValidity("Choose an image smaller than 5 MB for this preview.");
      input.reportValidity();
      input.value = "";
      input.setCustomValidity("");
      image.src = image.dataset.defaultSrc;
      return;
    }
    input.setCustomValidity("");
    image.dataset.objectUrl = URL.createObjectURL(file);
    image.src = image.dataset.objectUrl;
    image.alt = `Photo preview for ${card.querySelector('[name$="[food_name]"]').value || "food item"}`;
    updateReview();
  });

  wizard.querySelectorAll("[data-next-step]").forEach((button) => {
    button.addEventListener("click", () => {
      if (!validateStep(currentStep)) return;
      setStep(button.dataset.nextStep);
      clearErrors();
    });
  });
  wizard.querySelectorAll("[data-prev-step]").forEach((button) => {
    button.addEventListener("click", () => {
      clearErrors();
      setStep(button.dataset.prevStep);
    });
  });
  wizard.querySelectorAll("[data-go-step]").forEach((button) => {
    button.addEventListener("click", () => {
      const target = button.dataset.goStep;
      if (steps.indexOf(target) > steps.indexOf(currentStep)) return;
      clearErrors();
      setStep(target);
    });
  });

  const profileToggle = form.querySelector("[data-use-profile]");
  const profileUnavailable = form.querySelector("[data-profile-unavailable]");
  profileToggle.addEventListener("change", () => {
    profileUnavailable.hidden = !profileToggle.checked;
    if (profileToggle.checked) {
      const profileValues = {
        "contact[contact_name]": form.dataset.profileName || "",
        "contact[email]": form.dataset.profileEmail || "",
        "contact[phone]": form.dataset.profilePhone || "",
      };
      Object.entries(profileValues).forEach(([name, value]) => {
        const control = form.querySelector(`[name="${name}"]`);
        if (control && value) control.value = value;
      });
      updateReview();
      updateSubmitState();
    }
  });

  form.addEventListener("input", (event) => {
    if (event.target.matches("[data-field]")) {
      form.querySelectorAll(`[data-field="${CSS.escape(event.target.dataset.field)}"]`).forEach((control) => control.setAttribute("aria-invalid", "false"));
      const message = form.querySelector(`[data-error-for="${CSS.escape(event.target.dataset.field)}"]`);
      if (message) message.textContent = "";
      feedback.querySelector("[data-server-errors]")?.remove();
      feedback.querySelector("[data-client-errors]")?.remove();
    }
    if (event.target.dataset.itemDeadlinePart) syncItemDeadlineParts(event.target.closest("[data-food-item]"));
    if (event.target.dataset.itemPreparedPart) syncItemPreparedParts(event.target.closest("[data-food-item]"));
    updateEarliestDeadline();
    if (currentStep === "review") renderReview();
    updateSubmitState();
  });
  form.addEventListener("change", (event) => {
    if (event.target.dataset.itemDeadlinePart) syncItemDeadlineParts(event.target.closest("[data-food-item]"));
    if (event.target.dataset.itemPreparedPart) syncItemPreparedParts(event.target.closest("[data-food-item]"));
    updateEarliestDeadline();
    if (currentStep === "review") renderReview();
    updateSubmitState();
  });

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (submissionInProgress) return;
    itemList.querySelectorAll("[data-food-item]").forEach((card) => {
      syncItemPreparedParts(card);
      syncItemDeadlineParts(card);
    });
    syncAutomaticWindow();
    const errors = collectAllErrors();
    if (errors.length) {
      showErrors(errors);
      const firstStep = errors[0].step;
      setStep(firstStep);
      focusFirstError(errors);
      return;
    }

    submissionInProgress = true;
    const submit = form.querySelector("[data-submit-donation]");
    submit.textContent = "Saving donation...";
    submit.disabled = true;
    try {
      const formData = new FormData();
      formData.append("payload", JSON.stringify(buildPayload()));
      itemList.querySelectorAll("[data-food-item]").forEach((card, index) => {
        const photo = card.querySelector("[data-image-input]")?.files?.[0];
        if (photo) formData.append(`items[${index}][food_photo]`, photo);
      });
      const response = await fetch(form.action, {
        method: "POST",
        headers: { "Accept": "application/json" },
        body: formData,
      });
      const result = await response.json();
      if (!response.ok || !result.valid) {
        showErrors(result.errors || [{ field: "form", message: "The server could not validate this form. Your entries remain on this page.", step: "review" }]);
        setStep(result.first_step || "review");
        focusFirstError(result.errors || []);
      } else {
        clearErrors();
        if (result.redirect) {
          window.location.assign(result.redirect);
          return;
        }
        const notice = document.createElement("p");
        notice.className = "form-alert";
        notice.setAttribute("role", "status");
        notice.textContent = result.notice || "Your donation was saved.";
        feedback.prepend(notice);
        setStep("review");
      }
    } catch (error) {
      showErrors([{ field: "form", message: "The form could not reach Plentiq. Your entries remain on this page; try submitting again.", step: "review" }]);
      setStep("review");
    } finally {
      submissionInProgress = false;
      submit.textContent = "Submit Donation";
      updateSubmitState();
    }
  });

  itemList.querySelectorAll("[data-food-item]").forEach((card) => {
    syncCategoryRules(card);
    loadItemPreparedParts(card);
    loadItemDeadlineParts(card);
  });
  syncAutomaticWindow();
  reindexItems();
  updateEarliestDeadline();
  setStep(currentStep);
  renderReview();
  updateSubmitState();
})();

document.addEventListener("DOMContentLoaded", () => {
  const categoryPicker = document.querySelector(".acceptor-category-picker");
  if (categoryPicker) {
    const categoryInputs = [...categoryPicker.querySelectorAll('input[name="categories"]')];
    const selectedCount = categoryPicker.querySelector("#category-count");
    const updateCategorySelection = () => {
      const selected = categoryInputs.filter((input) => input.checked).length;
      categoryInputs.forEach((input) => input.closest(".category-choice")?.classList.toggle("is-selected", input.checked));
      if (selectedCount) selectedCount.textContent = `${selected} selected`;
    };
    categoryInputs.forEach((input) => input.addEventListener("change", updateCategorySelection));
    updateCategorySelection();
  }

  const dietaryPicker = document.querySelector(".dietary-picker");
  dietaryPicker?.querySelectorAll('input[name="dietary_code"]').forEach((input) => {
    input.addEventListener("change", () => {
      dietaryPicker.querySelectorAll(".dietary-choice").forEach((choice) => {
        choice.classList.toggle("is-selected", choice.querySelector('input[name="dietary_code"]')?.checked === true);
      });
    });
  });

  document.querySelectorAll('input[type="date"][name="needed_for"]').forEach((input) => {
    const today = new Date();
    const localToday = new Date(today.getTime() - today.getTimezoneOffset() * 60000)
      .toISOString().slice(0, 10);
    input.min = localToday;
  });
});

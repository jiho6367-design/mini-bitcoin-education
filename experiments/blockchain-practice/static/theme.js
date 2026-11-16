(() => {
  const key = "team-blockchain-theme";
  const legacyKey = "blockchain-lab-theme";
  const parse = (value) => value === "light" || value === "dark" ? value : "system";
  let preference = "system";
  try {
    const stored = localStorage.getItem(key);
    if (stored !== null) preference = parse(stored);
    else {
      const legacy = localStorage.getItem(legacyKey);
      if (["system", "light", "dark"].includes(legacy)) {
        preference = legacy;
        try { localStorage.setItem(key, preference); } catch { /* Keep this page's preference. */ }
      }
    }
  } catch { /* System defaults also work when browser storage is unavailable. */ }

  const media = matchMedia("(prefers-color-scheme: dark)");
  function apply() {
    document.documentElement.dataset.theme = preference === "system"
      ? media.matches ? "dark" : "light"
      : preference;
    document.documentElement.dataset.themePreference = preference;
    window.dispatchEvent(new CustomEvent("team-theme-change", { detail: preference }));
  }
  window.teamLabTheme = {
    getPreference: () => preference,
    select(value) {
      preference = parse(value);
      try { localStorage.setItem(key, preference); } catch { /* Keep the current choice. */ }
      apply();
    },
  };
  media.addEventListener("change", apply);
  window.addEventListener("storage", (event) => {
    if (event.key === key || event.key === null) {
      preference = parse(event.key === null ? null : event.newValue);
      apply();
    }
  });
  apply();
})();

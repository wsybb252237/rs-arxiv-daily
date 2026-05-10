(function () {
  const storageKey = "arxivDailyTheme";
  const root = document.documentElement;
  const systemDark = () => window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches;
  const savedTheme = localStorage.getItem(storageKey);

  function currentTheme() {
    return savedTheme || (systemDark() ? "dark" : "light");
  }

  function applyTheme(theme) {
    root.dataset.theme = theme;
    root.style.colorScheme = theme;
    document.querySelectorAll("[data-theme-toggle]").forEach((button) => {
      const icon = button.querySelector("i");
      const label = button.querySelector("[data-theme-label]");
      if (icon) icon.setAttribute("data-lucide", theme === "dark" ? "sun" : "moon");
      if (label) label.textContent = theme === "dark" ? "亮色" : "暗色";
      button.setAttribute("aria-label", theme === "dark" ? "切换到亮色主题" : "切换到暗色主题");
      button.setAttribute("title", theme === "dark" ? "切换到亮色主题" : "切换到暗色主题");
    });
    if (window.lucide) window.lucide.createIcons();
  }

  applyTheme(currentTheme());

  document.addEventListener("DOMContentLoaded", () => {
    applyTheme(root.dataset.theme || currentTheme());
    document.querySelectorAll("[data-theme-toggle]").forEach((button) => {
      button.addEventListener("click", () => {
        const nextTheme = root.dataset.theme === "dark" ? "light" : "dark";
        localStorage.setItem(storageKey, nextTheme);
        applyTheme(nextTheme);
      });
    });
  });
})();

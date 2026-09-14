import { useCallback, useEffect, useState } from "react";

const KEY = "safeherway.theme";

function current() {
  return document.documentElement.getAttribute("data-theme") === "dark" ? "dark" : "light";
}

/** Light / dark, persisted; the inline script in index.html applies it before paint. */
export function useTheme() {
  const [theme, setTheme] = useState(current);

  useEffect(() => {
    const media = window.matchMedia("(prefers-color-scheme: dark)");
    const onChange = () => {
      let saved = null;
      try { saved = localStorage.getItem(KEY); } catch {}
      if (!saved) {
        document.documentElement.toggleAttribute("data-theme", media.matches);
        if (media.matches) document.documentElement.setAttribute("data-theme", "dark");
        setTheme(current());
      }
    };
    media.addEventListener("change", onChange);
    return () => media.removeEventListener("change", onChange);
  }, []);

  const toggle = useCallback(() => {
    const next = current() === "dark" ? "light" : "dark";
    if (next === "dark") document.documentElement.setAttribute("data-theme", "dark");
    else document.documentElement.removeAttribute("data-theme");
    try { localStorage.setItem(KEY, next); } catch {}
    setTheme(next);
  }, []);

  return { theme, toggle, isDark: theme === "dark" };
}

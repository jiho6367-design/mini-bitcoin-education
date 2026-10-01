import { useLayoutEffect, useState } from "react";
import {
  applyTheme,
  loadBrowserTheme,
  saveBrowserTheme,
  type ThemePreference,
} from "../theme";

export function useTheme() {
  const [preference, setPreference] = useState(loadBrowserTheme);

  useLayoutEffect(() => {
    const media = window.matchMedia("(prefers-color-scheme: dark)");
    const update = () => applyTheme(preference, media.matches);
    update();
    media.addEventListener("change", update);
    return () => media.removeEventListener("change", update);
  }, [preference]);

  function selectTheme(next: ThemePreference) {
    saveBrowserTheme(next);
    setPreference(next);
  }

  return { preference, selectTheme };
}

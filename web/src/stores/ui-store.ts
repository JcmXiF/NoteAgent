import { create } from "zustand";
import {
  applyTheme,
  loadAndApplyFontSize,
  loadAndApplyTheme,
  saveFontSize,
  saveTheme,
  type AppTheme,
} from "@/lib/theme";

/**
 * Interface preferences, shared across projects.
 *
 * Importing this module applies the saved theme and font size immediately, so
 * it must be imported before the first render (see main.tsx) to avoid a flash
 * of the default theme.
 */
const initialTheme = loadAndApplyTheme();
const initialFontSize = loadAndApplyFontSize();

type UiState = {
  theme: AppTheme;
  fontSize: number;
  setTheme: (theme: AppTheme) => void;
  setFontSize: (px: number) => void;
};

export const useUiStore = create<UiState>((set) => ({
  theme: initialTheme,
  fontSize: initialFontSize,

  setTheme: (theme) => {
    applyTheme(theme);
    saveTheme(theme);
    set({ theme });
  },

  setFontSize: (px) => {
    const clamped = Math.min(20, Math.max(12, px));
    document.documentElement.style.fontSize = `${clamped}px`;
    saveFontSize(clamped);
    set({ fontSize: clamped });
  },
}));

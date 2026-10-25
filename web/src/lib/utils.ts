import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

/** The one place class names are merged, so conflicting utilities resolve. */
export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

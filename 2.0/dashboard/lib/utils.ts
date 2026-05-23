// shadcn/ui's standard `cn` helper. Merges class strings and resolves
// Tailwind class conflicts (later classes win, but only for clashing groups).
import { type ClassValue, clsx } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}

import type { TranscriptionLanguage } from "@/lib/api/types";

export const TRANSCRIPTION_LANGUAGE_OPTIONS: Array<{
  value: TranscriptionLanguage;
  label: string;
}> = [
  { value: "auto", label: "Auto: English/French" },
  { value: "en", label: "English" },
  { value: "fr", label: "Français" },
];

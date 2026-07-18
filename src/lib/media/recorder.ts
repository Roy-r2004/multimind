export const PREFERRED_AUDIO_MIME_TYPES = [
  "audio/webm;codecs=opus",
  "audio/webm",
  "audio/ogg;codecs=opus",
  "audio/ogg",
  "audio/mp4",
  "audio/wav",
] as const;

export type RecordingSupport =
  | { supported: true; mimeType: string }
  | {
      supported: false;
      reason: "unsupported_browser" | "insecure_context" | "media_devices_unavailable";
    };

export function selectSupportedRecordingMimeType(): string | null {
  if (typeof MediaRecorder === "undefined") {
    return null;
  }

  return PREFERRED_AUDIO_MIME_TYPES.find((type) => MediaRecorder.isTypeSupported(type)) ?? null;
}

export function getRecordingSupport(): RecordingSupport {
  if (typeof window !== "undefined" && !window.isSecureContext) {
    return { supported: false, reason: "insecure_context" };
  }

  if (
    typeof navigator === "undefined" ||
    !navigator.mediaDevices ||
    typeof navigator.mediaDevices.getUserMedia !== "function"
  ) {
    return { supported: false, reason: "media_devices_unavailable" };
  }

  const mimeType = selectSupportedRecordingMimeType();
  if (!mimeType) {
    return { supported: false, reason: "unsupported_browser" };
  }

  return { supported: true, mimeType };
}

export function microphoneAudioConstraints(): MediaStreamConstraints {
  return {
    audio: {
      channelCount: 1,
      echoCancellation: true,
      noiseSuppression: true,
      autoGainControl: true,
    },
  };
}

export async function requestMicrophoneStream(): Promise<MediaStream> {
  if (
    typeof navigator === "undefined" ||
    !navigator.mediaDevices ||
    typeof navigator.mediaDevices.getUserMedia !== "function"
  ) {
    const error = new Error("Media devices are unavailable.");
    error.name = "NotSupportedError";
    throw error;
  }

  return navigator.mediaDevices.getUserMedia(microphoneAudioConstraints());
}

export function recordingExtensionForMimeType(mimeType: string | null | undefined): string {
  const normalized = (mimeType ?? "").split(";")[0]?.trim().toLowerCase();

  switch (normalized) {
    case "audio/webm":
      return "webm";
    case "audio/ogg":
      return "ogg";
    case "audio/mp4":
      return "mp4";
    case "audio/mpeg":
      return "mp3";
    case "audio/wav":
    case "audio/x-wav":
      return "wav";
    default:
      return "audio";
  }
}

export function formatElapsedSeconds(totalSeconds: number): string {
  const seconds = Math.max(0, Math.floor(totalSeconds));
  const minutes = Math.floor(seconds / 60);
  const remainingSeconds = seconds % 60;
  return `${minutes}:${String(remainingSeconds).padStart(2, "0")}`;
}

export function stopMediaStreamTracks(stream: MediaStream | null | undefined): void {
  stream?.getTracks().forEach((track) => {
    track.stop();
  });
}

export function safelyStopMediaRecorder(recorder: MediaRecorder | null | undefined): boolean {
  if (!recorder || recorder.state === "inactive") {
    return false;
  }

  try {
    recorder.stop();
    return true;
  } catch {
    return false;
  }
}

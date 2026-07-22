import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { ButtonHTMLAttributes } from "react";
import { Loader2, Mic, Pause, Play, RotateCcw, Square, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Progress } from "@/components/ui/progress";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import { api } from "@/lib/api";
import { ApiClientError } from "@/lib/api/types";
import type { ApiTranscriptionResponse, TranscriptionLanguage } from "@/lib/api/types";
import { TRANSCRIPTION_LANGUAGE_OPTIONS } from "@/lib/transcriptionLanguages";
import { cn } from "@/lib/utils";
import {
  formatElapsedSeconds,
  getRecordingSupport,
  recordingExtensionForMimeType,
  requestMicrophoneStream,
  safelyStopMediaRecorder,
  stopMediaStreamTracks,
} from "@/lib/media/recorder";

type VoiceRecorderAuth = {
  token: string;
  orgId: string;
};

type RecorderState =
  | { status: "idle"; elapsedSeconds: number }
  | { status: "requesting_permission"; elapsedSeconds: number }
  | { status: "recording"; elapsedSeconds: number }
  | { status: "paused"; elapsedSeconds: number }
  | { status: "ready_to_transcribe"; elapsedSeconds: number }
  | { status: "uploading"; elapsedSeconds: number }
  | { status: "transcribing"; elapsedSeconds: number }
  | { status: "success"; elapsedSeconds: number }
  | { status: "unsupported"; elapsedSeconds: number; message: string }
  | { status: "error"; elapsedSeconds: number; message: string; canRetry: boolean };

export interface VoiceRecorderButtonProps {
  auth: VoiceRecorderAuth | null;
  disabled?: boolean;
  maxDurationSeconds?: number;
  onTranscript: (result: ApiTranscriptionResponse) => void;
  onRecordingStateChange?: (active: boolean) => void;
}

function supportMessage(
  reason: "unsupported_browser" | "insecure_context" | "media_devices_unavailable",
) {
  switch (reason) {
    case "insecure_context":
      return "Microphone access requires a secure browser connection.";
    case "media_devices_unavailable":
      return "Microphone access requires a secure browser connection.";
    case "unsupported_browser":
      return "Voice recording is not supported in this browser.";
  }
}

function microphoneErrorMessage(error: unknown): string {
  let name = "";
  if (typeof DOMException !== "undefined" && error instanceof DOMException) {
    name = error.name;
  } else if (error instanceof Error) {
    name = error.name;
  }

  switch (name) {
    case "NotAllowedError":
    case "PermissionDeniedError":
      return "Microphone permission was denied. Enable it in your browser settings and try again.";
    case "NotFoundError":
    case "DevicesNotFoundError":
      return "No microphone was found.";
    case "NotSupportedError":
    case "SecurityError":
      return "Microphone access requires a secure browser connection.";
    default:
      return "The microphone could not be started.";
  }
}

function normalizedErrorCode(error: ApiClientError): string {
  return (error.body?.error ?? "").toLowerCase();
}

function transcriptionErrorMessage(error: unknown): string | null {
  if (error instanceof ApiClientError) {
    const code = normalizedErrorCode(error);

    if (code === "request_cancelled") {
      return null;
    }
    if (code === "transcription_busy" || error.status === 429) {
      return "The transcription service is busy. Try again shortly.";
    }
    if (code === "silent_audio") {
      return "No clear speech was detected.";
    }
    if (code === "audio_too_long") {
      return "The recording is longer than the allowed limit.";
    }
    if (code === "transcription_timeout" || code === "request_timeout" || error.status === 504) {
      return "Transcription took too long. You can retry.";
    }
    if (
      code === "transcription_disabled" ||
      code === "transcription_model_unavailable" ||
      error.status === 503
    ) {
      return "Voice transcription is temporarily unavailable.";
    }
    if (code === "audio_too_large" || error.status === 413) {
      return "The recording is too large to transcribe.";
    }
    if (code === "unsupported_audio_type" || error.status === 415) {
      return "This audio format is not supported.";
    }
    if (code === "invalid_audio" || error.status === 422) {
      return "The recording could not be transcribed. Please try again.";
    }
    if (error.status === 408) {
      return "Transcription took too long. You can retry.";
    }
  }

  return "Voice transcription failed. You can retry.";
}

function IconButton({
  label,
  children,
  className,
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { label: string }) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Button
          type="button"
          size="icon"
          variant="ghost"
          aria-label={label}
          className={className}
          {...props}
        >
          {children}
        </Button>
      </TooltipTrigger>
      <TooltipContent>{label}</TooltipContent>
    </Tooltip>
  );
}

export function VoiceRecorderButton({
  auth,
  disabled = false,
  maxDurationSeconds = 600,
  onTranscript,
  onRecordingStateChange,
}: VoiceRecorderButtonProps) {
  const [state, setState] = useState<RecorderState>({ status: "idle", elapsedSeconds: 0 });
  const [language, setLanguage] = useState<TranscriptionLanguage>("auto");

  const recorderRef = useRef<MediaRecorder | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const retainedBlobRef = useRef<Blob | null>(null);
  const selectedMimeTypeRef = useRef<string | null>(null);
  const timerRef = useRef<ReturnType<typeof window.setInterval> | null>(null);
  const abortControllerRef = useRef<AbortController | null>(null);
  const attemptIdRef = useRef(0);
  const mountedRef = useRef(false);
  const stopInProgressRef = useRef(false);
  const cancelledRef = useRef(false);
  const activeStartedAtRef = useRef<number | null>(null);
  const elapsedBeforePauseMsRef = useRef(0);
  const maxDurationRef = useRef(maxDurationSeconds);

  const maxDuration = Math.max(1, maxDurationSeconds);
  const hasAuth = Boolean(auth?.token && auth.orgId);
  const isBusy = !["idle", "error", "unsupported", "success"].includes(state.status);
  const elapsedSeconds = state.elapsedSeconds;
  const remainingSeconds = Math.max(0, maxDuration - elapsedSeconds);
  const progress = Math.min(100, (elapsedSeconds / maxDuration) * 100);
  const showFinalMinuteWarning =
    (state.status === "recording" || state.status === "paused") && remainingSeconds <= 60;

  useEffect(() => {
    maxDurationRef.current = maxDuration;
  }, [maxDuration]);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      attemptIdRef.current += 1;
      abortControllerRef.current?.abort();
      abortControllerRef.current = null;
      if (timerRef.current) {
        window.clearInterval(timerRef.current);
        timerRef.current = null;
      }
      safelyStopMediaRecorder(recorderRef.current);
      recorderRef.current = null;
      stopMediaStreamTracks(streamRef.current);
      streamRef.current = null;
      chunksRef.current = [];
      retainedBlobRef.current = null;
    };
  }, []);

  useEffect(() => {
    const active = [
      "requesting_permission",
      "recording",
      "paused",
      "ready_to_transcribe",
      "uploading",
      "transcribing",
    ].includes(state.status);
    onRecordingStateChange?.(active);
  }, [onRecordingStateChange, state.status]);

  const clearTimer = useCallback(() => {
    if (timerRef.current) {
      window.clearInterval(timerRef.current);
      timerRef.current = null;
    }
  }, []);

  const elapsedMs = useCallback(() => {
    const activeStartedAt = activeStartedAtRef.current;
    if (activeStartedAt === null) {
      return elapsedBeforePauseMsRef.current;
    }
    return elapsedBeforePauseMsRef.current + (performance.now() - activeStartedAt);
  }, []);

  const elapsedSecondsNow = useCallback(() => Math.floor(elapsedMs() / 1000), [elapsedMs]);

  const cleanupRecorderAndStream = useCallback(() => {
    const recorder = recorderRef.current;
    if (recorder) {
      safelyStopMediaRecorder(recorder);
      recorder.ondataavailable = null;
      recorder.onerror = null;
      recorder.onstop = null;
    }
    recorderRef.current = null;
    stopMediaStreamTracks(streamRef.current);
    streamRef.current = null;
  }, []);

  const resetRecordingRefs = useCallback(
    (clearRetainedBlob: boolean) => {
      clearTimer();
      cleanupRecorderAndStream();
      chunksRef.current = [];
      selectedMimeTypeRef.current = null;
      elapsedBeforePauseMsRef.current = 0;
      activeStartedAtRef.current = null;
      stopInProgressRef.current = false;
      cancelledRef.current = false;
      if (clearRetainedBlob) {
        retainedBlobRef.current = null;
      }
    },
    [cleanupRecorderAndStream, clearTimer],
  );

  const stopRecorderAndCreateBlob = useCallback(() => {
    const recorder = recorderRef.current;
    const mimeType = recorder?.mimeType || selectedMimeTypeRef.current || "audio/webm";

    return new Promise<Blob>((resolve, reject) => {
      if (!recorder || recorder.state === "inactive") {
        resolve(new Blob(chunksRef.current, { type: mimeType }));
        return;
      }

      let settled = false;
      const finish = () => {
        if (settled) return;
        settled = true;
        recorder.removeEventListener("stop", finish);
        recorder.removeEventListener("error", fail);
        resolve(new Blob(chunksRef.current, { type: mimeType }));
      };
      const fail = () => {
        if (settled) return;
        settled = true;
        recorder.removeEventListener("stop", finish);
        recorder.removeEventListener("error", fail);
        reject(new Error("MediaRecorder failed."));
      };

      recorder.addEventListener("stop", finish);
      recorder.addEventListener("error", fail);

      try {
        recorder.requestData();
      } catch {
        /* Some browsers throw when no data is ready yet. */
      }

      if (!safelyStopMediaRecorder(recorder)) {
        finish();
      }
    });
  }, []);

  const resetToIdle = useCallback(() => {
    resetRecordingRefs(true);
    if (mountedRef.current) {
      setState({ status: "idle", elapsedSeconds: 0 });
    }
  }, [resetRecordingRefs]);

  const startTimer = useCallback(
    (autoStop: () => void) => {
      clearTimer();
      timerRef.current = window.setInterval(() => {
        const seconds = Math.min(maxDurationRef.current, elapsedSecondsNow());
        if (mountedRef.current) {
          setState((current) =>
            current.status === "recording" || current.status === "paused"
              ? { ...current, elapsedSeconds: seconds }
              : current,
          );
        }
        if (seconds >= maxDurationRef.current) {
          autoStop();
        }
      }, 250);
    },
    [clearTimer, elapsedSecondsNow],
  );

  const transcribeBlob = useCallback(
    async (blob: Blob, elapsedForState: number) => {
      if (!auth?.token || !auth.orgId) {
        if (mountedRef.current) {
          setState({
            status: "error",
            elapsedSeconds: elapsedForState,
            message: "Sign in to use voice transcription.",
            canRetry: true,
          });
        }
        return;
      }

      const attemptId = ++attemptIdRef.current;
      const controller = new AbortController();
      abortControllerRef.current = controller;

      if (mountedRef.current) {
        setState({ status: "uploading", elapsedSeconds: elapsedForState });
      }

      await Promise.resolve();
      if (!mountedRef.current || attemptId !== attemptIdRef.current) return;

      if (mountedRef.current) {
        setState({ status: "transcribing", elapsedSeconds: elapsedForState });
      }

      try {
        const extension = recordingExtensionForMimeType(blob.type);
        const result = await api.transcriptions.create(auth, {
          file: blob,
          filename: `recording.${extension}`,
          language,
          signal: controller.signal,
        });

        if (!mountedRef.current || attemptId !== attemptIdRef.current) return;
        retainedBlobRef.current = null;
        chunksRef.current = [];
        setState({ status: "success", elapsedSeconds: elapsedForState });
        onTranscript(result);
        resetToIdle();
      } catch (error) {
        if (!mountedRef.current || attemptId !== attemptIdRef.current) return;
        const message = transcriptionErrorMessage(error);
        if (!message) return;
        setState({
          status: "error",
          elapsedSeconds: elapsedForState,
          message,
          canRetry: Boolean(retainedBlobRef.current),
        });
      } finally {
        if (attemptId === attemptIdRef.current) {
          abortControllerRef.current = null;
        }
      }
    },
    [auth, language, onTranscript, resetToIdle],
  );

  const stopRecording = useCallback(async () => {
    if (stopInProgressRef.current) return;
    stopInProgressRef.current = true;
    const finalElapsedSeconds = Math.min(maxDurationRef.current, elapsedSecondsNow());
    clearTimer();
    elapsedBeforePauseMsRef.current = finalElapsedSeconds * 1000;
    activeStartedAtRef.current = null;

    if (mountedRef.current) {
      setState({ status: "ready_to_transcribe", elapsedSeconds: finalElapsedSeconds });
    }

    try {
      const blob = await stopRecorderAndCreateBlob();
      cleanupRecorderAndStream();

      if (cancelledRef.current || !mountedRef.current) return;
      if (blob.size === 0) {
        retainedBlobRef.current = null;
        setState({
          status: "error",
          elapsedSeconds: finalElapsedSeconds,
          message: "No audio was recorded.",
          canRetry: false,
        });
        return;
      }

      retainedBlobRef.current = blob;
      await transcribeBlob(blob, finalElapsedSeconds);
    } catch {
      cleanupRecorderAndStream();
      if (!cancelledRef.current && mountedRef.current) {
        setState({
          status: "error",
          elapsedSeconds: finalElapsedSeconds,
          message: "The recording could not be prepared.",
          canRetry: Boolean(retainedBlobRef.current),
        });
      }
    } finally {
      stopInProgressRef.current = false;
    }
  }, [
    cleanupRecorderAndStream,
    clearTimer,
    elapsedSecondsNow,
    stopRecorderAndCreateBlob,
    transcribeBlob,
  ]);

  const pauseRecording = useCallback(() => {
    const recorder = recorderRef.current;
    if (!recorder || recorder.state !== "recording") return;
    recorder.pause();
    elapsedBeforePauseMsRef.current = elapsedMs();
    activeStartedAtRef.current = null;
    clearTimer();
    setState({ status: "paused", elapsedSeconds: elapsedSecondsNow() });
  }, [clearTimer, elapsedMs, elapsedSecondsNow]);

  const resumeRecording = useCallback(() => {
    const recorder = recorderRef.current;
    if (!recorder || recorder.state !== "paused") return;
    recorder.resume();
    activeStartedAtRef.current = performance.now();
    setState({ status: "recording", elapsedSeconds: elapsedSecondsNow() });
    startTimer(() => void stopRecording());
  }, [elapsedSecondsNow, startTimer, stopRecording]);

  const cancel = useCallback(() => {
    cancelledRef.current = true;
    attemptIdRef.current += 1;
    abortControllerRef.current?.abort();
    abortControllerRef.current = null;
    safelyStopMediaRecorder(recorderRef.current);
    resetRecordingRefs(true);
    if (mountedRef.current) {
      setState({ status: "idle", elapsedSeconds: 0 });
    }
  }, [resetRecordingRefs]);

  const startRecording = useCallback(async () => {
    if (disabled || !hasAuth || isBusy) return;

    const support = getRecordingSupport();
    if (!support.supported) {
      setState({
        status: "unsupported",
        elapsedSeconds: 0,
        message: supportMessage(support.reason),
      });
      return;
    }

    setState({ status: "requesting_permission", elapsedSeconds: 0 });
    cancelledRef.current = false;
    retainedBlobRef.current = null;
    chunksRef.current = [];
    selectedMimeTypeRef.current = support.mimeType;
    elapsedBeforePauseMsRef.current = 0;
    activeStartedAtRef.current = null;

    try {
      const stream = await requestMicrophoneStream();
      if (cancelledRef.current || !mountedRef.current) {
        stopMediaStreamTracks(stream);
        return;
      }

      const recorder = new MediaRecorder(stream, { mimeType: support.mimeType });
      streamRef.current = stream;
      recorderRef.current = recorder;

      recorder.ondataavailable = (event) => {
        if (event.data.size > 0) {
          chunksRef.current.push(event.data);
        }
      };
      recorder.onerror = () => {
        resetRecordingRefs(false);
        if (mountedRef.current) {
          setState({
            status: "error",
            elapsedSeconds: elapsedSecondsNow(),
            message: "The microphone could not be started.",
            canRetry: false,
          });
        }
      };

      recorder.start(1000);
      activeStartedAtRef.current = performance.now();
      setState({ status: "recording", elapsedSeconds: 0 });
      startTimer(() => void stopRecording());
    } catch (error) {
      resetRecordingRefs(true);
      if (mountedRef.current) {
        setState({
          status: "error",
          elapsedSeconds: 0,
          message: microphoneErrorMessage(error),
          canRetry: false,
        });
      }
    }
  }, [disabled, elapsedSecondsNow, hasAuth, isBusy, resetRecordingRefs, startTimer, stopRecording]);

  const retryTranscription = useCallback(() => {
    const blob = retainedBlobRef.current;
    if (!blob) return;
    cancelledRef.current = false;
    void transcribeBlob(blob, state.elapsedSeconds);
  }, [state.elapsedSeconds, transcribeBlob]);

  const idleTooltip = useMemo(() => {
    if (disabled) return "Voice recording is unavailable.";
    if (!hasAuth) return "Sign in to use voice recording.";
    return "Record voice prompt";
  }, [disabled, hasAuth]);

  const renderLanguageSelect = () => (
    <Select
      value={language}
      onValueChange={(value) => setLanguage(value as TranscriptionLanguage)}
      disabled={state.status === "uploading" || state.status === "transcribing"}
    >
      <SelectTrigger
        className="h-8 w-[168px] rounded-md text-xs"
        aria-label="Transcription language"
      >
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        {TRANSCRIPTION_LANGUAGE_OPTIONS.map((option) => (
          <SelectItem key={option.value} value={option.value}>
            {option.label}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );

  return (
    <TooltipProvider delayDuration={150}>
      <div className="flex min-w-0 items-center gap-2">
        {state.status === "idle" || state.status === "success" ? (
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                type="button"
                size="icon"
                variant="ghost"
                aria-label={idleTooltip}
                disabled={disabled || !hasAuth}
                onClick={() => void startRecording()}
              >
                <Mic className="size-4" />
              </Button>
            </TooltipTrigger>
            <TooltipContent>{idleTooltip}</TooltipContent>
          </Tooltip>
        ) : null}

        {state.status === "unsupported" ? (
          <div className="flex min-w-0 items-center gap-2 rounded-md border border-border bg-background px-2 py-1.5 text-sm">
            <Mic className="size-4 text-muted-foreground" />
            <span className="max-w-[220px] truncate text-muted-foreground">{state.message}</span>
          </div>
        ) : null}

        {state.status === "requesting_permission" ? (
          <div className="flex items-center gap-2 rounded-md border border-border bg-background px-2 py-1.5 text-sm text-muted-foreground">
            <Loader2 className="size-4 animate-spin" />
            <span>Starting…</span>
          </div>
        ) : null}

        {state.status === "recording" || state.status === "paused" ? (
          <div className="flex min-w-0 items-center gap-2 rounded-md border border-border bg-background px-2 py-1.5 shadow-sm">
            <span
              className={cn(
                "size-2.5 rounded-full",
                state.status === "recording" ? "bg-destructive" : "bg-muted-foreground",
              )}
              aria-hidden="true"
            />
            <div className="min-w-[3rem] text-sm font-medium tabular-nums">
              {formatElapsedSeconds(elapsedSeconds)}
            </div>
            <div className="hidden w-20 sm:block">
              <Progress value={progress} />
            </div>
            {renderLanguageSelect()}
            {state.status === "recording" ? (
              <IconButton label="Pause recording" onClick={pauseRecording}>
                <Pause className="size-4" />
              </IconButton>
            ) : (
              <IconButton label="Resume recording" onClick={resumeRecording}>
                <Play className="size-4" />
              </IconButton>
            )}
            <IconButton label="Stop and transcribe" onClick={() => void stopRecording()}>
              <Square className="size-4" />
            </IconButton>
            <IconButton label="Cancel recording" onClick={cancel}>
              <X className="size-4" />
            </IconButton>
            {showFinalMinuteWarning ? (
              <span className="hidden text-xs text-destructive md:inline">
                {remainingSeconds}s left
              </span>
            ) : null}
          </div>
        ) : null}

        {["ready_to_transcribe", "uploading", "transcribing"].includes(state.status) ? (
          <div className="flex min-w-0 items-center gap-2 rounded-md border border-border bg-background px-2 py-1.5 text-sm shadow-sm">
            <Loader2 className="size-4 animate-spin text-muted-foreground" />
            <span className="text-muted-foreground">
              {state.status === "ready_to_transcribe" ? "Preparing…" : "Transcribing…"}
            </span>
            <IconButton label="Cancel transcription" onClick={cancel}>
              <X className="size-4" />
            </IconButton>
          </div>
        ) : null}

        {state.status === "error" ? (
          <div className="flex min-w-0 items-center gap-2 rounded-md border border-destructive/30 bg-background px-2 py-1.5 text-sm shadow-sm">
            <span className="max-w-[220px] truncate text-destructive">{state.message}</span>
            {state.canRetry ? (
              <Button type="button" size="sm" variant="outline" onClick={retryTranscription}>
                <RotateCcw className="size-3.5" />
                Retry
              </Button>
            ) : null}
            <Button type="button" size="sm" variant="ghost" onClick={cancel}>
              Discard
            </Button>
          </div>
        ) : null}
      </div>
    </TooltipProvider>
  );
}

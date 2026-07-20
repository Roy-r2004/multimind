import type { ApiModelAnswer, ApiTurn } from "./api/types";

export type TurnAnswerCard = {
  modelId: string;
  answer?: ApiModelAnswer;
  status: string;
};

const IN_FLIGHT_STATUSES = new Set(["pending", "running"]);
const GEMINI_EQUIVALENT_IDS = new Set([
  "gemini",
  "google/gemini-2.5-pro",
  "or:google--gemini-2.5-pro",
]);

function answerRank(answer: ApiModelAnswer): number {
  if (answer.status === "completed" && answer.text) return 50;
  if (answer.status === "completed") return 40;
  if (!IN_FLIGHT_STATUSES.has(answer.status)) return 30;
  if (answer.status === "running") return 20;
  return 10;
}

export function modelCardIdentity(modelId: string): string {
  return GEMINI_EQUIVALENT_IDS.has(modelId) ? "gemini" : modelId;
}

export function isTurnInFlight(turn: Pick<ApiTurn, "status">): boolean {
  return IN_FLIGHT_STATUSES.has(turn.status);
}

function preferAnswer(current: ApiModelAnswer, next: ApiModelAnswer): ApiModelAnswer {
  const currentRank = answerRank(current);
  const nextRank = answerRank(next);
  if (nextRank > currentRank) return next;
  if (nextRank < currentRank) return current;
  if (!current.text && next.text) return next;
  if (!current.error_message && next.error_message) return next;
  return current;
}

function uniqueSavedAnswers(savedAnswers: ApiModelAnswer[]): ApiModelAnswer[] {
  const cards: ApiModelAnswer[] = [];
  const indexByIdentity = new Map<string, number>();

  for (const answer of savedAnswers) {
    const identity = modelCardIdentity(answer.model_id);
    const existingIndex = indexByIdentity.get(identity);
    if (existingIndex === undefined) {
      indexByIdentity.set(identity, cards.length);
      cards.push(answer);
      continue;
    }
    cards[existingIndex] = preferAnswer(cards[existingIndex], answer);
  }

  return cards;
}

function findBestAnswerIndexForIdentity(
  savedAnswers: ApiModelAnswer[],
  selectedModelId: string,
  usedIndexes: Set<number>,
): number | null {
  const selectedIdentity = modelCardIdentity(selectedModelId);
  let bestIndex: number | null = null;

  for (const [index, answer] of savedAnswers.entries()) {
    if (usedIndexes.has(index) || modelCardIdentity(answer.model_id) !== selectedIdentity) {
      continue;
    }
    if (bestIndex === null || preferAnswer(savedAnswers[bestIndex], answer) === answer) {
      bestIndex = index;
    }
  }

  return bestIndex;
}

export function deriveTurnAnswerCards(
  turn: Pick<ApiTurn, "status" | "model_answers">,
  selectedModelIds: string[],
): TurnAnswerCard[] {
  const savedAnswers = turn.model_answers ?? [];

  if (!isTurnInFlight(turn)) {
    return uniqueSavedAnswers(savedAnswers).map((answer) => ({
      modelId: answer.model_id,
      answer,
      status: answer.status,
    }));
  }

  const usedIndexes = new Set<number>();
  const usedIdentities = new Set<string>();
  const cards: TurnAnswerCard[] = [];

  for (const selectedModelId of selectedModelIds) {
    const selectedIdentity = modelCardIdentity(selectedModelId);
    if (usedIdentities.has(selectedIdentity)) continue;

    const answerIndex = findBestAnswerIndexForIdentity(savedAnswers, selectedModelId, usedIndexes);
    const answer = answerIndex === null ? undefined : savedAnswers[answerIndex];
    if (answerIndex !== null) usedIndexes.add(answerIndex);

    const modelId = answer?.model_id ?? selectedModelId;
    usedIdentities.add(modelCardIdentity(modelId));
    cards.push({
      modelId,
      answer,
      status: answer?.status ?? "pending",
    });
  }

  for (const [index, answer] of savedAnswers.entries()) {
    const identity = modelCardIdentity(answer.model_id);
    if (!usedIndexes.has(index) && !usedIdentities.has(identity)) {
      usedIdentities.add(identity);
      cards.push({
        modelId: answer.model_id,
        answer,
        status: answer.status,
      });
    }
  }

  return cards;
}

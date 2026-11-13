/**
 * Client for the public correction API (`context/api-contract.md`).
 *
 * The browser only ever talks to the backend endpoint; the LLM provider and its
 * credentials stay on the server (`context/architecture.md`).
 */

/** Maximum accepted text length for V1 (`context/project.md`). */
export const MAX_TEXT_LENGTH = 1000;

const DEFAULT_API_BASE_URL = "http://localhost:8000";
const CORRECT_PATH = "/api/v1/correct";

/** Shown when the request never reached the backend. */
export const NETWORK_ERROR_MESSAGE =
  "ارتباط با سرور برقرار نشد. لطفاً اتصال شبکه را بررسی کنید.";

/** Shown when the backend responded but the body was not usable. */
export const UNEXPECTED_ERROR_MESSAGE = "خطای نامشخصی رخ داد. لطفاً دوباره تلاش کنید.";

/**
 * Persian messages per documented error code, used only as a fallback when the
 * backend response carries no message of its own.
 */
const ERROR_MESSAGES: Record<string, string> = {
  INVALID_INPUT: "متن ارسالی نامعتبر است.",
  TEXT_TOO_LONG: "متن واردشده بیش از حداکثر مجاز است.",
  PROVIDER_UNAVAILABLE: "سرویس اصلاح متن در حال حاضر در دسترس نیست.",
  PROVIDER_TIMEOUT: "زمان انتظار برای پاسخ سرویس اصلاح متن به پایان رسید.",
  PROVIDER_RATE_LIMIT:
    "محدودیت استفاده از سرویس اصلاح متن فعال شده است. لطفاً بعداً تلاش کنید.",
  PROVIDER_AUTH_ERROR: "ارتباط با سرویس اصلاح متن با خطا مواجه شد.",
  INTERNAL_ERROR: "خطای داخلی سرور رخ داد.",
};

/** An error carrying a documented API error code (`api-contract.md`). */
export class CorrectionApiError extends Error {
  readonly code: string;

  constructor(code: string, message: string) {
    super(message);
    this.name = "CorrectionApiError";
    this.code = code;
  }
}

/** The configured backend base URL, trimmed of trailing slashes. */
function apiBaseUrl(): string {
  const configured = import.meta.env.VITE_API_BASE_URL?.trim();
  return (configured ? configured : DEFAULT_API_BASE_URL).replace(/\/+$/, "");
}

async function readJson(response: Response): Promise<unknown> {
  try {
    return await response.json();
  } catch {
    return null;
  }
}

/** Build a {@link CorrectionApiError} from a documented error body. */
function extractError(payload: unknown): CorrectionApiError {
  if (payload !== null && typeof payload === "object") {
    const body = payload as Record<string, unknown>;
    const code = typeof body.error === "string" ? body.error : "";
    const message =
      typeof body.message === "string" && body.message.trim() !== "" ? body.message : "";

    return new CorrectionApiError(
      code !== "" ? code : "INTERNAL_ERROR",
      message !== "" ? message : ERROR_MESSAGES[code] ?? UNEXPECTED_ERROR_MESSAGE,
    );
  }

  return new CorrectionApiError("INTERNAL_ERROR", UNEXPECTED_ERROR_MESSAGE);
}

/** Read `corrected_text`, or `null` when the body is not a usable success. */
function extractCorrectedText(payload: unknown): string | null {
  if (payload !== null && typeof payload === "object") {
    const value = (payload as Record<string, unknown>).corrected_text;
    if (typeof value === "string" && value.trim() !== "") {
      return value;
    }
  }
  return null;
}

/**
 * Consent choice for the optional evaluation log (`context/correction-logging.md`).
 *
 * The flag is always sent explicitly, and the backend — not the browser — is
 * what enforces it: `true` allows storing the submitted and corrected text for
 * quality evaluation, `false` stores neither.
 */
export interface CorrectionRequestOptions {
  storeForEvaluation: boolean;
  signal?: AbortSignal;
}

/**
 * Send `text` to `POST /api/v1/correct` and resolve with the corrected text.
 *
 * Never fabricates a result: any failure rejects with a
 * {@link CorrectionApiError} (or the abort error raised by `fetch`).
 */
export async function requestCorrection(
  text: string,
  options: CorrectionRequestOptions,
): Promise<string> {
  let response: Response;

  try {
    response = await fetch(`${apiBaseUrl()}${CORRECT_PATH}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        text,
        store_for_evaluation: options.storeForEvaluation,
      }),
      signal: options.signal,
    });
  } catch (error) {
    // Caller-initiated cancellation must not be reported as a network failure.
    if (error instanceof Error && error.name === "AbortError") {
      throw error;
    }
    throw new CorrectionApiError("NETWORK_ERROR", NETWORK_ERROR_MESSAGE);
  }

  const payload = await readJson(response);

  if (!response.ok) {
    throw extractError(payload);
  }

  const correctedText = extractCorrectedText(payload);
  if (correctedText === null) {
    throw new CorrectionApiError("INTERNAL_ERROR", UNEXPECTED_ERROR_MESSAGE);
  }

  return correctedText;
}

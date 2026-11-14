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
const EXPORT_DOCX_PATH = "/api/v1/export-docx";

/** Suggested filename of the generated Word file (`word-export-spec.md`). */
export const EXPORT_FILENAME = "virastar-export.docx";

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

/**
 * A successful correction together with the provider slot that produced it.
 *
 * `modelUsed` and `providerName` are empty when the backend does not report
 * them, so an older backend response still yields a usable correction.
 */
export interface CorrectionResult {
  text: string;
  modelUsed: string;
  providerName: string;
}

/** Read the optional metadata string, defaulting to `""`. */
function optionalString(body: Record<string, unknown>, key: string): string {
  const value = body[key];
  return typeof value === "string" ? value.trim() : "";
}

/** Read a usable success body, or `null` when `corrected_text` is missing. */
function extractCorrection(payload: unknown): CorrectionResult | null {
  if (payload !== null && typeof payload === "object") {
    const body = payload as Record<string, unknown>;
    const value = body.corrected_text;
    if (typeof value === "string" && value.trim() !== "") {
      return {
        text: value,
        modelUsed: optionalString(body, "model_used"),
        providerName: optionalString(body, "provider_name"),
      };
    }
  }
  return null;
}

/**
 * Proofreading style accepted by `POST /api/v1/correct` (`api-contract.md`).
 *
 * `preserve` corrects errors without changing the writer's tone and register;
 * `formal` also rewrites the text in a formal, administrative register.
 */
export type CorrectionStyle = "preserve" | "formal";

export interface CorrectionRequestOptions {
  style: CorrectionStyle;
  signal?: AbortSignal;
}

/**
 * Send `text` to `POST /api/v1/correct` in the chosen `style` and resolve with
 * the corrected text and the provider slot that produced it.
 *
 * Never fabricates a result: any failure rejects with a
 * {@link CorrectionApiError} (or the abort error raised by `fetch`).
 */
export async function requestCorrection(
  text: string,
  options: CorrectionRequestOptions,
): Promise<CorrectionResult> {
  const payload = await postJson(
    CORRECT_PATH,
    { text, style: options.style },
    options.signal,
  );

  const result = extractCorrection(payload);
  if (result === null) {
    throw new CorrectionApiError("INTERNAL_ERROR", UNEXPECTED_ERROR_MESSAGE);
  }

  return result;
}

/**
 * Formatting options of `POST /api/v1/export-docx`, mirroring `ExportOptions`
 * on the backend and the defaults documented in `context/word-export-spec.md`.
 */
export type ExportFont = "Vazirmatn" | "Sahel" | "Tahoma";
export type ExportFontSize = 12 | 14 | 16 | 18;
export type ExportLineSpacing = 1 | 1.15 | 1.5 | 2;
export type ExportAlignment = "right" | "justify" | "center";
export type ExportMargins = "normal" | "narrow" | "wide";
export type ExportPageNumberPosition = "center" | "right";

export interface ExportOptions {
  font: ExportFont;
  font_size: ExportFontSize;
  line_spacing: ExportLineSpacing;
  alignment: ExportAlignment;
  margins: ExportMargins;
  page_numbers: boolean;
  page_number_position: ExportPageNumberPosition;
  page_border: boolean;
}

/** The spec's defaults; an untouched settings form sends exactly these. */
export const DEFAULT_EXPORT_OPTIONS: ExportOptions = {
  font: "Vazirmatn",
  font_size: 14,
  line_spacing: 1.5,
  alignment: "justify",
  margins: "normal",
  page_numbers: true,
  page_number_position: "center",
  page_border: false,
};

/**
 * Send the corrected text and the chosen options to `POST /api/v1/export-docx`
 * and resolve with the generated `.docx` file.
 *
 * Failure uses the same {@link CorrectionApiError} as the correction request,
 * because both endpoints share the documented `{error, message}` envelope.
 */
export async function requestDocxExport(
  text: string,
  options: ExportOptions,
): Promise<Blob> {
  let response: Response;

  try {
    response = await fetch(`${apiBaseUrl()}${EXPORT_DOCX_PATH}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text, options }),
    });
  } catch {
    throw new CorrectionApiError("NETWORK_ERROR", NETWORK_ERROR_MESSAGE);
  }

  if (!response.ok) {
    throw extractError(await readJson(response));
  }

  try {
    return await response.blob();
  } catch {
    throw new CorrectionApiError("INTERNAL_ERROR", UNEXPECTED_ERROR_MESSAGE);
  }
}

/** POST a JSON body and return the parsed success body or throw the API error. */
async function postJson(
  path: string,
  body: Record<string, unknown>,
  signal?: AbortSignal,
): Promise<unknown> {
  let response: Response;

  try {
    response = await fetch(`${apiBaseUrl()}${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      signal,
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

  return payload;
}

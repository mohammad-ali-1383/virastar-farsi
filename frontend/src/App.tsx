/**
 * Persian proofreading interface (`context/ui.md`).
 *
 * The user types text, submits it, and picks a proofreading style in a Persian
 * dialog before the backend correction API is called; the corrected result
 * appears in a separate panel without the original input being replaced. All
 * provider communication happens on the backend.
 */

import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";

import ExportSettingsDialog from "./ExportSettingsDialog";
import StyleDialog from "./StyleDialog";
import {
  CorrectionApiError,
  EXPORT_FILENAME,
  MAX_TEXT_LENGTH,
  requestCorrection,
  requestDocxExport,
  type CorrectionStyle,
  type ExportOptions,
} from "./api";

type Status = "idle" | "loading" | "success" | "error";
type CopyState = "idle" | "copied" | "error";
type ExportState = "idle" | "loading" | "error";

const EMPTY_TEXT_MESSAGE = "لطفاً متن فارسی مورد نظر را وارد کنید.";
const TOO_LONG_MESSAGE = "متن واردشده بیش از حداکثر مجاز است.";
const COPY_ERROR_MESSAGE = "کپی متن ممکن نشد؛ لطفاً متن را دستی انتخاب و کپی کنید.";
const EXPORT_ERROR_MESSAGE = "ساخت فایل ورد ممکن نشد. لطفاً دوباره تلاش کنید.";
const EXPORT_LOADING_LABEL = "در حال ساخت فایل…";
const EXPORT_LABEL = "دریافت فایل ورد";

/**
 * Save a generated file under `filename` without leaving the page.
 *
 * The object URL is revoked asynchronously: the click that starts the download
 * reads it in the browser, and revoking it in the same tick is not reliable in
 * every browser.
 */
function downloadBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 0);
}

export default function App() {
  const [text, setText] = useState("");
  const [status, setStatus] = useState<Status>("idle");
  const [correctedText, setCorrectedText] = useState("");
  // Model that produced the current result; empty when the backend omits it.
  const [modelUsed, setModelUsed] = useState("");
  const [errorMessage, setErrorMessage] = useState("");
  const [validationMessage, setValidationMessage] = useState("");
  const [copyState, setCopyState] = useState<CopyState>("idle");
  // The style is chosen on every submission: nothing is sent while it is open.
  const [styleOpen, setStyleOpen] = useState(false);
  // The Word export is available from the result panel only.
  const [exportOpen, setExportOpen] = useState(false);
  const [exportState, setExportState] = useState<ExportState>("idle");
  const [exportError, setExportError] = useState("");

  const requestRef = useRef<AbortController | null>(null);
  const copyTimerRef = useRef<number | null>(null);

  // Cancel an in-flight request and timers when the component unmounts.
  useEffect(() => {
    return () => {
      requestRef.current?.abort();
      if (copyTimerRef.current !== null) {
        window.clearTimeout(copyTimerRef.current);
      }
    };
  }, []);

  const characterCount = text.length;
  const isTooLong = characterCount > MAX_TEXT_LENGTH;
  const isEmpty = text.trim().length === 0;
  const isLoading = status === "loading";
  const canSubmit = !isLoading && !isEmpty && !isTooLong;

  const handleTextChange = useCallback((value: string) => {
    setText(value);
    setValidationMessage("");
  }, []);

  const sendCorrection = useCallback(
    async (style: CorrectionStyle) => {
      requestRef.current?.abort();
      const controller = new AbortController();
      requestRef.current = controller;

      setStatus("loading");
      setErrorMessage("");
      setValidationMessage("");
      setCorrectedText("");
      setModelUsed("");
      setCopyState("idle");
      // A pending export belongs to the previous result.
      setExportOpen(false);
      setExportState("idle");
      setExportError("");

      try {
        const result = await requestCorrection(text.trim(), {
          style,
          signal: controller.signal,
        });
        setCorrectedText(result.text);
        setModelUsed(result.modelUsed);
        setStatus("success");
      } catch (error) {
        if (error instanceof Error && error.name === "AbortError") {
          return;
        }
        setErrorMessage(
          error instanceof CorrectionApiError
            ? error.message
            : "خطای نامشخصی رخ داد. لطفاً دوباره تلاش کنید.",
        );
        setStatus("error");
      } finally {
        if (requestRef.current === controller) {
          requestRef.current = null;
        }
      }
    },
    [text],
  );

  const handleSubmit = useCallback(
    (event: FormEvent<HTMLFormElement>) => {
      event.preventDefault();

      // Defence in depth: the submit button is disabled in both states.
      if (isEmpty) {
        setValidationMessage(EMPTY_TEXT_MESSAGE);
        return;
      }
      if (isTooLong) {
        setValidationMessage(TOO_LONG_MESSAGE);
        return;
      }

      // Choose the style first: no request is sent until the user picks one.
      setStyleOpen(true);
    },
    [isEmpty, isTooLong],
  );

  const handleStyleChoice = useCallback(
    (style: CorrectionStyle) => {
      setStyleOpen(false);
      void sendCorrection(style);
    },
    [sendCorrection],
  );

  // Dismissing the dialog makes no choice, so nothing is sent.
  const handleStyleDismiss = useCallback(() => setStyleOpen(false), []);

  const handleExportDismiss = useCallback(() => setExportOpen(false), []);

  const handleExportConfirm = useCallback(
    async (options: ExportOptions) => {
      if (correctedText === "") {
        return;
      }

      setExportOpen(false);
      setExportState("loading");
      setExportError("");

      try {
        const file = await requestDocxExport(correctedText, options);
        downloadBlob(file, EXPORT_FILENAME);
        setExportState("idle");
      } catch (error) {
        setExportError(
          error instanceof CorrectionApiError ? error.message : EXPORT_ERROR_MESSAGE,
        );
        setExportState("error");
      }
    },
    [correctedText],
  );

  const handleCopy = useCallback(async () => {
    if (correctedText === "") {
      return;
    }

    try {
      await navigator.clipboard.writeText(correctedText);
      setCopyState("copied");
      if (copyTimerRef.current !== null) {
        window.clearTimeout(copyTimerRef.current);
      }
      copyTimerRef.current = window.setTimeout(() => setCopyState("idle"), 2000);
    } catch {
      setCopyState("error");
    }
  }, [correctedText]);

  const inlineError = isTooLong ? TOO_LONG_MESSAGE : validationMessage;

  return (
    <main className="app">
      <header className="app__header">
        <h1>ویراستار متن فارسی</h1>
        <p className="app__intro">
          متن فارسی خود را وارد کنید تا املا، دستور زبان، نشانه‌گذاری، فاصله‌ها و نویسه‌آرایی آن
          اصلاح شود؛ معنای متن حفظ می‌شود.
        </p>
      </header>

      <form className="form" onSubmit={handleSubmit} noValidate>
        <label className="form__label" htmlFor="source-text">
          متن شما
        </label>
        <textarea
          id="source-text"
          className="form__textarea"
          value={text}
          onChange={(event) => handleTextChange(event.target.value)}
          placeholder="متن فارسی خود را اینجا بنویسید…"
          dir="rtl"
          disabled={isLoading}
          aria-describedby="text-counter"
          aria-invalid={isTooLong}
        />

        <div className="form__meta">
          <p
            id="text-counter"
            className={isTooLong ? "counter counter--over" : "counter"}
            aria-live="polite"
          >
            {characterCount} / {MAX_TEXT_LENGTH}
          </p>
          {inlineError !== "" && (
            <p className="field-error" role="alert">
              {inlineError}
            </p>
          )}
        </div>

        <div className="actions">
          <button type="submit" className="button" disabled={!canSubmit}>
            {isLoading ? "در حال اصلاح…" : "اصلاح متن"}
          </button>
        </div>
      </form>

      {styleOpen && (
        <StyleDialog onChoose={handleStyleChoice} onDismiss={handleStyleDismiss} />
      )}

      {exportOpen && (
        <ExportSettingsDialog
          onConfirm={handleExportConfirm}
          onDismiss={handleExportDismiss}
        />
      )}

      {status === "error" && errorMessage !== "" && (
        <p className="error" role="alert">
          {errorMessage}
        </p>
      )}

      {status === "success" && correctedText !== "" && (
        <section className="result">
          <div className="result__header">
            <h2>متن اصلاح‌شده</h2>
            <div className="result__actions">
              <button type="button" className="button--ghost" onClick={handleCopy}>
                کپی
              </button>
              <button
                type="button"
                className="button--ghost"
                onClick={() => setExportOpen(true)}
                disabled={exportState === "loading"}
                aria-haspopup="dialog"
              >
                {exportState === "loading" ? EXPORT_LOADING_LABEL : EXPORT_LABEL}
              </button>
            </div>
          </div>
          <p className="result__text" dir="rtl">
            {correctedText}
          </p>
          {copyState === "copied" && (
            <p className="copy-status" role="status">
              متن اصلاح‌شده کپی شد.
            </p>
          )}
          {copyState === "error" && (
            <p className="copy-status copy-status--error" role="alert">
              {COPY_ERROR_MESSAGE}
            </p>
          )}
          {exportState === "error" && exportError !== "" && (
            <p className="copy-status copy-status--error" role="alert">
              {exportError}
            </p>
          )}
        </section>
      )}

      {status === "success" && correctedText !== "" && modelUsed !== "" && (
        <p className="result-model" dir="rtl">
          ویرایش‌شده با مدل: {modelUsed}
        </p>
      )}
    </main>
  );
}

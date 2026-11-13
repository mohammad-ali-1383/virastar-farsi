/**
 * Persian proofreading interface (`context/ui.md`).
 *
 * The user types text, submits it, and answers a Persian consent dialog before
 * the backend correction API is called; the corrected result appears in a
 * separate panel without the original input being replaced. All provider
 * communication and all consent enforcement happen on the backend.
 */

import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";

import ConsentDialog from "./ConsentDialog";
import { CorrectionApiError, MAX_TEXT_LENGTH, requestCorrection } from "./api";

type Status = "idle" | "loading" | "success" | "error";
type CopyState = "idle" | "copied" | "error";

const EMPTY_TEXT_MESSAGE = "لطفاً متن فارسی مورد نظر را وارد کنید.";
const TOO_LONG_MESSAGE = "متن واردشده بیش از حداکثر مجاز است.";
const COPY_ERROR_MESSAGE = "کپی متن ممکن نشد؛ لطفاً متن را دستی انتخاب و کپی کنید.";

export default function App() {
  const [text, setText] = useState("");
  const [status, setStatus] = useState<Status>("idle");
  const [correctedText, setCorrectedText] = useState("");
  const [errorMessage, setErrorMessage] = useState("");
  const [validationMessage, setValidationMessage] = useState("");
  const [copyState, setCopyState] = useState<CopyState>("idle");
  // Consent is asked for on every submission: nothing is sent while it is open.
  const [consentOpen, setConsentOpen] = useState(false);

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
    async (storeForEvaluation: boolean) => {
      requestRef.current?.abort();
      const controller = new AbortController();
      requestRef.current = controller;

      setStatus("loading");
      setErrorMessage("");
      setValidationMessage("");
      setCorrectedText("");
      setCopyState("idle");

      try {
        const result = await requestCorrection(text.trim(), {
          storeForEvaluation,
          signal: controller.signal,
        });
        setCorrectedText(result);
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

      // Ask for consent first: no request is sent until the user chooses.
      setConsentOpen(true);
    },
    [isEmpty, isTooLong],
  );

  const handleConsentChoice = useCallback(
    (storeForEvaluation: boolean) => {
      setConsentOpen(false);
      void sendCorrection(storeForEvaluation);
    },
    [sendCorrection],
  );

  // Dismissing the dialog makes no choice, so nothing is sent.
  const handleConsentDismiss = useCallback(() => setConsentOpen(false), []);

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

      {consentOpen && (
        <ConsentDialog onChoose={handleConsentChoice} onDismiss={handleConsentDismiss} />
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
            <button type="button" className="button--ghost" onClick={handleCopy}>
              کپی
            </button>
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
        </section>
      )}
    </main>
  );
}

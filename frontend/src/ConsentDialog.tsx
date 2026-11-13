/**
 * Consent dialog for the optional evaluation log (`context/ui.md` ->
 * Privacy and consent, `context/correction-logging.md`).
 *
 * Shown after the user submits text and before any request is sent. Both
 * choices run the correction; only the stored-for-evaluation choice opts in to
 * keeping the texts. Dismissing the dialog — with Escape, the backdrop, or the
 * close button — sends nothing at all.
 */

import { useEffect, useRef } from "react";

/** Labels are exported so tests and callers cannot drift from the UI copy. */
export const CONSENT_ACCEPT_LABEL = "موافقم، ذخیره شود";
export const CONSENT_DECLINE_LABEL = "فقط متنم را اصلاح کن";
const CLOSE_LABEL = "بستن";

interface ConsentDialogProps {
  /** Called with the user's explicit choice; the dialog then closes. */
  onChoose: (storeForEvaluation: boolean) => void;
  /** Called when the user makes no choice; no request may be sent. */
  onDismiss: () => void;
}

export default function ConsentDialog({ onChoose, onDismiss }: ConsentDialogProps) {
  const acceptRef = useRef<HTMLButtonElement>(null);

  // Escape closes the dialog without choosing, i.e. without a request.
  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        onDismiss();
      }
    };

    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, [onDismiss]);

  // Move focus into the dialog so keyboard users are not left on the form.
  useEffect(() => {
    acceptRef.current?.focus();
  }, []);

  return (
    <div className="consent" onClick={onDismiss}>
      <div
        className="consent__panel"
        role="dialog"
        aria-modal="true"
        aria-labelledby="consent-title"
        aria-describedby="consent-description"
        onClick={(event) => event.stopPropagation()}
      >
        <h2 id="consent-title">ذخیره متن برای ارزیابی کیفیت</h2>
        <p id="consent-description" className="consent__text">
          متن شما در هر دو حالت اصلاح می‌شود. تفاوت فقط در ذخیره‌سازی است: اگر موافق باشید، متن
          اصلی و نتیجهٔ اصلاح‌شده برای بررسی کیفیت ذخیره می‌شود؛ اگر نخواهید، هیچ‌کدام ذخیره
          نمی‌شوند.
        </p>

        <div className="consent__actions">
          <button
            ref={acceptRef}
            type="button"
            className="button"
            onClick={() => onChoose(true)}
          >
            {CONSENT_ACCEPT_LABEL}
          </button>
          <button
            type="button"
            className="button button--secondary"
            onClick={() => onChoose(false)}
          >
            {CONSENT_DECLINE_LABEL}
          </button>
        </div>

        <button
          type="button"
          className="consent__close"
          onClick={onDismiss}
          aria-label={CLOSE_LABEL}
        >
          بستن
        </button>
      </div>
    </div>
  );
}

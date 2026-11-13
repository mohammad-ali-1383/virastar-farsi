/**
 * Style dialog (`context/ui.md` -> Proofreading style, `context/api-contract.md`).
 *
 * Opened after the user submits text and before any request is sent: the user
 * picks how the text should be edited, and only that choice triggers a
 * correction. «ویرایش با حفظ لحن» is the recommended default, so it is focused
 * and styled as the primary action. Dismissing the dialog — with Escape, the
 * backdrop, or the close button — sends nothing at all.
 */

import { useEffect, useRef } from "react";

import type { CorrectionStyle } from "./api";

/** Labels are exported so tests and callers cannot drift from the UI copy. */
export const STYLE_DIALOG_TITLE = "شیوهٔ ویرایش متن";
export const STYLE_PRESERVE_LABEL = "ویرایش با حفظ لحن";
export const STYLE_FORMAL_LABEL = "تبدیل به لحن رسمی و اداری";
export const STYLE_PRESERVE_HINT = "پیشنهاد ما — فقط خطاها اصلاح می‌شود و لحن شما می‌ماند.";
export const STYLE_FORMAL_HINT = "متن به لحن رسمی و اداری بازنویسی می‌شود.";
const CLOSE_LABEL = "بستن";

interface StyleDialogProps {
  /** Called with the chosen style; the dialog then closes and the request starts. */
  onChoose: (style: CorrectionStyle) => void;
  /** Called when the user makes no choice; no request may be sent. */
  onDismiss: () => void;
}

export default function StyleDialog({ onChoose, onDismiss }: StyleDialogProps) {
  const preserveRef = useRef<HTMLButtonElement>(null);

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

  // Focus the recommended default so keyboard users can just press Enter.
  useEffect(() => {
    preserveRef.current?.focus();
  }, []);

  return (
    <div className="dialog" onClick={onDismiss}>
      <div
        className="dialog__panel"
        role="dialog"
        aria-modal="true"
        aria-labelledby="style-dialog-title"
        aria-describedby="style-dialog-description"
        onClick={(event) => event.stopPropagation()}
      >
        <h2 id="style-dialog-title">{STYLE_DIALOG_TITLE}</h2>
        <p id="style-dialog-description" className="dialog__text">
          متن شما در هر دو حالت اصلاح می‌شود؛ فقط شیوهٔ ویرایش تفاوت دارد.
        </p>

        <div className="dialog__choices">
          <button
            ref={preserveRef}
            type="button"
            className="button dialog__choice"
            onClick={() => onChoose("preserve")}
          >
            <span className="dialog__choice-label">{STYLE_PRESERVE_LABEL}</span>
            <span className="dialog__choice-hint">{STYLE_PRESERVE_HINT}</span>
          </button>
          <button
            type="button"
            className="button button--secondary dialog__choice"
            onClick={() => onChoose("formal")}
          >
            <span className="dialog__choice-label">{STYLE_FORMAL_LABEL}</span>
            <span className="dialog__choice-hint">{STYLE_FORMAL_HINT}</span>
          </button>
        </div>

        <button
          type="button"
          className="dialog__close"
          onClick={onDismiss}
          aria-label={CLOSE_LABEL}
        >
          بستن
        </button>
      </div>
    </div>
  );
}

/**
 * Settings dialog for the Word export (`context/word-export-spec.md`).
 *
 * Opened from the result panel after a correction. It only collects the
 * formatting options and hands them to the caller on confirm; the request and
 * the download itself stay in `App`, next to the correction flow. Escape, the
 * backdrop, and the close button dismiss the dialog without exporting.
 */

import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";

import {
  DEFAULT_EXPORT_OPTIONS,
  type ExportAlignment,
  type ExportFont,
  type ExportFontSize,
  type ExportLineSpacing,
  type ExportMargins,
  type ExportOptions,
  type ExportPageNumberPosition,
} from "./api";

export const EXPORT_DIALOG_TITLE = "تنظیمات فایل ورد";
export const EXPORT_CONFIRM_LABEL = "ساخت و دریافت فایل";
export const EXPORT_CANCEL_LABEL = "انصراف";

const CLOSE_LABEL = "بستن";

const FONT_OPTIONS: ReadonlyArray<{ value: ExportFont; label: string }> = [
  { value: "Vazirmatn", label: "وزیرمتن" },
  { value: "Sahel", label: "ساحل" },
  { value: "Tahoma", label: "تاهوما" },
];

const FONT_SIZE_OPTIONS: readonly ExportFontSize[] = [12, 14, 16, 18];
const LINE_SPACING_OPTIONS: readonly ExportLineSpacing[] = [1, 1.15, 1.5, 2];

const ALIGNMENT_OPTIONS: ReadonlyArray<{ value: ExportAlignment; label: string }> = [
  { value: "right", label: "راست‌چین" },
  { value: "justify", label: "دوطرفه" },
  { value: "center", label: "وسط‌چین" },
];

const MARGIN_OPTIONS: ReadonlyArray<{ value: ExportMargins; label: string }> = [
  { value: "normal", label: "معمولی" },
  { value: "narrow", label: "باریک" },
  { value: "wide", label: "پهن" },
];

const PAGE_NUMBER_POSITIONS: ReadonlyArray<{
  value: ExportPageNumberPosition;
  label: string;
}> = [
  { value: "center", label: "وسط" },
  { value: "right", label: "راست" },
];

interface ExportSettingsDialogProps {
  /** Called with the chosen options when the user confirms the export. */
  onConfirm: (options: ExportOptions) => void;
  /** Called when the user cancels; no request may be sent. */
  onDismiss: () => void;
}

export default function ExportSettingsDialog({
  onConfirm,
  onDismiss,
}: ExportSettingsDialogProps) {
  const [options, setOptions] = useState<ExportOptions>(DEFAULT_EXPORT_OPTIONS);
  const confirmRef = useRef<HTMLButtonElement>(null);

  // Escape closes the dialog without exporting.
  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        onDismiss();
      }
    };

    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, [onDismiss]);

  // Move focus into the dialog so keyboard users are not left on the result.
  useEffect(() => {
    confirmRef.current?.focus();
  }, []);

  const update = useCallback(
    <Key extends keyof ExportOptions>(key: Key, value: ExportOptions[Key]) => {
      setOptions((current) => ({ ...current, [key]: value }));
    },
    [],
  );

  const handleSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    onConfirm(options);
  };

  return (
    <div className="dialog" onClick={onDismiss}>
      <div
        className="dialog__panel dialog__panel--settings"
        role="dialog"
        aria-modal="true"
        aria-labelledby="export-settings-title"
        onClick={(event) => event.stopPropagation()}
      >
        <h2 id="export-settings-title">{EXPORT_DIALOG_TITLE}</h2>
        <p className="dialog__text">
          ظاهر فایل Word را انتخاب کنید؛ فایل با متن اصلاح‌شده ساخته می‌شود.
        </p>

        <form className="settings" onSubmit={handleSubmit}>
          <div className="settings__grid">
            <div className="settings__field">
              <label htmlFor="export-font">فونت</label>
              <select
                id="export-font"
                value={options.font}
                onChange={(event) => update("font", event.target.value as ExportFont)}
              >
                {FONT_OPTIONS.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </select>
            </div>

            <div className="settings__field">
              <label htmlFor="export-font-size">اندازه قلم</label>
              <select
                id="export-font-size"
                value={String(options.font_size)}
                onChange={(event) =>
                  update("font_size", Number(event.target.value) as ExportFontSize)
                }
              >
                {FONT_SIZE_OPTIONS.map((size) => (
                  <option key={size} value={size}>
                    {size}
                  </option>
                ))}
              </select>
            </div>

            <div className="settings__field">
              <label htmlFor="export-line-spacing">فاصلهٔ خطوط</label>
              <select
                id="export-line-spacing"
                value={String(options.line_spacing)}
                onChange={(event) =>
                  update(
                    "line_spacing",
                    Number(event.target.value) as ExportLineSpacing,
                  )
                }
              >
                {LINE_SPACING_OPTIONS.map((spacing) => (
                  <option key={spacing} value={spacing}>
                    {spacing}
                  </option>
                ))}
              </select>
            </div>

            <div className="settings__field">
              <label htmlFor="export-alignment">تراز متن</label>
              <select
                id="export-alignment"
                value={options.alignment}
                onChange={(event) =>
                  update("alignment", event.target.value as ExportAlignment)
                }
              >
                {ALIGNMENT_OPTIONS.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </select>
            </div>

            <div className="settings__field">
              <label htmlFor="export-margins">حاشیهٔ صفحه</label>
              <select
                id="export-margins"
                value={options.margins}
                onChange={(event) =>
                  update("margins", event.target.value as ExportMargins)
                }
              >
                {MARGIN_OPTIONS.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </select>
            </div>

            <div className="settings__field">
              <label htmlFor="export-page-number-position">جای شماره صفحه</label>
              <select
                id="export-page-number-position"
                value={options.page_number_position}
                disabled={!options.page_numbers}
                onChange={(event) =>
                  update(
                    "page_number_position",
                    event.target.value as ExportPageNumberPosition,
                  )
                }
              >
                {PAGE_NUMBER_POSITIONS.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </select>
            </div>
          </div>

          <div className="settings__checks">
            <div className="settings__check">
              <input
                id="export-page-numbers"
                type="checkbox"
                checked={options.page_numbers}
                onChange={(event) => update("page_numbers", event.target.checked)}
              />
              <label htmlFor="export-page-numbers">شماره صفحه</label>
            </div>
            <div className="settings__check">
              <input
                id="export-page-border"
                type="checkbox"
                checked={options.page_border}
                onChange={(event) => update("page_border", event.target.checked)}
              />
              <label htmlFor="export-page-border">کادر دور صفحه</label>
            </div>
          </div>

          <p className="settings__note">
            فونت‌های وزیرمتن و ساحل باید روی رایانهٔ شما نصب باشند تا در Word درست
            نمایش داده شوند.
          </p>

          <div className="dialog__actions">
            <button ref={confirmRef} type="submit" className="button">
              {EXPORT_CONFIRM_LABEL}
            </button>
            <button
              type="button"
              className="button button--secondary"
              onClick={onDismiss}
            >
              {EXPORT_CANCEL_LABEL}
            </button>
          </div>
        </form>

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

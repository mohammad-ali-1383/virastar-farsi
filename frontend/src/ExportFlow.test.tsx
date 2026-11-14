/**
 * Tests for the Word export flow (`context/word-export-spec.md`).
 *
 * The real `App` and settings dialog are used with a stubbed `fetch`, so what is
 * verified is exactly what the browser would send: the corrected text and the
 * selected options, the `.docx` download, and that dismissing the dialog or a
 * failed export never downloads anything.
 */

import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import App from "./App";
import { STYLE_PRESERVE_LABEL } from "./StyleDialog";
import { DEFAULT_EXPORT_OPTIONS, EXPORT_FILENAME, type ExportOptions } from "./api";
import { EXPORT_CONFIRM_LABEL, EXPORT_DIALOG_TITLE } from "./ExportSettingsDialog";

const USER_TEXT = "سلام این یک متن ازمایشی است";
const CORRECTED_TEXT = "سلام، این یک متن آزمایشی است.";
const EXPORT_URL_SUFFIX = "/api/v1/export-docx";
const EXPORT_BUTTON_LABEL = "دریافت فایل ورد";
const CLOSE_LABEL = "بستن";

interface ExportReply {
  ok: boolean;
  status: number;
  blob?: () => Promise<Blob>;
  json?: () => Promise<unknown>;
}

let fetchMock: ReturnType<typeof vi.fn>;
let clickSpy: ReturnType<typeof vi.spyOn>;
const createObjectURL = vi.fn(() => "blob:mock-file");
const revokeObjectURL = vi.fn();

/** Route correction requests to a success and export requests to `reply`. */
function stubApi(
  reply: ExportReply = {
    ok: true,
    status: 200,
    blob: async () => new Blob(["PK"], { type: "application/octet-stream" }),
  },
): void {
  fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    if (String(input).endsWith(EXPORT_URL_SUFFIX)) {
      return reply as unknown as Response;
    }
    return {
      ok: true,
      status: 200,
      json: async () => ({ corrected_text: CORRECTED_TEXT }),
    } as unknown as Response;
  });
  vi.stubGlobal("fetch", fetchMock);
}

type User = ReturnType<typeof userEvent.setup>;

async function showCorrectedResult(user: User): Promise<void> {
  await user.type(screen.getByLabelText("متن شما"), USER_TEXT);
  await user.click(screen.getByRole("button", { name: "اصلاح متن" }));
  await user.click(
    screen.getByRole("button", { name: new RegExp(STYLE_PRESERVE_LABEL) }),
  );
  await screen.findByText(CORRECTED_TEXT);
}

async function openExportSettings(user: User): Promise<void> {
  await user.click(screen.getByRole("button", { name: EXPORT_BUTTON_LABEL }));
}

/** The export request the app sent, if any. */
function exportCall(): [string, RequestInit] | undefined {
  return fetchMock.mock.calls.find(([input]) =>
    String(input).endsWith(EXPORT_URL_SUFFIX),
  ) as [string, RequestInit] | undefined;
}

function exportBody(): { text: string; options: ExportOptions } {
  const call = exportCall();
  expect(call).toBeDefined();
  return JSON.parse(String(call?.[1].body)) as {
    text: string;
    options: ExportOptions;
  };
}

function anchorElement(): HTMLAnchorElement {
  return clickSpy.mock.instances[0] as unknown as HTMLAnchorElement;
}

describe("Word export flow", () => {
  beforeEach(() => {
    stubApi();
    Object.defineProperty(window.URL, "createObjectURL", {
      value: createObjectURL,
      configurable: true,
    });
    Object.defineProperty(window.URL, "revokeObjectURL", {
      value: revokeObjectURL,
      configurable: true,
    });
    clickSpy = vi
      .spyOn(HTMLAnchorElement.prototype, "click")
      .mockImplementation(() => {});
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
    createObjectURL.mockClear();
    revokeObjectURL.mockClear();
    delete (window.URL as unknown as Record<string, unknown>).createObjectURL;
    delete (window.URL as unknown as Record<string, unknown>).revokeObjectURL;
  });

  it("offers the download only after a successful correction", async () => {
    const user = userEvent.setup();
    render(<App />);

    expect(screen.queryByRole("button", { name: EXPORT_BUTTON_LABEL })).toBeNull();

    await showCorrectedResult(user);

    expect(screen.getByRole("button", { name: EXPORT_BUTTON_LABEL })).toBeTruthy();
    // The existing correction result is untouched.
    expect(screen.getByRole("button", { name: "کپی" })).toBeTruthy();
  });

  it("opens with the documented defaults", async () => {
    const user = userEvent.setup();
    render(<App />);
    await showCorrectedResult(user);

    await openExportSettings(user);

    expect(screen.getByRole("dialog", { name: EXPORT_DIALOG_TITLE })).toBeTruthy();
    expect((screen.getByLabelText("فونت") as HTMLSelectElement).value).toBe(
      "Vazirmatn",
    );
    expect((screen.getByLabelText("اندازه قلم") as HTMLSelectElement).value).toBe(
      "14",
    );
    expect((screen.getByLabelText("فاصلهٔ خطوط") as HTMLSelectElement).value).toBe(
      "1.5",
    );
    expect((screen.getByLabelText("تراز متن") as HTMLSelectElement).value).toBe(
      "justify",
    );
    expect((screen.getByLabelText("حاشیهٔ صفحه") as HTMLSelectElement).value).toBe(
      "normal",
    );
    expect((screen.getByLabelText("شماره صفحه") as HTMLInputElement).checked).toBe(
      true,
    );
    expect(
      (screen.getByLabelText("جای شماره صفحه") as HTMLSelectElement).value,
    ).toBe("center");
    expect(
      (screen.getByLabelText("کادر دور صفحه") as HTMLInputElement).checked,
    ).toBe(false);
  });

  it("posts the corrected text and default options, then downloads the file", async () => {
    const user = userEvent.setup();
    render(<App />);
    await showCorrectedResult(user);
    await openExportSettings(user);

    await user.click(screen.getByRole("button", { name: EXPORT_CONFIRM_LABEL }));

    await waitFor(() => expect(exportCall()).toBeDefined());
    const [url, init] = exportCall() as [string, RequestInit];
    expect(String(url).endsWith(EXPORT_URL_SUFFIX)).toBe(true);
    expect(init.method).toBe("POST");
    expect((init.headers as Record<string, string>)["Content-Type"]).toBe(
      "application/json",
    );
    expect(exportBody()).toEqual({
      text: CORRECTED_TEXT,
      options: DEFAULT_EXPORT_OPTIONS,
    });

    // The dialog closes and the browser saves the blob under the spec filename.
    expect(screen.queryByRole("dialog")).toBeNull();
    await waitFor(() => expect(clickSpy).toHaveBeenCalledTimes(1));
    expect(createObjectURL).toHaveBeenCalledTimes(1);
    expect(anchorElement().download).toBe(EXPORT_FILENAME);
    expect(anchorElement().href).toContain("blob:mock-file");
    await waitFor(() =>
      expect(revokeObjectURL).toHaveBeenCalledWith("blob:mock-file"),
    );
    // No second correction request was made.
    expect(
      fetchMock.mock.calls.filter(
        ([input]) => String(input).endsWith("/api/v1/correct"),
      ),
    ).toHaveLength(1);
  });

  it("sends the chosen options", async () => {
    const user = userEvent.setup();
    render(<App />);
    await showCorrectedResult(user);
    await openExportSettings(user);

    await user.selectOptions(screen.getByLabelText("فونت"), "Tahoma");
    await user.selectOptions(screen.getByLabelText("اندازه قلم"), "18");
    await user.selectOptions(screen.getByLabelText("فاصلهٔ خطوط"), "2");
    await user.selectOptions(screen.getByLabelText("تراز متن"), "center");
    await user.selectOptions(screen.getByLabelText("حاشیهٔ صفحه"), "wide");

    // The position is meaningful only while page numbers are enabled.
    await user.click(screen.getByLabelText("شماره صفحه"));
    expect(
      (screen.getByLabelText("جای شماره صفحه") as HTMLSelectElement).disabled,
    ).toBe(true);
    await user.click(screen.getByLabelText("شماره صفحه"));
    expect(
      (screen.getByLabelText("جای شماره صفحه") as HTMLSelectElement).disabled,
    ).toBe(false);

    await user.selectOptions(screen.getByLabelText("جای شماره صفحه"), "right");
    await user.click(screen.getByLabelText("کادر دور صفحه"));
    await user.click(screen.getByRole("button", { name: EXPORT_CONFIRM_LABEL }));

    await waitFor(() => expect(exportCall()).toBeDefined());
    expect(exportBody()).toEqual({
      text: CORRECTED_TEXT,
      options: {
        font: "Tahoma",
        font_size: 18,
        line_spacing: 2,
        alignment: "center",
        margins: "wide",
        page_numbers: true,
        page_number_position: "right",
        page_border: true,
      },
    });
    await waitFor(() => expect(clickSpy).toHaveBeenCalledTimes(1));
  });

  it("shows the backend message and downloads nothing when export fails", async () => {
    stubApi({
      ok: false,
      status: 400,
      json: async () => ({
        error: "INVALID_INPUT",
        message: "متن ارسالی نامعتبر است.",
      }),
    });

    const user = userEvent.setup();
    render(<App />);
    await showCorrectedResult(user);
    await openExportSettings(user);

    await user.click(screen.getByRole("button", { name: EXPORT_CONFIRM_LABEL }));

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("متن ارسالی نامعتبر است.");
    expect(clickSpy).not.toHaveBeenCalled();
    expect(createObjectURL).not.toHaveBeenCalled();
  });

  it("dismissing with Escape, backdrop, or close sends nothing", async () => {
    const user = userEvent.setup();
    render(<App />);
    await showCorrectedResult(user);

    await openExportSettings(user);
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).toBeNull();

    await openExportSettings(user);
    const backdrop = screen.getByRole("dialog").parentElement;
    expect(backdrop).not.toBeNull();
    await user.click(backdrop as HTMLElement);
    expect(screen.queryByRole("dialog")).toBeNull();

    await openExportSettings(user);
    await user.click(screen.getByRole("button", { name: CLOSE_LABEL }));
    expect(screen.queryByRole("dialog")).toBeNull();

    expect(exportCall()).toBeUndefined();
    expect(clickSpy).not.toHaveBeenCalled();
  });
});

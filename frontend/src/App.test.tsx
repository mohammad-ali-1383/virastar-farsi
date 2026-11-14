/**
 * Tests for the style-selection flow in `App` (`context/ui.md` ->
 * Proofreading style).
 *
 * The dialog is exercised through the real component tree with a stubbed
 * `fetch`, so what is verified is exactly what the browser would send: the
 * chosen `style` and the user's text, and no request at all when the dialog is
 * dismissed.
 */

import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import App from "./App";
import {
  STYLE_DIALOG_TITLE,
  STYLE_FORMAL_LABEL,
  STYLE_PRESERVE_LABEL,
} from "./StyleDialog";

const USER_TEXT = "سلام این یک متن ازمایشی است";
const CORRECTED_TEXT = "سلام، این یک متن آزمایشی است.";
const MODEL_USED = "test-provider-model-v1";
const MODEL_LABEL = `ویرایش‌شده با مدل: ${MODEL_USED}`;
const CLOSE_LABEL = "بستن";
const SUBMIT_LABEL = "اصلاح متن";
const LOADING_LABEL = "در حال اصلاح…";

let fetchMock: ReturnType<typeof vi.fn>;

function stubSuccessfulFetch(): void {
  fetchMock = vi.fn().mockResolvedValue({
    ok: true,
    status: 200,
    json: async () => ({
      corrected_text: CORRECTED_TEXT,
      model_used: MODEL_USED,
      provider_name: "slot-1",
    }),
  });
  vi.stubGlobal("fetch", fetchMock);
}

/** The JSON body of the request the app actually sent. */
function sentBody(): Record<string, unknown> {
  const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
  return JSON.parse(String(init.body)) as Record<string, unknown>;
}

function preserveOption(): HTMLElement {
  return screen.getByRole("button", { name: new RegExp(STYLE_PRESERVE_LABEL) });
}

function formalOption(): HTMLElement {
  return screen.getByRole("button", { name: new RegExp(STYLE_FORMAL_LABEL) });
}

async function submitText(user: ReturnType<typeof userEvent.setup>): Promise<void> {
  await user.type(screen.getByLabelText("متن شما"), USER_TEXT);
  await user.click(screen.getByRole("button", { name: SUBMIT_LABEL }));
}

describe("correction style flow", () => {
  beforeEach(stubSuccessfulFetch);
  afterEach(() => {
    // Unmount the tree so each test starts from an empty document (the project
    // does not enable Vitest globals, so Testing Library cannot auto-clean).
    cleanup();
    vi.unstubAllGlobals();
  });

  it("asks for a style before sending any request", async () => {
    const user = userEvent.setup();
    render(<App />);

    await submitText(user);

    expect(screen.getByRole("dialog")).toBeTruthy();
    expect(screen.getByText(STYLE_DIALOG_TITLE)).toBeTruthy();
    expect(preserveOption()).toBeTruthy();
    expect(formalOption()).toBeTruthy();
    // Nothing has left the browser yet.
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("recommends «حفظ لحن» by default and focuses it", async () => {
    const user = userEvent.setup();
    render(<App />);

    await submitText(user);

    expect(document.activeElement).toBe(preserveOption());
  });

  it("sends style preserve when tone preservation is chosen", async () => {
    const user = userEvent.setup();
    render(<App />);

    await submitText(user);
    await user.click(preserveOption());

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(sentBody()).toEqual({ text: USER_TEXT, style: "preserve" });
    expect(await screen.findByText(CORRECTED_TEXT)).toBeTruthy();
  });

  it("sends style formal when the administrative tone is chosen", async () => {
    const user = userEvent.setup();
    render(<App />);

    await submitText(user);
    await user.click(formalOption());

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(sentBody()).toEqual({ text: USER_TEXT, style: "formal" });
    expect(await screen.findByText(CORRECTED_TEXT)).toBeTruthy();
  });

  it("shows the model that produced the correction", async () => {
    const user = userEvent.setup();
    render(<App />);

    await submitText(user);
    await user.click(preserveOption());

    expect(await screen.findByText(MODEL_LABEL)).toBeTruthy();
  });

  it("does not show the model label before or during the request", async () => {
    const user = userEvent.setup();
    let resolveFetch: ((value: unknown) => void) | undefined;
    fetchMock.mockReturnValue(
      new Promise((resolve) => {
        resolveFetch = resolve;
      }),
    );
    render(<App />);

    await submitText(user);
    expect(screen.queryByText(MODEL_LABEL)).toBeNull();

    await user.click(preserveOption());
    expect(screen.queryByText(MODEL_LABEL)).toBeNull();

    resolveFetch?.({
      ok: true,
      status: 200,
      json: async () => ({
        corrected_text: CORRECTED_TEXT,
        model_used: MODEL_USED,
        provider_name: "slot-1",
      }),
    });

    expect(await screen.findByText(MODEL_LABEL)).toBeTruthy();
  });

  it("does not send the removed evaluation flag", async () => {
    const user = userEvent.setup();
    render(<App />);

    await submitText(user);
    await user.click(preserveOption());

    expect(sentBody()).not.toHaveProperty("store_for_evaluation");
  });

  it("posts to the correction endpoint with JSON", async () => {
    const user = userEvent.setup();
    render(<App />);

    await submitText(user);
    await user.click(preserveOption());

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(String(url).endsWith("/api/v1/correct")).toBe(true);
    expect(init.method).toBe("POST");
    expect((init.headers as Record<string, string>)["Content-Type"]).toBe("application/json");
  });

  it("sends nothing when the dialog is dismissed with Escape", async () => {
    const user = userEvent.setup();
    render(<App />);

    await submitText(user);
    await user.keyboard("{Escape}");

    expect(screen.queryByRole("dialog")).toBeNull();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("sends nothing when the backdrop is clicked", async () => {
    const user = userEvent.setup();
    render(<App />);

    await submitText(user);
    const backdrop = screen.getByRole("dialog").parentElement;
    expect(backdrop).not.toBeNull();
    await user.click(backdrop as HTMLElement);

    expect(screen.queryByRole("dialog")).toBeNull();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("sends nothing when the close button is used", async () => {
    const user = userEvent.setup();
    render(<App />);

    await submitText(user);
    await user.click(screen.getByRole("button", { name: CLOSE_LABEL }));

    expect(screen.queryByRole("dialog")).toBeNull();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("can be reopened after a dismissal and then sends the chosen style", async () => {
    const user = userEvent.setup();
    render(<App />);

    await submitText(user);
    await user.keyboard("{Escape}");
    await user.click(screen.getByRole("button", { name: SUBMIT_LABEL }));
    await user.click(formalOption());

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(sentBody()).toEqual({ text: USER_TEXT, style: "formal" });
  });

  it("does not ask for a style when there is no text to correct", async () => {
    const user = userEvent.setup();
    render(<App />);

    // The UI blocks an empty submission, so the dialog is never reached (the
    // empty-text guard inside the handler stays as defence in depth).
    const submit = screen.getByRole("button", { name: SUBMIT_LABEL });
    expect((submit as HTMLButtonElement).disabled).toBe(true);
    await user.click(submit);

    expect(screen.queryByRole("dialog")).toBeNull();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("does not ask for a style when the text is too long", async () => {
    const user = userEvent.setup();
    render(<App />);

    // Paste-like entry: typing 1001 characters would be needlessly slow.
    const textarea = screen.getByLabelText("متن شما");
    await user.click(textarea);
    await user.paste("ا".repeat(1001));
    await user.click(screen.getByRole("button", { name: SUBMIT_LABEL }));

    expect(screen.queryByRole("dialog")).toBeNull();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("shows the loading state while the correction is in flight", async () => {
    const user = userEvent.setup();
    let resolveFetch: ((value: unknown) => void) | undefined;
    fetchMock.mockReturnValue(
      new Promise((resolve) => {
        resolveFetch = resolve;
      }),
    );
    render(<App />);

    await submitText(user);
    await user.click(preserveOption());

    const loadingButton = screen.getByRole("button", { name: LOADING_LABEL });
    expect((loadingButton as HTMLButtonElement).disabled).toBe(true);
    expect(
      (screen.getByLabelText("متن شما") as HTMLTextAreaElement).disabled,
    ).toBe(true);

    resolveFetch?.({
      ok: true,
      status: 200,
      json: async () => ({ corrected_text: CORRECTED_TEXT }),
    });

    expect(await screen.findByText(CORRECTED_TEXT)).toBeTruthy();
    expect(screen.getByRole("button", { name: SUBMIT_LABEL })).toBeTruthy();
  });

  it("shows the backend error message when the request fails", async () => {
    const user = userEvent.setup();
    fetchMock.mockResolvedValue({
      ok: false,
      status: 503,
      json: async () => ({ error: "PROVIDER_UNAVAILABLE", message: "سرویس در دسترس نیست." }),
    });
    render(<App />);

    await submitText(user);
    await user.click(preserveOption());

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("سرویس در دسترس نیست.");
    expect(screen.queryByText(/ویرایش‌شده با مدل/)).toBeNull();
  });
});

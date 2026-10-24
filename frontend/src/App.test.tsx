/**
 * Tests for the consent flow in `App` (`context/ui.md` -> Privacy and consent).
 *
 * The dialog is exercised through the real component tree with a stubbed
 * `fetch`, so what is verified is exactly what the browser would send: the
 * explicit `store_for_evaluation` flag for both choices, and no request at all
 * when the dialog is dismissed.
 */

import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import App from "./App";
import { CONSENT_ACCEPT_LABEL, CONSENT_DECLINE_LABEL } from "./ConsentDialog";

const USER_TEXT = "سلام این یک متن ازمایشی است";
const CORRECTED_TEXT = "سلام، این یک متن آزمایشی است.";
const CLOSE_LABEL = "بستن";

let fetchMock: ReturnType<typeof vi.fn>;

function stubSuccessfulFetch(): void {
  fetchMock = vi.fn().mockResolvedValue({
    ok: true,
    status: 200,
    json: async () => ({ corrected_text: CORRECTED_TEXT }),
  });
  vi.stubGlobal("fetch", fetchMock);
}

/** The JSON body of the request the app actually sent. */
function sentBody(): Record<string, unknown> {
  const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
  return JSON.parse(String(init.body)) as Record<string, unknown>;
}

async function submitText(user: ReturnType<typeof userEvent.setup>): Promise<void> {
  await user.type(screen.getByLabelText("متن شما"), USER_TEXT);
  await user.click(screen.getByRole("button", { name: "اصلاح متن" }));
}

describe("correction consent flow", () => {
  beforeEach(stubSuccessfulFetch);
  afterEach(() => {
    // Unmount the tree so each test starts from an empty document (the project
    // does not enable Vitest globals, so Testing Library cannot auto-clean).
    cleanup();
    vi.unstubAllGlobals();
  });

  it("asks for consent before sending any request", async () => {
    const user = userEvent.setup();
    render(<App />);

    await submitText(user);

    expect(screen.getByRole("dialog")).toBeTruthy();
    expect(screen.getByRole("button", { name: CONSENT_ACCEPT_LABEL })).toBeTruthy();
    expect(screen.getByRole("button", { name: CONSENT_DECLINE_LABEL })).toBeTruthy();
    // Nothing has left the browser yet.
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("sends the flag as true when storing is chosen", async () => {
    const user = userEvent.setup();
    render(<App />);

    await submitText(user);
    await user.click(screen.getByRole("button", { name: CONSENT_ACCEPT_LABEL }));

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(sentBody()).toEqual({ text: USER_TEXT, store_for_evaluation: true });
    expect(await screen.findByText(CORRECTED_TEXT)).toBeTruthy();
  });

  it("sends the flag as false when correction only is chosen", async () => {
    const user = userEvent.setup();
    render(<App />);

    await submitText(user);
    await user.click(screen.getByRole("button", { name: CONSENT_DECLINE_LABEL }));

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(sentBody()).toEqual({ text: USER_TEXT, store_for_evaluation: false });
    // The correction still happens for the declined choice.
    expect(await screen.findByText(CORRECTED_TEXT)).toBeTruthy();
  });

  it("posts to the correction endpoint with JSON", async () => {
    const user = userEvent.setup();
    render(<App />);

    await submitText(user);
    await user.click(screen.getByRole("button", { name: CONSENT_ACCEPT_LABEL }));

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

  it("can be reopened after a dismissal and then sends the chosen flag", async () => {
    const user = userEvent.setup();
    render(<App />);

    await submitText(user);
    await user.keyboard("{Escape}");
    await user.click(screen.getByRole("button", { name: "اصلاح متن" }));
    await user.click(screen.getByRole("button", { name: CONSENT_DECLINE_LABEL }));

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(sentBody()).toEqual({ text: USER_TEXT, store_for_evaluation: false });
  });

  it("does not ask for consent when there is no text to correct", async () => {
    const user = userEvent.setup();
    render(<App />);

    // The UI blocks an empty submission, so the dialog is never reached (the
    // empty-text guard inside the handler stays as defence in depth).
    const submit = screen.getByRole("button", { name: "اصلاح متن" });
    expect((submit as HTMLButtonElement).disabled).toBe(true);
    await user.click(submit);

    expect(screen.queryByRole("dialog")).toBeNull();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("does not ask for consent when the text is too long", async () => {
    const user = userEvent.setup();
    render(<App />);

    // Paste-like entry: typing 1001 characters would be needlessly slow.
    const textarea = screen.getByLabelText("متن شما");
    await user.click(textarea);
    await user.paste("ا".repeat(1001));
    await user.click(screen.getByRole("button", { name: "اصلاح متن" }));

    expect(screen.queryByRole("dialog")).toBeNull();
    expect(fetchMock).not.toHaveBeenCalled();
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
    await user.click(screen.getByRole("button", { name: CONSENT_ACCEPT_LABEL }));

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("سرویس در دسترس نیست.");
  });
});

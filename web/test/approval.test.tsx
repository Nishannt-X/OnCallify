import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApprovalPanel } from "../src/components/ApprovalPanel";
import { StatusChip } from "../src/components/bits";
import type { Proposal } from "../src/types";

afterEach(cleanup);

const proposal = (over: Partial<Proposal> = {}): Proposal => ({
  id: "prop-1",
  action: "restart_app",
  params: { app: "ledgerline" },
  params_hash: "5f285dbb" + "0".repeat(56),
  fingerprint: "5f285dbb",
  blast_radius: "ledgerline is unavailable for a few seconds",
  rationale: "connections are not returned",
  dry_run: { ok: true, code: "ok", detail: "would reload /ledgerline" },
  created_at: new Date().toISOString(),
  expires_at: new Date(Date.now() + 600_000).toISOString(),
  status: "open",
  ...over,
});

describe("ApprovalPanel", () => {
  it("keeps Approve disabled until the exact fingerprint is typed", () => {
    const onApprove = vi.fn().mockResolvedValue(undefined);
    render(<ApprovalPanel proposal={proposal()} hasToken onApprove={onApprove} onReject={vi.fn()} />);
    const button = screen.getByRole("button", { name: /approve and run/i });
    const input = screen.getByLabelText(/to approve/i);
    expect((button as HTMLButtonElement).disabled).toBe(true);
    fireEvent.change(input, { target: { value: "yes do it" } });
    expect((button as HTMLButtonElement).disabled).toBe(true);
    fireEvent.change(input, { target: { value: "5F285DBB" } });
    expect((button as HTMLButtonElement).disabled).toBe(false);
    fireEvent.click(button);
    expect(onApprove).toHaveBeenCalledWith("5F285DBB");
  });

  it("cannot approve without an approver token", () => {
    render(<ApprovalPanel proposal={proposal()} hasToken={false} onApprove={vi.fn()} onReject={vi.fn()} />);
    fireEvent.change(screen.getByLabelText(/to approve/i), { target: { value: "5f285dbb" } });
    expect((screen.getByRole("button", { name: /approve and run/i }) as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByText(/approver token/i)).toBeTruthy();
  });

  it("refuses an expired proposal and warns when the alert cleared on its own", () => {
    render(
      <ApprovalPanel
        proposal={proposal({ expires_at: new Date(Date.now() - 1000).toISOString() })}
        hasToken
        onApprove={vi.fn()}
        onReject={vi.fn()}
        alertResolvedMeanwhile
      />,
    );
    fireEvent.change(screen.getByLabelText(/to approve/i), { target: { value: "5f285dbb" } });
    expect((screen.getByRole("button", { name: /approve and run/i }) as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByText(/expired/i)).toBeTruthy();
    expect(screen.getByText(/resolved on its own/i)).toBeTruthy();
  });

  it("requires a reason to reject", () => {
    const onReject = vi.fn().mockResolvedValue(undefined);
    render(<ApprovalPanel proposal={proposal()} hasToken onApprove={vi.fn()} onReject={onReject} />);
    const reject = screen.getByRole("button", { name: /reject/i });
    expect((reject as HTMLButtonElement).disabled).toBe(true);
    fireEvent.change(screen.getByLabelText(/reason to reject/i), { target: { value: "wrong diagnosis" } });
    fireEvent.click(reject);
    expect(onReject).toHaveBeenCalledWith("wrong diagnosis");
  });
});

describe("StatusChip", () => {
  it("labels states a human cares about", () => {
    render(<StatusChip status="awaiting_approval" />);
    expect(screen.getByText("needs approval").className).toContain("amber");
  });
});

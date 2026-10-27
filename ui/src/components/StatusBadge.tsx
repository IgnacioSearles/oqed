import type { ProofStatus } from "../events";

/** Proof status as a boxed label. Color is never the only signal: each state
 * has its own symbol and text. */
export function StatusBadge({
  status,
  reason,
  short = false,
}: {
  status: ProofStatus | null | undefined;
  reason?: string | null;
  short?: boolean;
}) {
  const base = "inline-block border px-1.5 leading-5 whitespace-nowrap";
  if (status === "proven") {
    return (
      <span
        className={`${base} border-good bg-good-bg text-good`}
        title="Symbolic execution (angr) + Z3: equal to the compiler's output for every ABI-valid input"
      >
        ✓ {short ? "proven" : "proven equivalent"}
      </span>
    );
  }
  if (status === "tested_only") {
    return (
      <span
        className={`${base} border-warn bg-warn-bg text-warn`}
        title={reason ? `Not proven: ${reason}` : "Passed differential testing; not proven"}
      >
        ~ {short ? "tested" : "tested, not proven"}
      </span>
    );
  }
  return <span className={`${base} border-rule-soft text-ink-3`}>– no win</span>;
}

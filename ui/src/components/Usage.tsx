import { type FunctionView, type UsageRow, fmtTokens, fmtUsd, modelLabel, usage } from "../model";
import { Panel } from "./Panel";

/** Model calls, tokens and spend for this function's search. */
export function Usage({ fn }: { fn: FunctionView }) {
  const { rows, total } = usage(fn.candidates);
  const unpriced = rows.some((r) => r.cost == null);
  const row = (r: UsageRow, isTotal = false) => (
    <tr key={r.model} className={isTotal ? "border-t border-rule font-bold" : "border-b border-rule-soft"}>
      <td className="px-2.5 py-1 whitespace-nowrap">
        {isTotal ? (
          "total"
        ) : (
          <>
            {r.tier === "reasoning" ? "◇ " : ""}
            {modelLabel(r.model)}
          </>
        )}
      </td>
      <td className="px-2.5 py-1 text-right">{r.calls}</td>
      <td className="px-2.5 py-1 text-right text-ink-2">{fmtTokens(r.tokensIn)}</td>
      <td className="px-2.5 py-1 text-right text-ink-2">{fmtTokens(r.tokensOut)}</td>
      <td className="px-2.5 py-1 text-right">{fmtUsd(r.cost)}</td>
    </tr>
  );
  return (
    <Panel title="Usage" right="list-price estimate">
      <div className="overflow-x-auto">
        <table className="tabular w-full border-collapse">
          <thead>
            <tr className="border-b border-rule-soft text-left text-ink-3">
              <th className="px-2.5 py-1 font-normal">model</th>
              <th className="px-2.5 py-1 text-right font-normal">calls</th>
              <th className="px-2.5 py-1 text-right font-normal">tokens in</th>
              <th className="px-2.5 py-1 text-right font-normal">out</th>
              <th className="px-2.5 py-1 text-right font-normal">cost</th>
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 && (
              <tr>
                <td colSpan={5} className="px-2.5 py-1 text-ink-3">
                  no model calls yet
                </td>
              </tr>
            )}
            {rows.map((r) => row(r))}
            {rows.length > 1 && row(total, true)}
          </tbody>
        </table>
      </div>
      <p className="border-t border-rule-soft px-2.5 py-1.5 text-ink-3">
        Output includes reasoning tokens. Cached-input discounts are not applied, so costs err high.
        {unpriced && " — means the provider's per-token price is not configured; the total leaves it out."}
      </p>
    </Panel>
  );
}

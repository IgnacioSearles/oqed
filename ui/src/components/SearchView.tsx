import type { FunctionView } from "../model";
import type { Replay } from "../replay";
import { CandidateFeed } from "./CandidateFeed";
import { Funnel } from "./Funnel";
import { Playback } from "./Playback";
import { ProgressChart } from "./ProgressChart";
import { Usage } from "./Usage";

/** The search as it happened: `fn` is the state at the playback cursor,
 * `full` the finished function (fixes chart axes so they don't move). */
export function SearchView({
  fn,
  full,
  replay,
  live,
}: {
  fn: FunctionView;
  full: FunctionView;
  replay: Replay;
  live: boolean;
}) {
  return (
    <div className="space-y-1.5">
      <Playback replay={replay} live={live} />
      <div className="grid items-start gap-1.5 xl:grid-cols-[minmax(0,2fr)_minmax(0,3fr)]">
        <div className="min-w-0 space-y-1.5">
          <Funnel fn={fn} />
          <Usage fn={fn} />
        </div>
        <ProgressChart fn={fn} full={full} />
      </div>
      <CandidateFeed fn={fn} />
    </div>
  );
}

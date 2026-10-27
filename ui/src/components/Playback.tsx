import { type Replay, SPEEDS } from "../replay";
import { Panel } from "./Panel";

const clock = (s: number) => {
  const m = Math.floor(s / 60);
  return `${String(m).padStart(2, "0")}:${String(Math.floor(s % 60)).padStart(2, "0")}`;
};

export function Playback({ replay, live }: { replay: Replay; live: boolean }) {
  const { cursor, start, end, playing, speed } = replay;
  const btn = "border border-rule px-2 leading-6";
  return (
    <Panel
      title="Playback"
      right={
        live ? (
          <span className="text-good">● live</span>
        ) : (
          <span className="tabular">
            {clock(cursor - start)} / {clock(end - start)}
          </span>
        )
      }
    >
      <div className="flex flex-wrap items-center gap-2 px-2.5 py-2">
        <button
          onClick={playing ? replay.pause : replay.play}
          className={`${btn} w-20 bg-accent-bg font-bold text-accent`}
        >
          {playing ? "pause" : replay.atEnd ? "replay" : "play"}
        </button>
        <div className="flex">
          {SPEEDS.map((s) => (
            <button
              key={s}
              onClick={() => replay.setSpeed(s)}
              aria-pressed={speed === s}
              className={`${btn} -ml-px first:ml-0 ${speed === s ? "bg-select font-bold text-ink" : "bg-panel text-ink-3"}`}
            >
              {s}×
            </button>
          ))}
        </div>
        <input
          type="range"
          aria-label="playback position"
          min={start}
          max={end}
          step={0.1}
          value={cursor}
          onChange={(e) => replay.seek(Number(e.target.value))}
          className="min-w-40 flex-1 accent-[var(--accent)]"
        />
      </div>
    </Panel>
  );
}

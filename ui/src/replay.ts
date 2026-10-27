// Replays a run's event stream against its own timestamps.

import { useCallback, useEffect, useRef, useState } from "react";

export const SPEEDS = [1, 4, 16, 64] as const;

export interface Replay {
  /** Absolute timestamp (seconds) of the playback cursor. */
  cursor: number;
  playing: boolean;
  speed: number;
  start: number;
  end: number;
  atEnd: boolean;
  play: () => void;
  pause: () => void;
  seek: (t: number) => void;
  setSpeed: (s: number) => void;
}

/** Playback over [start, end]. The cursor rests at `end` (the full result)
 * until play is pressed; play from the end restarts from `start`. When `end`
 * grows (a live run) and the cursor was at the end, it follows. */
export function useReplay(start: number, end: number): Replay {
  const [cursor, setCursor] = useState(end);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState(16);
  const followRef = useRef(true);
  const lastFrame = useRef<number | null>(null);

  // Reset when the range changes to a different function or run.
  useEffect(() => {
    setCursor(end);
    setPlaying(false);
    followRef.current = true;
  }, [start]);

  useEffect(() => {
    if (followRef.current) setCursor(end);
  }, [end]);

  useEffect(() => {
    if (!playing) return;
    let raf = 0;
    const tick = (now: number) => {
      const dt = lastFrame.current == null ? 0 : (now - lastFrame.current) / 1000;
      lastFrame.current = now;
      setCursor((c) => {
        const next = Math.min(end, c + dt * speed);
        if (next >= end) {
          setPlaying(false);
          followRef.current = true;
        }
        return next;
      });
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => {
      cancelAnimationFrame(raf);
      lastFrame.current = null;
    };
  }, [playing, speed, end]);

  const play = useCallback(() => {
    followRef.current = false;
    setCursor((c) => (c >= end ? start : c));
    setPlaying(true);
  }, [start, end]);

  const pause = useCallback(() => setPlaying(false), []);

  const seek = useCallback(
    (t: number) => {
      const c = Math.max(start, Math.min(end, t));
      followRef.current = c >= end;
      setCursor(c);
    },
    [start, end],
  );

  return { cursor, playing, speed, start, end, atEnd: cursor >= end, play, pause, seek, setSpeed };
}

/** Number of events with timestamp <= t (events are in timestamp order). */
export function countUntil(timestamps: number[], t: number): number {
  let lo = 0;
  let hi = timestamps.length;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (timestamps[mid] <= t) lo = mid + 1;
    else hi = mid;
  }
  return lo;
}

import { useCallback, useRef, useState } from "react";

/** A callback ref and the measured content width of the element it is
 * attached to: measured when the element mounts, then on every resize. */
export function useWidth<T extends HTMLElement>(): [(el: T | null) => void, number, React.RefObject<T | null>] {
  const node = useRef<T | null>(null);
  const observer = useRef<ResizeObserver | null>(null);
  const [w, setW] = useState(0);
  const ref = useCallback((el: T | null) => {
    observer.current?.disconnect();
    node.current = el;
    if (!el) return;
    const cs = getComputedStyle(el);
    setW(el.clientWidth - parseFloat(cs.paddingLeft) - parseFloat(cs.paddingRight));
    observer.current = new ResizeObserver(([e]) => setW(e.contentRect.width));
    observer.current.observe(el);
  }, []);
  return [ref, w, node];
}

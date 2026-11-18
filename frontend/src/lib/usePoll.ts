import { useCallback, useEffect, useRef, useState } from "react";

/** Poll a fetcher on an interval; exposes data, error and a manual refresh. */
export function usePoll<T>(fetcher: () => Promise<T>, intervalMs = 2000, deps: unknown[] = []) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const ref = useRef(fetcher);
  ref.current = fetcher;

  const refresh = useCallback(async () => {
    try {
      setData(await ref.current());
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  useEffect(() => {
    let alive = true;
    const run = async () => { if (alive) await refresh(); };
    run();
    const id = setInterval(run, intervalMs);
    return () => { alive = false; clearInterval(id); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [intervalMs, refresh, ...deps]);

  return { data, error, refresh };
}

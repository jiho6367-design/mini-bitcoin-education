import { useEffect, useRef, useState } from "react";

/** A synchronous guard also catches two clicks before React paints disabled. */
export function useInteraction() {
  const running = useRef(new Set<string>());
  const mounted = useRef(true);
  const [pending, setPending] = useState<string[]>([]);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);
  async function run<T>(label: string, task: () => Promise<T>) {
    if (running.current.has(label)) return;
    running.current.add(label);
    setPending([...running.current]);
    try {
      return await task();
    } finally {
      running.current.delete(label);
      if (mounted.current) setPending([...running.current]);
    }
  }
  return { run, busy: pending.length > 0, pending };
}

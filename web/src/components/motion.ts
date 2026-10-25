import { useEffect, useRef, useState } from "react";

/**
 * Smoothly animates a numerical transition using an ease-out cubic curve.
 */
export function useCountUp(target: number, durationMs = 800): number {
  const [val, setVal] = useState(target);
  const startRef = useRef(target);
  const startTimeRef = useRef<number | null>(null);

  useEffect(() => {
    const prefersReduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (prefersReduced) {
      setVal(target);
      startRef.current = target;
      return;
    }

    const startVal = startRef.current;
    if (startVal === target) return;

    let rafId: number;
    startTimeRef.current = null;

    const step = (ts: number) => {
      if (!startTimeRef.current) startTimeRef.current = ts;
      const elapsed = ts - startTimeRef.current;
      const progress = Math.min(1, elapsed / durationMs);
      // ease-out cubic
      const eased = 1 - Math.pow(1 - progress, 3);
      const current = startVal + (target - startVal) * eased;
      setVal(current);

      if (progress < 1) {
        rafId = requestAnimationFrame(step);
      } else {
        startRef.current = target;
        setVal(target);
      }
    };

    rafId = requestAnimationFrame(step);
    return () => cancelAnimationFrame(rafId);
  }, [target, durationMs]);

  return val;
}

/**
 * Returns true if window has been scrolled past the threshold.
 */
export function useScrolled(threshold = 12): boolean {
  const [scrolled, setScrolled] = useState(false);
  useEffect(() => {
    const onScroll = () => {
      setScrolled(window.scrollY > threshold);
    };
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, [threshold]);
  return scrolled;
}

/**
 * Measures the active nav link and sets CSS custom properties --ind-x and --ind-w.
 */
export function useNavIndicator(containerRef: React.RefObject<HTMLElement | null>, activeKey: string) {
  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    const activeEl = container.querySelector<HTMLElement>(".active");
    if (!activeEl) return;

    const cRect = container.getBoundingClientRect();
    const aRect = activeEl.getBoundingClientRect();

    const x = aRect.left - cRect.left;
    const w = aRect.width;

    container.style.setProperty("--ind-x", `${x}px`);
    container.style.setProperty("--ind-w", `${w}px`);
    container.classList.add("measured");
  }, [containerRef, activeKey]);
}

/**
 * Theme toggle (editorial cream vs obsidian dark).
 */
export function useTheme() {
  const [theme, setTheme] = useState<"light" | "dark">(() => {
    const saved = localStorage.getItem("oncallify-theme");
    if (saved === "light" || saved === "dark") return saved;
    return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  });

  useEffect(() => {
    document.documentElement.setAttribute("data-theme", theme);
    localStorage.setItem("oncallify-theme", theme);
  }, [theme]);

  const toggle = () => setTheme((t) => (t === "light" ? "dark" : "light"));

  return { theme, toggle };
}

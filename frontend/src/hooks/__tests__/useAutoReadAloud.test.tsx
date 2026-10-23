import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, expect, it } from "vitest";
import { useAutoReadAloud } from "../useAutoReadAloud";

const original = Object.getOwnPropertyDescriptor(window, "localStorage");
beforeEach(() => {
  const values = new Map<string, string>();
  Object.defineProperty(window, "localStorage", { configurable: true, value: {
    getItem: (key: string) => values.get(key) ?? null,
    setItem: (key: string, value: string) => values.set(key, value),
  } });
});
afterEach(() => {
  if (original) Object.defineProperty(window, "localStorage", original);
});

it("shares a preference change immediately between composer and bubble instances", () => {
  const first = renderHook(() => useAutoReadAloud());
  const second = renderHook(() => useAutoReadAloud());
  expect(first.result.current.enabled).toBe(false);
  act(() => first.result.current.toggle());
  expect(first.result.current.enabled).toBe(true);
  expect(second.result.current.enabled).toBe(true);
  act(() => second.result.current.toggle());
  expect(first.result.current.enabled).toBe(false);
});

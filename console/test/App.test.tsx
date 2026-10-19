import { renderToString } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { App } from "../src/App";
import { SIMULATION_NOTICE } from "../src/labels";

describe("App", () => {
  it("always shows the synthetic/simulated disclosure", () => {
    const html = renderToString(<App />);
    expect(html).toContain(SIMULATION_NOTICE);
    expect(html).toContain("SafePay Console");
  });
});

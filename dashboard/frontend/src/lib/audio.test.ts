import { describe, expect, it } from "vitest";

import { downsampleBuffer, floatTo16BitPcm } from "./audio";

describe("browser audio helpers", () => {
  it("converts float samples to little-endian pcm16", () => {
    const pcm = floatTo16BitPcm(new Float32Array([-1, 0, 1]));
    const view = new DataView(pcm.buffer);

    expect(view.getInt16(0, true)).toBe(-32768);
    expect(view.getInt16(2, true)).toBe(0);
    expect(view.getInt16(4, true)).toBe(32767);
  });

  it("downsamples by averaging source windows", () => {
    const source = new Float32Array([1, 1, -1, -1]);
    const result = downsampleBuffer(source, 32000, 16000);

    expect(Array.from(result)).toEqual([1, -1]);
  });

  it("returns the original buffer when already at target sample rate", () => {
    const source = new Float32Array([0.25, -0.25]);

    expect(downsampleBuffer(source, 16000, 16000)).toBe(source);
  });
});

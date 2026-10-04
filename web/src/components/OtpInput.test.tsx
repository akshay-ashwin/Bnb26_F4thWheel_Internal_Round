import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { OtpInput } from "./OtpInput";

afterEach(cleanup);

const box = (n: number) => screen.getByLabelText(`Digit ${n}`) as HTMLInputElement;

describe("OtpInput", () => {
  it("submits once on a pasted 6-digit code (spaces ignored)", () => {
    const done = vi.fn();
    render(<OtpInput label="Code" onComplete={done} />);
    fireEvent.paste(box(1), { clipboardData: { getData: () => "123 456" } });
    expect(done).toHaveBeenCalledExactlyOnceWith("123456");
  });

  it("typing over a filled box keeps only the new digit", () => {
    const done = vi.fn();
    render(<OtpInput label="Code" onComplete={done} />);
    fireEvent.change(box(1), { target: { value: "5" } });
    fireEvent.change(box(1), { target: { value: "53" } });
    expect(box(1).value).toBe("3");
    expect(box(2).value).toBe("");
    expect(done).not.toHaveBeenCalled();
  });
});

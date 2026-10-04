import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";

import { Banner, Button, OtpInput } from "./ui";

describe("Button", () => {
  it("disables itself after the first tap until the action settles", async () => {
    let finish: () => void = () => undefined;
    const onClick = vi.fn(() => new Promise<void>((resolve) => (finish = resolve)));
    render(<Button onClick={onClick}>Claim my seat</Button>);
    const button = screen.getByRole("button", { name: "Claim my seat" });

    await userEvent.click(button);
    await userEvent.click(button);
    await userEvent.click(button);

    expect(onClick).toHaveBeenCalledTimes(1);
    expect(button).toBeDisabled();
    expect(button).toHaveAttribute("aria-busy", "true");

    await act(async () => finish());
    expect(button).toBeEnabled();
  });

  it("shows the busy label while busy", () => {
    render(
      <Button busy busyLabel="Confirming…">
        Claim
      </Button>,
    );
    expect(screen.getByRole("button")).toHaveTextContent("Confirming…");
  });
});

describe("Banner", () => {
  it("announces errors as alerts and other messages as polite status", () => {
    render(
      <>
        <Banner tone="danger">Sold out</Banner>
        <Banner tone="warning" live>
          Reconnecting
        </Banner>
      </>,
    );
    expect(screen.getByRole("alert")).toHaveTextContent("Sold out");
    expect(screen.getByRole("status")).toHaveAttribute("aria-live", "polite");
  });
});

describe("OtpInput", () => {
  it("keeps digits only and reports a complete code once", async () => {
    const onComplete = vi.fn();
    const Harness = () => {
      const [value, setValue] = useState("");
      return <OtpInput value={value} onChange={setValue} onComplete={onComplete} />;
    };
    render(<Harness />);
    await userEvent.type(screen.getByLabelText("One-time code"), "12a34-56");
    expect(screen.getByLabelText("One-time code")).toHaveValue("123456");
    expect(onComplete).toHaveBeenCalledExactlyOnceWith("123456");
  });
});

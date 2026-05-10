import { describe, it, expect } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import * as React from "react";
import {
  FluentProvider,
  webLightTheme,
} from "@fluentui/react-components";
import { StackedSection } from "../../src/components/PbiFixer/components/common/StackedSection";

const wrap = (ui: React.ReactElement) =>
  render(<FluentProvider theme={webLightTheme}>{ui}</FluentProvider>);

describe("StackedSection", () => {
  it("renders title and is collapsed by default", () => {
    wrap(
      <StackedSection title="Sales Model">
        <div>BODY-CONTENT</div>
      </StackedSection>,
    );
    expect(screen.getByText("Sales Model")).toBeInTheDocument();
    expect(screen.queryByText("BODY-CONTENT")).not.toBeInTheDocument();
    expect(screen.getByRole("button")).toHaveAttribute("aria-expanded", "false");
  });

  it("renders body when defaultExpanded=true", () => {
    wrap(
      <StackedSection title="Marketing Model" defaultExpanded>
        <div>BODY-CONTENT</div>
      </StackedSection>,
    );
    expect(screen.getByText("BODY-CONTENT")).toBeInTheDocument();
    expect(screen.getByRole("button")).toHaveAttribute("aria-expanded", "true");
  });

  it("toggles open/closed on header click (uncontrolled)", () => {
    wrap(
      <StackedSection title="Finance Model">
        <div>BODY-CONTENT</div>
      </StackedSection>,
    );
    const header = screen.getByRole("button");
    expect(screen.queryByText("BODY-CONTENT")).not.toBeInTheDocument();
    fireEvent.click(header);
    expect(screen.getByText("BODY-CONTENT")).toBeInTheDocument();
    fireEvent.click(header);
    expect(screen.queryByText("BODY-CONTENT")).not.toBeInTheDocument();
  });

  it("toggles via keyboard (Enter and Space)", () => {
    wrap(
      <StackedSection title="HR Model">
        <div>HR-BODY</div>
      </StackedSection>,
    );
    const header = screen.getByRole("button");
    fireEvent.keyDown(header, { key: "Enter" });
    expect(screen.getByText("HR-BODY")).toBeInTheDocument();
    fireEvent.keyDown(header, { key: " " });
    expect(screen.queryByText("HR-BODY")).not.toBeInTheDocument();
  });

  it("respects controlled expanded prop and fires onToggle", () => {
    const calls: boolean[] = [];
    const Holder = () => {
      const [open, setOpen] = React.useState(false);
      return (
        <StackedSection
          title="Controlled"
          expanded={open}
          onToggle={(next) => {
            calls.push(next);
            setOpen(next);
          }}
        >
          <div>CTRL-BODY</div>
        </StackedSection>
      );
    };
    wrap(<Holder />);
    expect(screen.queryByText("CTRL-BODY")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button"));
    expect(calls).toEqual([true]);
    expect(screen.getByText("CTRL-BODY")).toBeInTheDocument();
  });

  it("renders meta and trailing slots; trailing click does not toggle", () => {
    wrap(
      <StackedSection
        title="With Meta"
        defaultExpanded
        meta={<span>(12 issues)</span>}
        trailing={<button>Refresh</button>}
      >
        <div>META-BODY</div>
      </StackedSection>,
    );
    expect(screen.getByText("(12 issues)")).toBeInTheDocument();
    fireEvent.click(screen.getByText("Refresh"));
    expect(screen.getByText("META-BODY")).toBeInTheDocument();
  });
});

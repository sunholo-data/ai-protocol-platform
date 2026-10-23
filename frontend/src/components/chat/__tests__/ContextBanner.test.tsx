import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ContextBanner } from "../ContextBanner";

describe("ContextBanner", () => {
  it("renders folder name and document count", () => {
    render(
      <ContextBanner context={{ folderName: "Q1 Financial Review", docCount: 14 }} />,
    );
    expect(screen.getByText(/analyzing/i)).toBeInTheDocument();
    expect(screen.getByText("14")).toBeInTheDocument();
    expect(screen.getByText("Q1 Financial Review")).toBeInTheDocument();
  });

  it("uses singular 'document' for count of 1", () => {
    render(<ContextBanner context={{ folderName: "Budget", docCount: 1 }} />);
    expect(screen.getByText(/1/)).toBeInTheDocument();
    expect(screen.getByText(/document\b/)).toBeInTheDocument();
    expect(screen.queryByText(/documents/)).toBeNull();
  });

  it("renders nothing when context is null", () => {
    const { container } = render(<ContextBanner context={null} />);
    expect(container.firstChild).toBeNull();
  });

  it("names the project, its document count and instructions, with a settings link", () => {
    render(
      <ContextBanner
        context={null}
        instructionsOn
        project={{ id: "p1", name: "Iberia portfolio review", docCount: 12, instructionsOn: true }}
      />,
    );
    const scope = screen.getByTestId("project-scope");
    expect(scope).toHaveTextContent(/In project\s*Iberia portfolio review\s*·\s*12 documents\s*·\s*instructions on/);
    expect(screen.getByRole("link", { name: "Project settings" })).toHaveAttribute("href", "/projects/p1");
    // The scope line already covers instructions — no second "Your instructions on".
    expect(screen.queryByTestId("instructions-on")).toBeNull();
  });

  it("omits 'instructions on' when neither the user's nor the project's are set", () => {
    render(<ContextBanner context={null} project={{ id: "p1", name: "Dispute", docCount: 1, instructionsOn: false }} />);
    expect(screen.getByTestId("project-scope")).toHaveTextContent(/In project\s*Dispute\s*·\s*1 document\s*·\s*Project settings$/);
    expect(screen.getByTestId("project-scope")).not.toHaveTextContent("instructions on");
  });

  it("keeps 'Your instructions on' when the chat is in no project", () => {
    render(<ContextBanner context={null} instructionsOn project={null} />);
    expect(screen.getByTestId("instructions-on")).toBeInTheDocument();
    expect(screen.queryByTestId("project-scope")).toBeNull();
  });

  it("offers Leave project only when the chat can leave, and leaving calls back", async () => {
    const onLeave = vi.fn();
    const { rerender } = render(<ContextBanner context={null} project={{ id: "p1", name: "Dispute", docCount: 1, instructionsOn: false }} />);
    expect(screen.queryByRole("button", { name: "Leave Dispute" })).toBeNull();
    rerender(
      <ContextBanner context={null} project={{ id: "p1", name: "Dispute", docCount: 1, instructionsOn: false }} onLeaveProject={onLeave} />,
    );
    screen.getByRole("button", { name: "Leave Dispute" }).click();
    expect(onLeave).toHaveBeenCalledTimes(1);
  });

  it("says when the project's documents are still being prepared for search", () => {
    render(
      <ContextBanner context={null} project={{ id: "p1", name: "Dispute", docCount: 2, instructionsOn: false, searchPreparing: true }} />,
    );
    expect(screen.getByTestId("project-scope")).toHaveTextContent("preparing search");
  });
});

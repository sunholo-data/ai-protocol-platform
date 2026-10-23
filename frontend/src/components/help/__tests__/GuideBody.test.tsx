import { render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { GuideBody } from "../GuideBody";
import { HelpGate } from "../HelpGate";

const auth = { user: null as null | { uid: string }, loading: false };
vi.mock("@/contexts/AuthContext", () => ({ useAuth: () => auth }));
vi.mock("@/components/SignInButton", () => ({ SignInButton: () => <button type="button">Sign in</button> }));

const BODY = `## Make a folder searchable

1. Click **Make searchable** in {{appName}}.

> [!WARNING]
> **Your copy is a snapshot.**

![The folder menu](img/folders/12.png "Turning search on.")

## Which folders

| Folder | Searched |
|---|---|
| Yours | Yes |
`;

describe("GuideBody", () => {
  it("renders anchored headings, steps beside the screenshot, alerts and tables", () => {
    render(<GuideBody body={BODY} appName="Acme" />);
    const h = screen.getByRole("heading", { name: "Make a folder searchable" });
    expect(h).toHaveAttribute("id", "make-a-folder-searchable");
    expect(screen.getByText(/in Acme\./)).toBeInTheDocument();
    const img = screen.getByRole("img", { name: "The folder menu" });
    expect(img).toHaveAttribute("src", "/help/img/folders/12.png");
    expect(screen.getByText("Turning search on.")).toBeInTheDocument();
    expect(within(screen.getByRole("note", { name: "Warning" })).getByText("Your copy is a snapshot.")).toBeInTheDocument();
    expect(screen.getByRole("table")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Which folders" })).toHaveAttribute("id", "which-folders");
  });
});

describe("HelpGate", () => {
  it("signed out: the sign-in panel, not the guide", () => {
    auth.user = null;
    render(
      <HelpGate>
        <p>guide content</p>
      </HelpGate>,
    );
    expect(screen.getByText(/sign in to open the user guide/i)).toBeInTheDocument();
    expect(screen.queryByText("guide content")).toBeNull();
  });

  it("signed in: the guide", () => {
    auth.user = { uid: "u1" };
    render(
      <HelpGate>
        <p>guide content</p>
      </HelpGate>,
    );
    expect(screen.getByText("guide content")).toBeInTheDocument();
  });
});

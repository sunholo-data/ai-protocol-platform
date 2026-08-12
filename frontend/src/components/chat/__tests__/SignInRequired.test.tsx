import { render, screen } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import { SignInRequired } from "../SignInRequired";

// SignInButton is non-trivial (auth context); stub it so the test focuses on
// SignInRequired's own rendering contract.
vi.mock("@/components/SignInButton", () => ({
  SignInButton: () => <button data-testid="sign-in-button">Sign in</button>,
}));

describe("SignInRequired", () => {
  it("renders the generic headline when no skillName is provided", () => {
    render(<SignInRequired />);
    expect(screen.getByText(/sign-in required/i)).toBeInTheDocument();
    expect(
      screen.getByText(/you need to sign in to open this chat/i),
    ).toBeInTheDocument();
    expect(screen.getByTestId("sign-in-button")).toBeInTheDocument();
  });

  it("personalises the headline when skillName is provided", () => {
    render(<SignInRequired skillName="Contract Expert" />);
    expect(
      screen.getByText(/you need to sign in to open Contract Expert/i),
    ).toBeInTheDocument();
  });

  it("includes a 'Back to homepage' link pointing at /", () => {
    render(<SignInRequired />);
    const link = screen.getByRole("link", { name: /back to homepage/i });
    expect(link).toHaveAttribute("href", "/");
  });
});

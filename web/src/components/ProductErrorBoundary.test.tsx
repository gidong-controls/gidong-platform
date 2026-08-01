import { render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { ProductErrorBoundary } from "./ProductErrorBoundary";

function BrokenProduct(): never {
  throw new Error("render failed");
}

afterEach(() => {
  vi.restoreAllMocks();
});

it("shows a safe recovery screen instead of a blank window", () => {
  vi.spyOn(console, "error").mockImplementation(() => undefined);

  render(
    <ProductErrorBoundary>
      <BrokenProduct />
    </ProductErrorBoundary>,
  );

  expect(
    screen.getByRole("heading", { name: "화면을 복구해야 합니다" }),
  ).toBeInTheDocument();
  expect(
    screen.getByRole("button", { name: "화면 다시 불러오기" }),
  ).toBeInTheDocument();
  expect(screen.getByText(/자동으로 다시 실행하지 않습니다/)).toBeInTheDocument();
});

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { ApiError } from "../api";
import type { Job } from "../types";
import { Badge, ErrorBox, JobProgress } from "./ui";

afterEach(cleanup);

const job: Job = {
  id: "j",
  document_id: "d",
  kind: "pipeline",
  status: "running",
  params: {},
  stages: ["ingest", "preprocessing", "ocr", "layout", "extraction"],
  completed_stages: ["ingest", "preprocessing"],
  current_stage: "ocr",
  attempts: 1,
  max_attempts: 3,
  cancel_requested: false,
  error_code: null,
  error_message: null,
  submitted_by: "alice",
  created_at: "",
  started_at: null,
  finished_at: null,
  updated_at: "",
};

describe("ui", () => {
  it("shows a status as words, not only colour", () => {
    render(<Badge value="needs_review" />);
    expect(screen.getByText("needs review").className).toContain("warn");
  });

  it("shows job progress by stage", () => {
    render(<JobProgress job={job} />);
    expect(screen.getByText("ocr (2/5)")).toBeTruthy();
    expect(screen.getByRole("progressbar").getAttribute("value")).toBe("2");
  });

  it("shows a failed job's error code", () => {
    render(<JobProgress job={{ ...job, status: "failed", current_stage: null, error_code: "CORRUPT_DOCUMENT" }} />);
    expect(screen.getByText("CORRUPT_DOCUMENT")).toBeTruthy();
  });

  it("shows errors as alerts", () => {
    render(<ErrorBox error={new ApiError(403, "FORBIDDEN", "Requires the admin role")} title="Could not load" />);
    expect(screen.getByRole("alert").textContent).toBe("Could not load: Requires the admin role");
  });

  it("renders document text as text, never as markup", () => {
    render(<Badge value={'<img src=x onerror="alert(1)">'} />);
    expect(document.querySelector("img")).toBeNull();
  });
});

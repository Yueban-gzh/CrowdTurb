import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { chromium } from "playwright";

const team = process.env.CROWDTURB_TEAM_FIXTURES;
const viewer = process.env.CROWDTURB_VIEWER_ROOT;
assert.ok(team && viewer, "Run tools/start_workbench.py --prepare-only and set its environment variables.");

const directory = await mkdtemp(join(tmpdir(), "crowdturb-browser-"));
const server = spawn(process.env.CROWDTURB_PYTHON || "python", [
  "-u", "-m", "apps.api.crowdturb", "--port", "0", "--no-demo",
  "--db", join(directory, "workbench.sqlite3"), "--team-fixtures", team, "--viewer-root", viewer,
], {stdio: ["ignore", "pipe", "pipe"]});
let logs = "";
const stopped = new Promise((resolve) => {
  server.once("close", resolve);
  server.once("error", resolve);
});
server.stderr.on("data", (chunk) => { logs += chunk; });
let browser;

try {
  const address = await new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error("Workbench startup timed out.")), 15000);
    server.stdout.on("data", (chunk) => {
      logs += chunk;
      const address = logs.match(/CrowdTurb system base: (http:\/\/127\.0\.0\.1:\d+)/)?.[1];
      if (address) {
        clearTimeout(timer);
        resolve(address);
      }
    });
    stopped.then((exit) => {
      clearTimeout(timer);
      reject(new Error(`Workbench stopped before it was ready: ${exit}`));
    });
  });

  browser = await chromium.launch({
    channel: process.env.PLAYWRIGHT_CHANNEL || undefined,
    args: ["--enable-unsafe-swiftshader"],
  });
  const page = await browser.newPage({baseURL: address, viewport: {width: 1366, height: 900}});
  const pageErrors = [];
  page.on("pageerror", (error) => pageErrors.push(error.message));
  await page.goto(address);

  const editor = page.frameLocator("#editor");
  await editor.locator("#mapName").getByText("单层展厅", {exact: true}).waitFor();
  const width = editor.locator('#exitBox [data-f="width"]');
  await width.press("ArrowUp");
  await width.press("Tab");
  const queued = page.waitForResponse((response) =>
    new URL(response.url()).pathname === "/api/workbench/runs" && response.request().method() === "POST");
  await page.getByRole("button", {name: "评估当前布局"}).click();
  const accepted = await queued;
  assert.equal(accepted.status(), 202);
  const {run} = await accepted.json();
  await page.waitForURL(`**/?run=${run.run_id}`);
  await page.reload();
  await page.getByRole("heading", {name: "Mock 评估完成"}).waitFor();
  await page.getByText("场景版本 2", {exact: false}).waitFor();

  const frame = page.frameLocator("#viewer");
  await frame.getByText("✓ 通过", {exact: true}).waitFor();
  await page.getByRole("button", {name: "方案比较"}).click();
  const recommended = page.locator(".candidate.is-recommended");
  await recommended.getByRole("button", {name: "回放此方案"}).click();
  const candidate = await page.locator("#view-choice").inputValue();
  assert.notEqual(candidate, "baseline");
  await frame.getByText("✓ 通过", {exact: true}).waitFor();

  const baselineURL = `/api/workbench/runs/${run.run_id}/views/baseline`;
  const manifest = await (await page.request.get(baselineURL)).json();
  const scene = await (await page.request.get(manifest.artifacts.scene)).json();
  assert.equal(scene.exits[0].width_m, 6.1);
  assert.deepEqual(pageErrors, []);
  console.log("PASS: edit, queue, reload, D comparison, B baseline and candidate playback");
} catch (error) {
  console.error(logs);
  throw error;
} finally {
  await browser?.close();
  server.kill();
  await stopped;
  await rm(directory, {recursive: true, force: true, maxRetries: 4});
}

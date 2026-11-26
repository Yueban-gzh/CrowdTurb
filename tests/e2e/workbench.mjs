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
  await page.route("**/api/workbench/scene", async (route) => {
    const response = await route.fetch();
    const data = await response.json();
    const facility = data.scene.geometry.facilities[0];
    facility.allowed_region = [[5, 3], [12, 3], [12, 7], [5, 7]];
    const parameter = data.scene.variable_parameters.find((item) => item.type === "facility_center_x");
    parameter.allowed_range = [20, 23];
    await route.fulfill({response, json: data});
  });
  await page.goto(address);

  const editor = page.frameLocator("#editor");
  await editor.locator("#mapName").getByText("单层展厅", {exact: true}).waitFor();
  await editor.getByText("F1 横坐标评估范围已按当前移动区更新为 6.50–10.50 米。", {exact: true}).waitFor();
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

  await page.getByRole("button", {name: "方案比较"}).click();
  await recommended.getByRole("button", {name: "同屏对比"}).click();
  const timeline = page.getByRole("slider", {name: "对比时间轴"});
  await page.locator("#paired-scale").waitFor();
  const plans = page.frames().filter((item) => new URL(item.url()).searchParams.get("paired") === "1");
  assert.equal(plans.length, 2);
  const startImages = await Promise.all(plans.map((plan) =>
    plan.locator("#view-2d").evaluate((canvas) => canvas.toDataURL())));
  await timeline.press("End");
  assert.equal(await page.locator("#paired-time").textContent(), "60.00 s");
  const endImages = await Promise.all(plans.map((plan) =>
    plan.locator("#view-2d").evaluate((canvas) => canvas.toDataURL())));
  assert.ok(startImages.every((image, index) => image !== endImages[index]), "both maps must redraw on seek");
  await page.getByRole("button", {name: "返回候选方案"}).click();
  assert.equal(await page.locator(".paired-views iframe").count(), 0);

  const baselineURL = `/api/workbench/runs/${run.run_id}/views/baseline`;
  const saved = await (await page.request.get(`/api/workbench/runs/${run.run_id}`)).json();
  const adjusted = saved.scene.variable_parameters.find((item) => item.type === "facility_center_x");
  assert.deepEqual(adjusted.allowed_range, [6.5, 10.5]);
  const manifest = await (await page.request.get(baselineURL)).json();
  const scene = await (await page.request.get(manifest.artifacts.scene)).json();
  assert.equal(scene.exits[0].width_m, 6.1);

  // Edit a real passage, then verify the saved scene and both geometry producers.
  await page.getByRole("button", {name: "布局编辑"}).click();
  const passage = editor.getByRole("spinbutton", {name: "P1 通道净宽（m）"});
  const beforeMask = await editor.locator("#passageBox").evaluate(() => Array.from(rasterize().walk));
  await passage.fill("6");
  await passage.press("Tab");
  const editedMask = await editor.locator("#passageBox").evaluate(() => Array.from(rasterize().walk));
  assert.notDeepEqual(editedMask, beforeMask, "passage width must change the editor geometry");
  await page.locator("#parameter").selectOption(JSON.stringify({type: "passage_width", target_id: "P1"}));
  const passageQueued = page.waitForResponse((response) =>
    new URL(response.url()).pathname === "/api/workbench/runs" && response.request().method() === "POST");
  await page.getByRole("button", {name: "评估当前布局"}).click();
  const passageResponse = await passageQueued;
  assert.equal(passageResponse.status(), 202);
  const passageRun = (await passageResponse.json()).run;
  await page.waitForURL(`**/?run=${passageRun.run_id}`);
  await page.getByRole("heading", {name: "Mock 评估完成"}).waitFor();
  const passageSaved = await (await page.request.get(`/api/workbench/runs/${passageRun.run_id}`)).json();
  assert.equal(passageSaved.scene.geometry.passages[0].width, 6);
  const passageView = await (await page.request.get(`/api/workbench/runs/${passageRun.run_id}/views/baseline/scene`)).json();
  assert.deepEqual(passageView.walkable.flat().map(Number), editedMask, "Python and browser must use the same passage mask");
  await page.reload();
  await page.getByRole("heading", {name: "Mock 评估完成"}).waitFor();
  await page.getByRole("button", {name: "布局编辑"}).click();
  assert.equal(await editor.getByRole("spinbutton", {name: "P1 通道净宽（m）"}).inputValue(), "6");
  await editor.locator("#fileIn").setInputFiles({
    name: "passage-scene.json", mimeType: "application/json",
    buffer: Buffer.from(JSON.stringify(passageSaved.scene)),
  });
  await editor.getByText("已加载 passage-scene.json，请重新校验。", {exact: true}).waitFor();
  assert.equal(await editor.getByRole("spinbutton", {name: "P1 通道净宽（m）"}).inputValue(), "6");
  assert.deepEqual(pageErrors, []);
  console.log("PASS: exit/passage edit, identical JS/Python masks, queue, reload, D comparison, B playback and synchronized 2D comparison");
} catch (error) {
  console.error(logs);
  throw error;
} finally {
  await browser?.close();
  server.kill();
  await stopped;
  await rm(directory, {recursive: true, force: true, maxRetries: 4});
}

const assert = require("node:assert/strict");
const { after, before, test } = require("node:test");
const { chromium } = require(process.env.OPTIKA_PLAYWRIGHT || "playwright");

const root = "http://127.0.0.1:8017";
let browser;
before(async () => {
  browser = await chromium.launch(process.env.OPTIKA_BROWSER_CHANNEL
    ? { channel: process.env.OPTIKA_BROWSER_CHANNEL } : {});
});
after(async () => { await browser?.close(); });

async function browserContext(t, { admin = false, width = 375 } = {}) {
  const context = await browser.newContext({ viewport: { width, height: 860 } });
  context.setDefaultTimeout(8000);
  if (admin) {
    await context.addCookies([{ name: "optika_customer_session", value: "g.browser-test", url: root }]);
  }
  const errors = [];
  context.on("page", page => page.on("pageerror", error => errors.push(error.message)));
  t.after(async () => {
    await context.close();
    assert.deepEqual(errors, []);
  });
  return context;
}

const timeField = (page, start) => page.locator('[name="preferred_time"][value="' + start + ':00"]');
const row = (page, start) => page.locator("#notebook-slot-" + start.replace(":", ""));

async function selectBooking(page, day, start) {
  await page.goto(root + "/booking");
  await page.locator('[name="service"]').first().check();
  await page.locator('[name="preferred_date"][value="' + day + '"]').check();
  await timeField(page, start).check();
}

async function manualBooking(page, day, start, phone) {
  await page.goto(root + "/admin?day=" + day);
  await page.getByRole("button", { name: "Додати запис на " + start }).click();
  await page.locator('#manual-appointment [name="phone"]').fill(phone);
  await page.getByRole("button", { name: "Зберегти запис на " + start }).click();
  await row(page, start).getByText(phone, { exact: true }).waitFor();
}

test("public pages and the Google entry point at mobile and desktop widths", async t => {
  for (const width of [320, 1440]) {
    const context = await browserContext(t, { width });
    const page = await context.newPage();
    for (const path of ["/", "/catalog", "/booking", "/cabinet"]) {
      assert.equal((await page.goto(root + path)).status(), 200, path);
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth), width, path);
    }
    const oauth = await context.request.get(root + "/auth/google", { maxRedirects: 0 });
    assert.equal(oauth.status(), 307);
    const authorization = new URL(oauth.headers().location);
    assert.equal(authorization.hostname, "accounts.google.com");
    assert.equal(authorization.searchParams.get("code_challenge_method"), "S256");
    assert.equal((await context.request.get(root + "/admin/audit")).status(), 403);
  }
});

test("only administrators can open Telegram linking directly from the mobile cabinet", async t => {
  for (const admin of [false, true]) {
    const context = await browserContext(t, { admin, width: 320 });
    const page = await context.newPage();
    await page.goto(root + "/cabinet");
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth), 320);
    assert.equal(await page.getByRole("button", { name: "Продовжити з Telegram" }).count(), 0);
    if (!admin) {
      assert.equal(await page.locator("[data-telegram-link]").count(), 0);
      assert.equal((await context.request.post(root + "/admin/telegram/link")).status(), 403);
      assert.equal((await context.request.get(root + "/auth/telegram")).status(), 404);
      continue;
    }
    await context.route("https://t.me/**", route => route.fulfill({ body: "Telegram test destination" }));
    const opened = context.waitForEvent("page");
    await page.getByRole("button", { name: "Прив’язати Telegram", exact: true }).click();
    const popup = await opened;
    await popup.waitForURL("https://t.me/**");
    const url = new URL(popup.url());
    assert.equal(url.hostname, "t.me");
    assert.match(url.searchParams.get("start"), /^[\w-]{43}$/);
    assert.equal(page.url(), root + "/cabinet");
    assert.match(await page.locator("[data-telegram-link-status]").textContent(), /Підтвердьте прив’язку в боті/);
    assert.equal(await page.locator("#telegram-finish").count(), 0);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth), 320);
  }
});

test("concurrent admin tabs and a changed schedule refresh the booking form", async t => {
  const admin = await browserContext(t, { admin: true });
  const guest = await browserContext(t);
  const editor = await admin.newPage();
  const diary = await admin.newPage();
  const booking = await guest.newPage();
  const day = "2026-11-19";
  await Promise.all([
    editor.goto(root + "/admin#time-slots"),
    diary.goto(root + "/admin?day=" + day),
    selectBooking(booking, day, "09:30"),
  ]);
  const scope = editor.locator("[data-availability-scope]");
  const doctor = await scope.locator("option").nth(1).getAttribute("value");
  await scope.selectOption(doctor);
  await editor.locator("[data-work-weekday='3']").click();
  await editor.locator("[data-custom-schedule]").check();
  await editor.locator('[data-work-schedule] [name="start_time"]').fill("09:15");
  const saved = editor.waitForResponse(response => response.url().endsWith("/admin/changes"));
  await editor.locator("[data-admin-save]").click();
  assert.equal((await saved).status(), 200);

  await booking.locator('[name="name"]').fill("Browser guest");
  await booking.locator('[name="phone"]').fill("0670000011");
  await booking.getByRole("button", { name: "Надіслати заявку" }).click();
  await booking.getByText("Робочі години змінилися. Виберіть час ще раз.").waitFor();
  await timeField(booking, "09:45").check();
  assert.equal(await timeField(booking, "09:30").count(), 0);
  assert.equal(await booking.locator('[name="phone"]').inputValue(), "0670000011");
  await booking.getByRole("button", { name: "Надіслати заявку" }).click();
  await booking.getByRole("heading", { name: "Заявку прийнято" }).waitFor();
});

test("manual statuses, service-only closure, deletion and activity history", async t => {
  const context = await browserContext(t, { admin: true });
  const page = await context.newPage();
  await manualBooking(page, "2026-11-18", "10:00", "0670000012");
  assert.equal(await row(page, "10:00").getByRole("combobox").inputValue(), "confirmed");
  const future = "2026-11-23";
  await manualBooking(page, future, "10:00", "0670000013");
  assert.equal(await row(page, "10:00").getByRole("combobox").inputValue(), "new");
  await page.getByRole("button", { name: "Закрити час 11:00", exact: true }).click();
  const reopen = page.getByRole("button", { name: "Відкрити час 11:00", exact: true });
  await reopen.waitFor();
  const bookings = await context.newPage();
  await selectBooking(bookings, future, "11:30");
  assert.equal(await timeField(bookings, "11:00").count(), 0);
  await bookings.locator('[name="service"]').nth(1).check();
  await timeField(bookings, "11:00").waitFor();
  await reopen.click();
  await page.getByRole("button", { name: "Закрити час 11:00", exact: true }).waitFor();
  await page.goto(root + "/admin/appointments?day=" + future);
  page.once("dialog", dialog => dialog.accept());
  await page.getByRole("row").filter({ hasText: "0670000013" }).getByRole("button").click();
  await page.getByText("Записів не знайдено").waitFor();
  await page.getByRole("link", { name: "Журнал дій" }).click();
  for (const label of ["Закрито години запису", "Відкрито години запису", "Видалено прийом"]) {
    await page.getByText(label, { exact: true }).waitFor();
  }
  assert.match(await page.locator(".admin-audit-list").textContent(), /owner@example.com/);
  assert.doesNotMatch(await page.locator(".admin-audit-list").textContent(), /0670000013/);
});

test("schedule warning leaves the draft open and offers explicit confirmation", async t => {
  const context = await browserContext(t, { admin: true });
  const page = await context.newPage();
  await manualBooking(page, "2026-11-24", "10:00", "0670000014");
  await page.goto(root + "/admin#time-slots");
  const scope = page.locator("[data-availability-scope]");
  const doctor = await scope.locator("option").nth(1).getAttribute("value");
  await scope.selectOption(doctor);
  await page.locator("[data-work-weekday='1']").click();
  await page.locator("[data-custom-schedule]").check();
  await page.locator('[data-work-schedule] [name="start_time"]').fill("11:00");
  const save = page.getByRole("button", { name: "Зберегти зміни" });
  const review = page.getByRole("dialog", { name: "Прийоми поза новим графіком" });
  await save.click();
  await review.getByText(/24\.11\.2026 · 10:00/).waitFor();
  await review.getByRole("button", { name: "Повернутися до редагування" }).click();
  assert.equal(await page.locator('[data-work-schedule] [name="start_time"]').inputValue(), "11:00");
  await save.click();
  await review.getByRole("button", { name: "Зберегти графік" }).click();
  await page.getByText("Усі зміни збережено.").waitFor();
  await page.goto(root + "/admin/appointments?day=2026-11-24");
  await page.getByText("0670000014", { exact: true }).waitFor();
});

test("catalog draft publishes a new category and its product together", async t => {
  const context = await browserContext(t, { admin: true, width: 320 });
  const page = await context.newPage();
  await page.goto(root + "/admin#catalog");
  await page.getByText("Створити категорію", { exact: true }).click();
  const category = page.locator('[data-admin-add="create_categories"]');
  await category.getByRole("textbox").fill("Тестові оправи");
  await category.getByRole("button").click();
  await page.getByText("Додати товар", { exact: true }).click();
  const product = page.locator('[data-admin-add="products"]');
  await product.locator('[name="name"]').fill("Тестова оправа");
  await product.locator('[name="category"]').selectOption({ label: "Тестові оправи" });
  await product.locator('[name="price"]').fill("1500");
  const saved = page.waitForResponse(response => response.url().endsWith("/admin/changes"));
  await page.locator("[data-admin-save]").click();
  assert.equal((await saved).status(), 200);
  await page.getByText("Усі зміни збережено.").waitFor();
  await page.goto(root);
  await page.getByRole("link", { name: "Тестові оправи" }).click();
  await page.getByText("Тестова оправа", { exact: true }).waitFor();
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth), 320);
});

test("rescheduling stays in the notebook and confirms with a free hour", async t => {
  const context = await browserContext(t, { admin: true, width: 320 });
  const page = await context.newPage();
  const day = "2026-11-27";
  const next = "2026-11-28";
  await manualBooking(page, day, "10:00", "0670000015");
  const visit = row(page, "10:00");
  await visit.getByRole("combobox").selectOption("confirmed");
  await visit.getByRole("link", { name: "Перенести" }).click();
  await page.getByText("Перенесення запису", { exact: true }).waitFor();
  await page.getByRole("link", { name: "Наступний день" }).click();
  assert.match(page.url(), new RegExp("day=" + next + "&move="));
  assert.equal(await page.locator("[data-notebook-start]").count(), 0);
  assert.equal(await page.getByRole("dialog").count(), 0);
  await page.getByRole("link", { name: "Скасувати" }).click();
  assert.equal(await page.locator("[data-notebook-move]").count(), 0);
  await page.getByRole("link", { name: "Попередній день" }).click();
  await visit.getByRole("link", { name: "Перенести" }).click();
  await page.getByRole("link", { name: "Наступний день" }).click();
  await page.getByRole("button", { name: "Підтвердити перенесення на 11:00" }).click();
  await row(page, "11:00").getByText("0670000015", { exact: true }).waitFor();
  assert.equal(page.url(), root + "/admin?day=" + next + "#notebook-slot-1100");
  assert.equal(await row(page, "11:00").locator("[data-status]").getAttribute("data-status"), "new");
  assert.equal(await row(page, "11:00").getByRole("combobox").inputValue(), "confirmed");
  await page.getByRole("button", { name: "Зберегти зміни" }).click();
  await row(page, "11:00").locator('[data-status="confirmed"]').waitFor();
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth), 320);
  await page.goto(root + "/admin?day=" + day);
  await page.getByRole("button", { name: "Додати запис на 10:00" }).waitFor();
});

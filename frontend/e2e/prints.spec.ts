import { expect, test } from "@playwright/test";
import { mkdirSync } from "node:fs";

const BASE = process.env.PRINTS_BASE ?? "http://127.0.0.1:8094";
const COOKIE = process.env.PRINTS_COOKIE ?? "rev";
const ROTAS = ["/", "/licitacao/2139", "/precos?q=lego&tipo=item", "/conta", "/entrar"];

test("prints finais 1440 e 375", async ({ browser }) => {
  mkdirSync("../design/entrega", { recursive: true });
  const desktop = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  await desktop.context().addCookies([{
    name: "licitamais_session", value: COOKIE, domain: "127.0.0.1", path: "/",
    httpOnly: true, sameSite: "Lax", secure: false, expires: Math.floor(Date.now() / 1000) + 3600,
  }]);
  await desktop.goto(`${BASE}/`, { waitUntil: "domcontentloaded" });
  await expect(desktop.getByRole("heading", { name: "Oportunidades" })).toBeVisible({ timeout: 15000 });
  for (const rota of ROTAS) {
    const nome = rota.replace(/[^a-z0-9]+/gi, "-").replace(/^-|-$/g, "") || "raiz";
    await desktop.goto(`${BASE}${rota}`, { waitUntil: "domcontentloaded" });
    await desktop.waitForTimeout(1500);
    await desktop.screenshot({ path: `../design/entrega/${nome}-1440.png`, fullPage: true });
  }
  await desktop.close();

  const movel = await browser.newPage({ viewport: { width: 375, height: 667 }, isMobile: true, hasTouch: true });
  await movel.context().addCookies([{
    name: "licitamais_session", value: COOKIE, domain: "127.0.0.1", path: "/",
    httpOnly: true, sameSite: "Lax", secure: false, expires: Math.floor(Date.now() / 1000) + 3600,
  }]);
  await movel.goto(`${BASE}/`, { waitUntil: "domcontentloaded" });
  await expect(movel.getByRole("heading", { name: "Oportunidades" })).toBeVisible({ timeout: 15000 });
  for (const rota of ROTAS) {
    const nome = rota.replace(/[^a-z0-9]+/gi, "-").replace(/^-|-$/g, "") || "raiz";
    await movel.goto(`${BASE}${rota}`, { waitUntil: "domcontentloaded" });
    await movel.waitForTimeout(1500);
    await movel.screenshot({ path: `../design/entrega/${nome}-375.png`, fullPage: true });
  }
  await movel.close();
  expect(true).toBe(true);
});

import { expect, test } from "@playwright/test";
import { execSync } from "node:child_process";

function gerarToken(): string {
  const token = execSync("python scripts/e2e_token.py", { cwd: "..", encoding: "utf-8" }).trim();
  if (!token) throw new Error("token de login não gerado; ver scripts/e2e_token.py");
  return token;
}

test("fluxo: pedir link, entrar, filtrar, acompanhar, conta, sair", async ({ page, baseURL }) => {
  await page.goto(`${baseURL}/entrar`, { waitUntil: "domcontentloaded" });
  await expect(page.getByRole("heading", { name: "Entrar" })).toBeVisible({ timeout: 15000 });
  await page.getByLabel("E-mail").fill("cliente@exemplo.com");
  await page.getByRole("button", { name: "Enviar link de acesso" }).click();
  await expect(page.getByText("Se o e-mail for válido")).toBeVisible();

  const token = gerarToken().replace(/^\/api\/v2\/auth\/entrar\?t=/, "");
  await page.goto(`${baseURL}/api/v2/auth/entrar?t=${token}`);
  await page.getByRole("button", { name: "Confirmar acesso" }).click();
  await expect(page).toHaveURL(/\/$/);
  await expect(page.getByRole("heading", { name: "Oportunidades" })).toBeVisible({ timeout: 15000 });

  await page.getByRole("checkbox", { name: /Aberta/ }).first().click();
  await expect(page).toHaveURL(/situacao=aberta/);
  const primeira = page.locator(".lista .linha").first();
  await expect(primeira).toBeVisible();
  await primeira.click();
  await expect(page).toHaveURL(/\/licitacao\/\d+/);

  const termoCaixa = page.getByRole("button", { name: "Acompanhar" });
  await expect(termoCaixa).toBeVisible({ timeout: 15000 });
  await termoCaixa.click();
  await expect(page.getByRole("button", { name: "Acompanhando" })).toBeVisible();
  await page.goto(`${baseURL}/conta`);
  await expect(page.getByRole("heading", { name: "Conta e alertas" })).toBeVisible();
  await expect(page.locator(".alertas li").first()).toBeVisible();

  await page.getByRole("button", { name: "Sair" }).click();
  await expect(page).toHaveURL(/\/entrar/);
});

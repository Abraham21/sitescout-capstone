// Optional browser check against a running desk with a completed revised preview.
const assert = require('node:assert/strict');
const { chromium } = require('playwright');

async function checkPreview() {
  const browser = await chromium.launch({executablePath: process.env.CHROME_BIN || undefined});
  try {
    const page = await browser.newPage({reducedMotion: 'reduce'});
    await page.goto(process.env.SITESCOUT_DESK_URL || 'http://127.0.0.1:8501');
    await page.getByRole('tab', {name: 'Website preview'}).click();
    const frame = page.frameLocator('iframe[title="Saved business website preview"]')
      .frameLocator('iframe[title="Business website preview"]');
    await frame.getByRole('link', {name: 'Plan an estimate'}).click();
    const notes = frame.getByLabel('About your project');
    const description = 'Paint the hallway trim and two bedrooms after Tuesday.';
    await notes.fill(description);
    await frame.locator('#brief-button').click();
    assert.equal(await notes.inputValue(), description);
    assert.equal(await frame.locator('#brief-result').innerText(), description);
    assert.ok(await frame.locator('#scope').evaluate(el => el.getBoundingClientRect().top < innerHeight));
    console.log('The saved preview stays inside the desk and preserves the project description.');
  } finally {
    await browser.close();
  }
}
checkPreview().catch(error => { console.error(error); process.exitCode = 1; });

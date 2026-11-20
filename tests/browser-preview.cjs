// With no argument, check the preview embedded in the running desk.
// Pass a hosted preview URL to also check its standalone layout at common widths.
const assert = require('node:assert/strict');
const { chromium } = require('playwright');

async function checkHeadline(page) {
  for (const width of [320, 390, 768, 1024, 1280, 1440, 1531, 1920]) {
    await page.setViewportSize({width, height: 900});
    const result = await page.locator('h1').evaluate(async heading => {
      await document.fonts.ready;
      const walk = document.createTreeWalker(heading, NodeFilter.SHOW_TEXT);
      const broken = [];
      while (walk.nextNode()) {
        const node = walk.currentNode;
        for (const match of node.textContent.matchAll(/\S+/g)) {
          const range = document.createRange();
          range.setStart(node, match.index);
          range.setEnd(node, match.index + match[0].length);
          const lines = new Set([...range.getClientRects()].map(rect => Math.round(rect.top)));
          if (lines.size > 1) broken.push(match[0]);
        }
      }
      return {broken, overflow: document.documentElement.scrollWidth > innerWidth};
    });
    assert.deepEqual(result, {broken: [], overflow: false}, 'Headline or page overflow at ' + width);
  }
}

async function checkForm(page, site) {
  await site.locator('a[href="#estimate"]').first().click();
  const form = site.locator('#estimate-form');
  const submit = form.getByRole('button', {name: 'Preview request'});
  const confirmation = site.locator('#form-status');
  const sent = [];
  page.on('request', request => {
    if (/^https?:/.test(request.url()) && ['document','fetch','xhr','ping'].includes(request.resourceType()))
      sent.push(request.url());
  });
  await submit.click();
  assert.equal(await form.evaluate(el => el.checkValidity()), false, 'Empty requests must be rejected');
  await form.getByLabel('Name', {exact: true}).fill('Preview User');
  await form.getByLabel('Email', {exact: true}).fill('preview@example.com');
  const project = form.locator('select');
  if (await project.count() && !(await project.inputValue())) await project.selectOption({index: 1});
  await form.getByLabel('About your project').fill('Repaint the bedroom walls and hallway trim.');
  assert.equal(await form.evaluate(el => el.checkValidity()), true, 'Completed request should validate');
  await submit.click();
  await confirmation.filter({hasText: /preview|demo/i}).waitFor({state: 'visible'});
  assert.deepEqual(sent, [], 'The preview must keep contact details inside this browser');
}

async function main() {
  const browser = await chromium.launch({executablePath: process.env.CHROME_BIN || undefined});
  try {
    const page = await browser.newPage({reducedMotion: 'reduce'});
    const url = process.argv[2];
    let site = page;
    if (url) {
      const response = await page.goto(url);
      assert.equal(response.status(), 200);
      await checkHeadline(page);
    } else {
      await page.goto(process.env.SITESCOUT_DESK_URL || 'http://127.0.0.1:8501');
      await page.getByRole('tab', {name: 'Website preview'}).click();
      site = page.frameLocator('iframe[title="Saved business website preview"]')
        .frameLocator('iframe[title="Business website preview"]');
    }
    await checkForm(page, site);
    console.log(url ? 'Hosted layout and preview request passed.' : 'Embedded preview request passed.');
  } finally {
    await browser.close();
  }
}
main().catch(error => { console.error(error); process.exitCode = 1; });

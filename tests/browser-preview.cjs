// With no argument, check the preview embedded in the running desk.
// Pass a hosted preview URL to also check its standalone layout at common widths.
const assert = require('node:assert/strict');
const { chromium } = require('playwright');

async function checkLayout(page) {
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
    const reviews = page.locator('.review-card');
    const count = await reviews.count();
    if (count) {
      const visible = Math.min(count, width <= 700 ? 1 : 2);
      const fullCards = await reviews.evaluateAll(cards => {
        const rail = cards[0].parentElement.getBoundingClientRect();
        return cards.filter(card => {
          const bounds = card.getBoundingClientRect();
          return bounds.left >= rail.left - 1 && bounds.right <= rail.right + 1;
        }).length;
      });
      assert.equal(fullCards, visible, 'Reviews should fit as whole cards at ' + width);
      await page.locator('#reviews .slide-controls').waitFor({state: count > visible ? 'visible' : 'hidden'});
    }
  }
}

async function checkForm(page, site) {
  await site.locator('a[href="#estimate"]').first().click();
  const form = site.locator('#estimate-form');
  const submit = form.getByRole('button', {name: 'Send request', exact: true});
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
  const project = form.locator('#project-type');
  const services = site.locator('[data-service]');
  const serviceCount = await services.count();
  assert.equal(await project.count(), serviceCount > 1 ? 1 : 0, 'Only offer a meaningful choice');
  if (serviceCount > 1) {
    const choice = services.nth(1);
    await choice.click();
    assert.equal(await project.inputValue(), await choice.getAttribute('data-service'));
  }
  await form.getByLabel('About your project').fill('Repaint the bedroom walls and hallway trim.');
  assert.equal(await form.evaluate(el => el.checkValidity()), true, 'Completed request should validate');
  await submit.click();
  await confirmation.filter({hasText: /preview|demo/i}).waitFor({state: 'visible'});
  assert.match(await confirmation.innerText(), /reply|respond/i);
  assert.match(await confirmation.innerText(), /Abraham/);
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
      await checkLayout(page);
    } else {
      await page.goto(process.env.SITESCOUT_DESK_URL || 'http://127.0.0.1:8501');
      const draft = page.getByRole('link', {name: 'Open draft →'}).last();
      const rehearsal = page.getByRole('link', {name: 'Open saved rehearsal'});
      await draft.or(rehearsal).first().waitFor();
      if (await draft.count()) {
        const documentStart = await page.evaluate(() => performance.timeOrigin);
        await draft.scrollIntoViewIfNeeded();
        const rowTop = (await draft.boundingBox()).y;
        await draft.click();
        const drawer = page.getByRole('dialog');
        await drawer.waitFor();
        const heading = await drawer.locator('#business-detail').boundingBox();
        assert.ok(heading.y >= 0 && heading.y < page.viewportSize().height, 'The selected business should be in view');
        assert.equal(await page.evaluate(() => performance.timeOrigin), documentStart, 'Opening a draft reloaded the page');
        await page.keyboard.press('Escape');
        await drawer.waitFor({state: 'hidden'});
        assert.ok(Math.abs((await draft.boundingBox()).y - rowTop) < 3, 'Closing a draft should preserve the queue position');
        await draft.click();
        await drawer.waitFor();
      } else {
        await rehearsal.click();
      }
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

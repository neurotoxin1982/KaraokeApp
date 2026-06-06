const { chromium } = require('playwright');
(async () => {
  try {
    // Try to connect to running Electron instance
    const browser = await chromium.connectOverCDP('http://localhost:9222');
    console.log('Connected! Pages:', (await browser.contexts()[0].pages()).length);
    await browser.close();
  } catch(e) {
    console.log('CDP connect failed:', e.message);
  }
})();
